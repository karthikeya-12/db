"""
SQLAlchemy ORM models -- the source of truth for schema shape. sql/schema.sql
(repo root) mirrors these tables for the docker-compose init script; if you
change a model here, update sql/schema.sql to match.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
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
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class IngestionItemVersion(Base):
    """One ingested version of an item. An item whose LATEST version has exactly the incoming
    content is a duplicate; the same hash may legitimately appear again later (A -> B -> A)."""

    __tablename__ = "ingestion_item_versions"

    item_id: Mapped[str] = mapped_column(String, ForeignKey("ingestion_items.item_id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256_hash: Mapped[str] = mapped_column(String, nullable=False)
    storage_ref: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionJob(Base):
    """Queue row drained by core/jobs.py. The partial unique index that limits a source
    to one queued scan lives in sql/schema.sql only."""

    __tablename__ = "ingestion_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class TabularRow(Base):
    __tablename__ = "tabular_rows"
    __table_args__ = (UniqueConstraint("item_id", "row_index", name="uq_tabular_rows_item_row"),)

    row_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String, ForeignKey("ingestion_items.item_id"), nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    row_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    """One row per orchestrator.run() call -- makes run history queryable per source_id."""

    __tablename__ = "pipeline_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    items_identified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_skipped_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_dead_lettered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(nullable=False)
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


class AppUser(Base):
    __tablename__ = "app_users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    email: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorConnection(Base):
    __tablename__ = "connector_connections"
    __table_args__ = (UniqueConstraint("nango_connection_id", "integration_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    nango_connection_id: Mapped[str] = mapped_column(String, nullable=False)
    integration_key: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("app_users.user_id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JSONB)  # citation cards shown under an assistant answer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
