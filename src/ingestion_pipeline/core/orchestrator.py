"""
The pipeline orchestrator -- the ONLY place that knows the fixed stage order:

    read checkpoint -> list_changes -> per item:
        fetch -> archive-raw -> dedup-check -> format-normalize ->
        chunk -> embed -> persist -> publish-event -> advance-cursor

Adapters (Adapter subclasses) never see this sequence -- they only implement
list_changes/fetch/advance_cursor. Onboarding a new source means writing a
new Adapter; this file does not change.

A dead-lettered item is a terminal outcome, not a blocked one -- the cursor
advances past it just like a successfully processed item, so one
permanently-broken item can never stall the whole source.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert

from ingestion_pipeline.core import checkpoint, dedup, events
from ingestion_pipeline.core.adapter import Adapter
from ingestion_pipeline.core.blobstore import BlobStore
from ingestion_pipeline.core.chunker import get_chunker
from ingestion_pipeline.core.embedder import Embedder
from ingestion_pipeline.core.metrics import RunMetrics
from ingestion_pipeline.core.normalizer import TabularParser, get_document_normalizer
from ingestion_pipeline.core.retry import call_with_retry, write_dead_letter
from ingestion_pipeline.core.types import ContentType
from ingestion_pipeline.db.models import ChunkEmbedding, IngestionItem, TabularRow
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)


class _DuplicateItem(Exception):
    """Internal control-flow signal raised when the dedup check finds an exact duplicate."""


class PipelineOrchestrator:
    """The only component that knows the pipeline's fixed stage order. See module docstring above."""

    def __init__(self, adapter: Adapter, blob_store: BlobStore, embedder: Embedder) -> None:
        from ingestion_pipeline.config import get_settings

        self.adapter = adapter
        self.blob_store = blob_store
        self.embedder = embedder
        self.tabular_parser = TabularParser()
        self.document_normalizer = get_document_normalizer()
        settings = get_settings()
        self._retry_max_attempts = settings.retry_max_attempts
        self._retry_base_seconds = settings.retry_base_seconds

    def run(self) -> dict:
        source_id = self.adapter.source_id
        metrics = RunMetrics(source_id=source_id)

        with session_scope() as session:
            cursor = checkpoint.get_checkpoint(session, source_id)

        refs = self.adapter.list_changes(cursor)
        metrics.items_identified = len(refs)
        logger.info("list_changes source_id=%s count=%d", source_id, len(refs))

        for ref in refs:
            self._process_one(ref, metrics)

        summary = metrics.finish_and_log()
        with session_scope() as session:
            metrics.persist(session)
        return summary

    def _process_one(self, ref, metrics: RunMetrics) -> None:
        source_id = self.adapter.source_id

        try:
            raw_item = call_with_retry(
                self.adapter.fetch, ref, max_attempts=self._retry_max_attempts, base_seconds=self._retry_base_seconds
            )
        except Exception as exc:
            logger.error("fetch failed source_id=%s err=%s", source_id, exc)
            metrics.items_failed += 1
            return  # nothing was persisted for this ref yet -- safe to leave the cursor where it is

        item_id = raw_item.item_id

        with session_scope() as session:
            self._upsert_status(session, raw_item, "PROCESSING")

        try:
            with session_scope() as session:
                self._archive_and_dedup_check(session, raw_item)
        except _DuplicateItem:
            metrics.items_skipped_duplicate += 1
            self._advance_cursor(ref)
            return
        except Exception as exc:
            self._fail_item(item_id, source_id, "dedup-check", exc, metrics)
            self._advance_cursor(ref)
            return

        try:
            sha256_hash = getattr(raw_item, "_sha256_hash", None)
            with session_scope() as session:
                self._normalize_chunk_embed_persist(session, raw_item)
                events.publish(
                    session,
                    events.TOPIC_ITEM_INGESTED,
                    item_id,
                    {
                        "source_id": source_id,
                        "item_id": item_id,
                        "content_type": raw_item.content_type.value,
                        "storage_ref": getattr(raw_item, "_storage_ref", None),
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self._upsert_status(session, raw_item, "DONE")
                if sha256_hash:
                    dedup.register_hash(session, sha256_hash, item_id, source_id, raw_item.file_name)
        except Exception as exc:
            self._fail_item(item_id, source_id, "normalize-chunk-embed-persist", exc, metrics)
            self._advance_cursor(ref)
            return

        metrics.items_processed += 1
        self._advance_cursor(ref)

    def _upsert_status(self, session, raw_item, status: str) -> None:
        stmt = (
            pg_insert(IngestionItem)
            .values(
                item_id=raw_item.item_id,
                source_id=raw_item.source_id,
                content_type=raw_item.content_type.value,
                status=status,
                source_metadata=raw_item.source_metadata,
            )
            .on_conflict_do_update(index_elements=["item_id"], set_={"status": status})
        )
        session.execute(stmt)

    def _archive_and_dedup_check(self, session, raw_item) -> None:
        raw_bytes = raw_item.raw_bytes or b""
        sha256_hash = dedup.compute_sha256(raw_bytes)
        raw_item._sha256_hash = sha256_hash  # stashed for register_hash after persist succeeds

        outcome = dedup.check_dedup(session, raw_item.item_id, sha256_hash)
        if outcome.value == "DUPLICATE":
            self._upsert_status(session, raw_item, "DUPLICATE")
            raise _DuplicateItem()

        # item_id/file_name land in a filesystem path below -- sanitize anything that's a
        # reserved character on Windows (":" in particular: LocalFolderAdapter's
        # item_id is "local:filename.ext", and ":" is only valid there as a drive-letter
        # separator, which broke archiving with WinError 267 on real runs). Postgres/event
        # payloads keep the original item_id unsanitized -- only the path is affected.
        safe_item_id = re.sub(r'[<>:"/\\|?*]', "_", raw_item.item_id)
        safe_file_name = re.sub(r'[<>:"/\\|?*]', "_", raw_item.file_name or "raw.bin")
        archive_path = f"{raw_item.source_id}/{safe_item_id}/{safe_file_name}"
        storage_ref = self.blob_store.put(archive_path, raw_bytes)
        raw_item._storage_ref = storage_ref  # stashed for the event payload published after persist succeeds
        session.execute(
            pg_insert(IngestionItem)
            .values(
                item_id=raw_item.item_id,
                source_id=raw_item.source_id,
                content_type=raw_item.content_type.value,
                sha256_hash=sha256_hash,
                storage_ref={"raw": storage_ref},
            )
            .on_conflict_do_update(
                index_elements=["item_id"], set_={"sha256_hash": sha256_hash, "storage_ref": {"raw": storage_ref}}
            )
        )

    def _normalize_chunk_embed_persist(self, session, raw_item) -> None:
        content_type = raw_item.content_type

        if content_type == ContentType.TABULAR:
            rows = self.tabular_parser.parse(raw_item.raw_bytes, raw_item.file_name or "")
            for index, row in enumerate(rows):
                session.execute(
                    pg_insert(TabularRow)
                    .values(item_id=raw_item.item_id, row_index=index, row_data=row)
                    .on_conflict_do_nothing(index_elements=["item_id", "row_index"])
                )
            return

        if content_type == ContentType.DOCUMENT:
            text = self.document_normalizer.to_markdown(raw_item.raw_bytes, raw_item.file_name or "")
        else:
            text = (raw_item.raw_bytes or b"").decode("utf-8", errors="replace")

        chunker = get_chunker(content_type)
        chunks = chunker.chunk(raw_item.item_id, text)
        if not chunks:
            return

        vectors = call_with_retry(
            self.embedder.embed_missing,
            session,
            chunks,
            max_attempts=self._retry_max_attempts,
            base_seconds=self._retry_base_seconds,
        )
        for chunk in chunks:
            session.execute(
                pg_insert(ChunkEmbedding)
                .values(
                    chunk_id=chunk.chunk_id,
                    item_id=chunk.item_id,
                    chunk_index=chunk.chunk_index,
                    model=self.embedder.client.model_name,
                    body_text=chunk.text,
                    embedding=vectors.get(chunk.chunk_id),
                )
                .on_conflict_do_nothing(index_elements=["item_id", "chunk_index"])
            )

    def _fail_item(self, item_id: str, source_id: str, stage: str, exc: Exception, metrics: RunMetrics) -> None:
        logger.error("stage=%s failed item_id=%s err=%s", stage, item_id, exc)
        with session_scope() as session:
            write_dead_letter(session, item_id, source_id, stage, str(exc), attempt_count=1)
        metrics.items_failed += 1
        metrics.items_dead_lettered += 1

    def _advance_cursor(self, ref) -> None:
        new_cursor = self.adapter.advance_cursor(ref)
        with session_scope() as session:
            checkpoint.set_checkpoint(session, self.adapter.source_id, str(new_cursor))
