"""
Entity/relationship extraction consumer.

Polls the ingestion pipeline's event outbox (core/events.py) for
item.ingested events, extracts entities/relationships from each item's
chunked text (core/entity_extractor.py), and writes them to Neo4j
(core/graph_store.py). Tracks its own read position in
event_consumer_offsets under CONSUMER_NAME -- entirely independent of any
other consumer, and never touches the core orchestrator.

This is a deliberate architectural choice made when this was scoped: entity
extraction runs here, downstream of ingestion, rather than as a stage
inside orchestrator.py, so a slow/failing LLM call or Neo4j write can never
block or fail the core ingestion path for an item that already persisted
successfully.

Only DOCUMENT/TRANSCRIPT/MESSAGE items have chunked text to extract from --
TABULAR items (content_type in the event payload) are skipped since they
were never chunked/embedded in the first place (see core/chunker.py).

Usage:
    python -m ingestion_pipeline.consumers.entity_relationship_consumer
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from ingestion_pipeline.core import events
from ingestion_pipeline.core.entity_extractor import get_entity_extractor
from ingestion_pipeline.core.graph_store import get_graph_store
from ingestion_pipeline.core.logging_config import configure_logging
from ingestion_pipeline.db.models import ChunkEmbedding
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

CONSUMER_NAME = "entity_relationship"
SKIPPED_CONTENT_TYPES = {"TABULAR", "STRUCTURED_RECORD"}


def _get_item_text(session, item_id: str) -> str:
    """Reassemble an item's full text from its persisted chunks, in order."""
    rows = session.execute(
        select(ChunkEmbedding.body_text)
        .where(ChunkEmbedding.item_id == item_id)
        .order_by(ChunkEmbedding.chunk_index)
    ).scalars()
    return "\n".join(text for text in rows if text)


def run_once(batch_size: int = 50) -> dict:
    """Poll one batch of item.ingested events and process them. Returns a summary dict.

    Processes events in order and stops at the first failure without
    acknowledging past it, so a failed item is retried on the next run
    rather than silently skipped -- successfully processed events earlier
    in the same batch are still acknowledged."""
    extractor = get_entity_extractor()
    graph_store = get_graph_store()
    processed = 0
    skipped = 0
    batch: list = []

    try:
        with session_scope() as session:
            batch = events.poll(session, CONSUMER_NAME, events.TOPIC_ITEM_INGESTED, batch_size=batch_size)

        last_ok_event_id = None
        for event in batch:
            payload = event.payload
            item_id = payload.get("item_id")
            content_type = payload.get("content_type")

            if content_type in SKIPPED_CONTENT_TYPES:
                skipped += 1
                last_ok_event_id = event.event_id
                continue

            try:
                with session_scope() as session:
                    text = _get_item_text(session, item_id)
                if not text.strip():
                    logger.info("item_id=%s has no chunk text, skipping extraction", item_id)
                    skipped += 1
                    last_ok_event_id = event.event_id
                    continue

                extracted = extractor.extract(text)
                findings = extracted.get("_validation_findings") or []
                if findings:
                    logger.info(
                        "item_id=%s extraction had %d finding(s) -- invalid entities/relationships dropped: %s",
                        item_id,
                        len(findings),
                        findings,
                    )
                write_counters = graph_store.write_entities_and_relationships(
                    item_id=item_id,
                    source_id=payload.get("source_id"),
                    entities=extracted["entities"],
                    relationships=extracted["relationships"],
                )
                processed += 1
                last_ok_event_id = event.event_id
                logger.info(
                    "extracted item_id=%s entities=%d relationships=%d nodes_created=%d relationships_created=%d",
                    item_id,
                    len(extracted["entities"]),
                    len(extracted["relationships"]),
                    write_counters["nodes_created"],
                    write_counters["relationships_created"],
                )
            except Exception as exc:
                logger.error("extraction failed item_id=%s err=%s -- stopping batch here", item_id, exc)
                break

        if last_ok_event_id is not None:
            with session_scope() as session:
                events.acknowledge(session, CONSUMER_NAME, events.TOPIC_ITEM_INGESTED, last_ok_event_id)
    finally:
        graph_store.close()

    summary = {"events_seen": len(batch), "processed": processed, "skipped": skipped}
    logger.info("run_summary consumer=%s %s", CONSUMER_NAME, summary)
    return summary


def main() -> None:
    from ingestion_pipeline.config import get_settings

    configure_logging(get_settings().log_level)
    run_once()


if __name__ == "__main__":
    main()
