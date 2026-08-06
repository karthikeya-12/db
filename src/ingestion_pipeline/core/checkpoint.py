"""
Per-source watermark, read by Adapter.list_changes and written by the
orchestrator once an item's processing reaches a terminal state (success,
duplicate, or dead-lettered -- all three are terminal, not blocking).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ingestion_pipeline.db.models import ConnectorCheckpoint


def get_checkpoint(session: Session, source_id: str) -> str | None:
    row = session.execute(
        select(ConnectorCheckpoint).where(ConnectorCheckpoint.source_id == source_id)
    ).scalar_one_or_none()
    return row.cursor_value if row else None


def set_checkpoint(session: Session, source_id: str, cursor_value: str, cursor_meta: dict | None = None) -> None:
    stmt = (
        pg_insert(ConnectorCheckpoint)
        .values(source_id=source_id, cursor_value=cursor_value, cursor_meta=cursor_meta)
        .on_conflict_do_update(
            index_elements=["source_id"],
            set_={"cursor_value": cursor_value, "cursor_meta": cursor_meta},
        )
    )
    session.execute(stmt)
