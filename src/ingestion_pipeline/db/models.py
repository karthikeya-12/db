"""
SQLAlchemy ORM models -- the source of truth for schema shape. sql/schema.sql
(repo root) mirrors these tables for the docker-compose init script; if you
change a model here, update sql/schema.sql to match.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ContentType(str, enum.Enum):
    DOCUMENT = "DOCUMENT"
    TABULAR = "TABULAR"
    TRANSCRIPT = "TRANSCRIPT"
    MESSAGE = "MESSAGE"
    STRUCTURED_RECORD = "STRUCTURED_RECORD"


class ItemStatus(str, enum.Enum):
    NEW = "NEW"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"


class IngestionItem(Base):
    __tablename__ = "ingestion_items"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('DOCUMENT','TABULAR','TRANSCRIPT','MESSAGE','STRUCTURED_RECORD')",
            name="ck_ingestion_items_content_type",
        ),
        CheckConstraint(
            "status IN ('NEW','PROCESSING','DONE','FAILED','DUPLICATE')",
            name="ck_ingestion_items_status",
        ),
    )

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    sha256_hash: Mapped[str | None] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=ItemStatus.NEW.value, index=True)
    storage_ref: Mapped[dict | None] = mapped_column(JSONB)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    error_reason: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DedupRegistry(Base):
    __tablename__ = "dedup_registry"

    sha256_hash: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(String, ForeignKey("ingestion_items.item_id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    file_name: Mapped[str | None] = mapped_column(String)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorCheckpoint(Base):
    __tablename__ = "connector_checkpoints"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    cursor_value: Mapped[str | None] = mapped_column(String)
    cursor_meta: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IngestionEvent(Base):
    __tablename__ = "ingestion_events"
    __table_args__ = (UniqueConstraint("event_id"),)

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String, nullable=False, index=True)
    item_id: Mapped[str | None] = mapped_column(String, ForeignKey("ingestion_items.item_id"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class EventConsumerOffset(Base):
    __tablename__ = "event_consumer_offsets"

    consumer_name: Mapped[str] = mapped_column(String, primary_key=True)
    topic: Mapped[str] = mapped_column(String, primary_key=True)
    last_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (UniqueConstraint("item_id", "chunk_index", name="uq_chunk_embeddings_item_chunk"),)

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(String, ForeignKey("ingestion_items.item_id"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    body_text: Mapped[str | None] = mapped_column(String)
    # HALFVEC, not Vector: pgvector's HNSW index caps out at 2000 dims for
    # the plain vector type, and text-embedding-3-large is 3072-dim. halfvec
    # supports HNSW up to 4000 dims (see sql/schema.sql for the matching index).
    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(3072))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TabularRow(Base):
    __tablename__ = "tabular_rows"
    __table_args__ = (UniqueConstraint("item_id", "row_index", name="uq_tabular_rows_item_row"),)

    row_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String, ForeignKey("ingestion_items.item_id"), nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    row_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeadLetterItem(Base):
    __tablename__ = "dead_letter_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    item_id: Mapped[str] = mapped_column(String, ForeignKey("ingestion_items.item_id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    stage_failed: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str] = mapped_column(String, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    requeued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
