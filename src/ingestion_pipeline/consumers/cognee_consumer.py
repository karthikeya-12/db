"""
Event consumer that sends newly ingested documents to Cognee (core/cognee_indexer.py).

Runs downstream of the core pipeline on the `item.ingested` outbox, tracking its own read position
under CONSUMER_NAME, so a slow or failing LLM/Neo4j call can never fail an item that already
ingested successfully. TABULAR items are not indexed (their rows live in Postgres).

A document that keeps failing does not block the queue: each failure is recorded in
dead_letter_items (stage "cognee-index") with an attempt count, and after MAX_ATTEMPTS the event is
acknowledged and left there for a person to requeue (jobs.requeue_dead_letter re-publishes the event).

Usage:  python -m ingestion_pipeline.consumers.cognee_consumer
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ingestion_pipeline.core import events
from ingestion_pipeline.core.blobstore import BlobStore, get_blob_store
from ingestion_pipeline.core.cognee_indexer import CogneeIndexer
from ingestion_pipeline.db.models import DeadLetterItem
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

CONSUMER_NAME = "cognee_indexer"
STAGE = "cognee-index"
MAX_ATTEMPTS = 3
SKIPPED_CONTENT_TYPES = {"TABULAR", "STRUCTURED_RECORD"}


def _record_failure(item_id: str, source_id: str, exc: Exception) -> int:
    """Create or bump this item's open cognee-index dead-letter row; return its attempt count."""
    message = f"{type(exc).__name__}: {exc}"[:2000]
    with session_scope() as session:
        row = session.execute(
            select(DeadLetterItem).where(
                DeadLetterItem.item_id == item_id,
                DeadLetterItem.stage_failed == STAGE,
                DeadLetterItem.requeued_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                DeadLetterItem(item_id=item_id, source_id=source_id, stage_failed=STAGE, error_message=message, attempt_count=1)
            )
            return 1
        row.attempt_count += 1
        row.error_message = message
        row.failed_at = datetime.now(timezone.utc)
        return row.attempt_count


def run_once(batch_size: int = 20, indexer: CogneeIndexer | None = None, blob_store: BlobStore | None = None) -> dict:
    """Process one batch of item.ingested events. Returns counters."""
    indexer = indexer or CogneeIndexer()
    blob_store = blob_store or get_blob_store()

    with session_scope() as session:
        batch = events.poll(session, CONSUMER_NAME, events.TOPIC_ITEM_INGESTED, batch_size=batch_size)

    counters = {"events_seen": len(batch), "indexed": 0, "skipped": 0, "failed": 0}
    last_done: int | None = None

    for event in batch:
        payload = event.payload
        item_id, source_id = payload.get("item_id"), payload.get("source_id")

        if payload.get("content_type") in SKIPPED_CONTENT_TYPES or not payload.get("normalized_ref"):
            counters["skipped"] += 1
            last_done = event.event_id
            continue

        try:
            text = blob_store.get(payload["normalized_ref"]).decode("utf-8")
            indexer.index_document(
                item_id=item_id,
                source_id=source_id,
                text=text,
                title=payload.get("file_name"),
                metadata={"version": payload.get("version"), "file_name": payload.get("file_name")},
            )
        except Exception as exc:  # noqa: BLE001 -- one bad document must not kill the consumer
            attempts = _record_failure(item_id, source_id, exc)
            logger.error("cognee indexing failed item_id=%s attempt=%d err=%s", item_id, attempts, exc)
            if attempts < MAX_ATTEMPTS:
                break  # keep this event unacknowledged so the next run retries it
            counters["failed"] += 1  # out of attempts: dead-lettered, move on
            last_done = event.event_id
            continue

        counters["indexed"] += 1
        last_done = event.event_id
        with session_scope() as session:  # acknowledge each document, so progress is visible and a crash loses at most one
            events.acknowledge(session, CONSUMER_NAME, events.TOPIC_ITEM_INGESTED, last_done)

    if last_done is not None:
        with session_scope() as session:
            events.acknowledge(session, CONSUMER_NAME, events.TOPIC_ITEM_INGESTED, last_done)

    logger.info("run_summary consumer=%s %s", CONSUMER_NAME, counters)
    return counters


def main() -> None:
    from ingestion_pipeline.config import get_settings
    from ingestion_pipeline.core.logging_config import configure_logging

    configure_logging(get_settings().log_level)
    run_once()


if __name__ == "__main__":
    main()
