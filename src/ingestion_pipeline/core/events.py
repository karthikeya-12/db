"""
Event publishing -- the outbox pattern. publish() never references which
consumers exist; each consumer tracks its own read position in
event_consumer_offsets via poll()/acknowledge(), so a slow or failing
consumer never blocks another consumer or the publisher.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ingestion_pipeline.db.models import EventConsumerOffset, IngestionEvent

TOPIC_ITEM_INGESTED = "item.ingested"


def publish(session: Session, topic: str, item_id: str | None, payload: dict) -> int:
    """Insert one event row. Call once persist has succeeded for an item."""
    event = IngestionEvent(topic=topic, item_id=item_id, payload=payload)
    session.add(event)
    session.flush()  # populate event.event_id without committing the outer transaction
    return event.event_id


def poll(session: Session, consumer_name: str, topic: str, batch_size: int = 100) -> list[IngestionEvent]:
    """Return up to batch_size events past this consumer's last acknowledged event_id."""
    offset_row = session.execute(
        select(EventConsumerOffset).where(
            EventConsumerOffset.consumer_name == consumer_name, EventConsumerOffset.topic == topic
        )
    ).scalar_one_or_none()
    last_event_id = offset_row.last_event_id if offset_row else 0

    return list(
        session.execute(
            select(IngestionEvent)
            .where(IngestionEvent.topic == topic, IngestionEvent.event_id > last_event_id)
            .order_by(IngestionEvent.event_id)
            .limit(batch_size)
        ).scalars()
    )


def acknowledge(session: Session, consumer_name: str, topic: str, up_to_event_id: int) -> None:
    """Advance this consumer's offset. Call only after the batch from poll() is fully processed."""
    stmt = (
        pg_insert(EventConsumerOffset)
        .values(consumer_name=consumer_name, topic=topic, last_event_id=up_to_event_id)
        .on_conflict_do_update(index_elements=["consumer_name", "topic"], set_={"last_event_id": up_to_event_id})
    )
    session.execute(stmt)
