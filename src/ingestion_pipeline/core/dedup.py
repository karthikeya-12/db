"""
Content fingerprinting and dedup check -- ported from the reference
pipeline's dedup_checker.py pattern (SHA-256 over raw bytes, registry
lookup, hash registered only after successful processing) onto SQLAlchemy
instead of a raw psycopg2 connection.
"""
from __future__ import annotations

import enum
import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ingestion_pipeline.db.models import DedupRegistry


class DedupOutcome(str, enum.Enum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"
    UPDATE = "UPDATE"


def compute_sha256(raw_bytes: bytes) -> str:
    """Lowercase hex SHA-256 digest of raw_bytes -- stable across repeated runs on the same file."""
    return hashlib.sha256(raw_bytes).hexdigest()


def check_dedup(session: Session, item_id: str, sha256_hash: str) -> DedupOutcome:
    """
    Look up sha256_hash in dedup_registry.

    NEW       -- hash not seen before.
    DUPLICATE -- hash seen, already registered under this same item_id.
    UPDATE    -- hash seen, registered under a different item_id (same content, re-delivered).
    """
    row = session.execute(
        select(DedupRegistry).where(DedupRegistry.sha256_hash == sha256_hash)
    ).scalar_one_or_none()

    if row is None:
        return DedupOutcome.NEW
    if row.item_id == item_id:
        return DedupOutcome.DUPLICATE
    return DedupOutcome.UPDATE


def register_hash(session: Session, sha256_hash: str, item_id: str, source_id: str, file_name: str | None) -> None:
    """
    Insert the fingerprint after the item has been fully processed and persisted.
    A failed run therefore leaves no registry entry -- a retry reprocesses cleanly
    rather than being mistaken for a duplicate. ON CONFLICT DO NOTHING makes a
    retried registration safe.
    """
    stmt = (
        pg_insert(DedupRegistry)
        .values(sha256_hash=sha256_hash, item_id=item_id, source_id=source_id, file_name=file_name)
        .on_conflict_do_nothing(index_elements=["sha256_hash"])
    )
    session.execute(stmt)
