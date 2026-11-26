"""
Content fingerprinting and per-item version tracking.

An item is identified by item_id (source + external id), NOT by its content.
Each time changed content arrives for the same item_id a new version row is
written; identical content for the same item_id is a duplicate and skipped.

    NEW       -- this item has no versions yet (first time we see it).
    UPDATE    -- the item exists but this content is new (a changed file).
    DUPLICATE -- the item's latest version has exactly this content.

The version row is registered only after the item has been fully persisted, in
the same transaction, so a failed run leaves no row and a retry is never
mistaken for a duplicate. (The old global dedup_registry table is no longer
used; it remains in schema.sql only so existing databases keep working.)
"""
from __future__ import annotations

import enum
import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ingestion_pipeline.db.models import IngestionItemVersion


class DedupOutcome(str, enum.Enum):
    NEW = "NEW"
    DUPLICATE = "DUPLICATE"
    UPDATE = "UPDATE"


def compute_sha256(raw_bytes: bytes) -> str:
    """Lowercase hex SHA-256 digest of raw_bytes -- stable across repeated runs on the same file."""
    return hashlib.sha256(raw_bytes).hexdigest()


def check_dedup(session: Session, item_id: str, sha256_hash: str) -> DedupOutcome:
    """Classify incoming content for item_id. See module docstring for the three outcomes."""
    latest = session.execute(
        select(IngestionItemVersion.sha256_hash)
        .where(IngestionItemVersion.item_id == item_id)
        .order_by(IngestionItemVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return DedupOutcome.NEW
    # Only the LATEST version counts: if a file goes A -> B -> A, the second A is a real change
    # (the stored content is B) and must be re-ingested as a new version.
    return DedupOutcome.DUPLICATE if latest == sha256_hash else DedupOutcome.UPDATE


def register_version(session: Session, item_id: str, sha256_hash: str, storage_ref: str | None) -> int:
    """Record a new version for item_id and return its number (1, 2, 3, ...)."""
    latest = session.execute(
        select(func.max(IngestionItemVersion.version)).where(IngestionItemVersion.item_id == item_id)
    ).scalar_one()
    version = (latest or 0) + 1
    session.add(
        IngestionItemVersion(item_id=item_id, version=version, sha256_hash=sha256_hash, storage_ref=storage_ref)
    )
    session.flush()
    return version
