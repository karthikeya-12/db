"""
The pipeline orchestrator -- the ONLY place that knows the fixed stage order:

    read checkpoint -> list_changes -> per item:
        fetch -> dedup-check -> archive-raw -> format-normalize -> persist ->
        publish-event -> advance-cursor   (chunk/embed/graph happen downstream in Cognee)

Adapters never see this sequence -- they only implement list_changes/fetch/
advance_cursor. Onboarding a new source means writing a new Adapter; this file
does not change.

Semantics worth knowing:
  * An item is identified by item_id (source + external id). Changed content for
    the same item_id becomes a NEW VERSION that replaces the previous version's
    chunks/rows; identical content is a duplicate and is skipped without touching
    the item's existing status.
  * A dead-lettered item is a terminal outcome -- the cursor advances past it.
  * A FETCH failure persists nothing, so the cursor must not move past it: once
    one fetch fails in a run, the cursor stops advancing for the rest of that run
    (later items are simply re-listed next run and skipped by the duplicate check).
  * retry_item() re-runs a failed item from its archived raw bytes, which is what
    the dead-letter "retry" action uses.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ingestion_pipeline.core import checkpoint, dedup, events
from ingestion_pipeline.core.adapter import Adapter
from ingestion_pipeline.core.blobstore import BlobStore
from ingestion_pipeline.core.metrics import RunMetrics
from ingestion_pipeline.core.normalizer import DocumentNormalizer, TabularParser, get_document_normalizer
from ingestion_pipeline.core.retry import call_with_retry, write_dead_letter
from ingestion_pipeline.core.types import ContentType, RawItem
from ingestion_pipeline.db.models import IngestionItem, TabularRow
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"DONE", "FAILED", "DUPLICATE"}
_UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_path_segment(value: str) -> str:
    """One path segment that is valid on Windows too: no reserved or control characters, no trailing dot/space."""
    return _UNSAFE_PATH_CHARS.sub("_", value).rstrip(" .") or "_"


class PipelineOrchestrator:
    """The only component that knows the pipeline's fixed stage order. See module docstring above."""

    def __init__(
        self,
        adapter: Adapter,
        blob_store: BlobStore,
        document_normalizer: DocumentNormalizer | None = None,
    ) -> None:
        from ingestion_pipeline.config import get_settings

        self.adapter = adapter
        self.blob_store = blob_store
        self.tabular_parser = TabularParser()
        self.document_normalizer = document_normalizer or get_document_normalizer()
        settings = get_settings()
        self._retry_max_attempts = settings.retry_max_attempts
        self._retry_base_seconds = settings.retry_base_seconds

    # ------------------------------------------------------------------ run

    def run(self) -> dict:
        source_id = self.adapter.source_id
        metrics = RunMetrics(source_id=source_id)

        with session_scope() as session:
            cursor = checkpoint.get_checkpoint(session, source_id)

        refs = self.adapter.list_changes(cursor)
        metrics.items_identified = len(refs)
        logger.info("list_changes source_id=%s count=%d", source_id, len(refs))

        cursor_may_advance = True
        for ref in refs:
            cursor_may_advance = self._process_one(ref, metrics, cursor_may_advance)

        summary = metrics.finish_and_log()
        with session_scope() as session:
            metrics.persist(session)
        return summary

    def retry_item(self, item_id: str) -> dict:
        """Re-run one previously failed item from its archived raw bytes."""
        metrics = RunMetrics(source_id=self.adapter.source_id)
        metrics.items_identified = 1

        with session_scope() as session:
            item = session.get(IngestionItem, item_id)
            if item is None:
                raise KeyError(f"Unknown item_id {item_id!r}")
            raw_ref = (item.storage_ref or {}).get("raw")
            meta = dict(item.source_metadata or {})
            content_type = ContentType(item.content_type)
            source_id = item.source_id
        if not raw_ref:
            raise ValueError(f"item_id={item_id!r} has no archived raw bytes to retry from; re-scan the source instead")

        raw_item = RawItem(
            source_id=source_id,
            item_id=item_id,
            content_type=content_type,
            raw_bytes=self.blob_store.get(raw_ref),
            source_metadata=meta,
            file_name=meta.get("file_name"),
        )
        self._ingest(raw_item, metrics, force=True)

        summary = metrics.finish_and_log()
        with session_scope() as session:
            metrics.persist(session)
        return summary

    # ------------------------------------------------------------ per item

    def _process_one(self, ref, metrics: RunMetrics, cursor_may_advance: bool) -> bool:
        """Process one ref. Returns whether the cursor is still allowed to advance for later refs."""
        try:
            raw_item = call_with_retry(
                self.adapter.fetch, ref, max_attempts=self._retry_max_attempts, base_seconds=self._retry_base_seconds
            )
        except Exception as exc:
            logger.error("fetch failed source_id=%s err=%s", self.adapter.source_id, exc)
            metrics.items_failed += 1
            return False  # nothing persisted for this ref -- do not move the cursor past it

        self._ingest(raw_item, metrics)
        if cursor_may_advance:
            self._advance_cursor(ref)
        return cursor_may_advance

    def _ingest(self, raw_item: RawItem, metrics: RunMetrics, force: bool = False) -> None:
        """Everything after fetch: dedup -> archive -> normalize/chunk/embed/persist -> publish.

        force=True (used by retry_item) skips the "already failed with this exact content" check."""
        item_id = raw_item.item_id
        source_id = raw_item.source_id
        raw_bytes = raw_item.raw_bytes or b""
        sha256_hash = dedup.compute_sha256(raw_bytes)

        try:
            with session_scope() as session:
                outcome = dedup.check_dedup(session, item_id, sha256_hash)
        except Exception as exc:
            self._fail_item(raw_item, "dedup-check", exc, metrics)
            return

        if outcome == dedup.DedupOutcome.DUPLICATE:
            # Identical content already ingested for this item: touch nothing, so an existing DONE
            # item never flips back to PROCESSING and a failed-then-fixed item is not masked.
            metrics.items_skipped_duplicate += 1
            return

        if not force:
            # Content that already failed is not re-attempted on every scan (that would pile up
            # dead-letter rows for a permanently bad file). It is retried when the content changes
            # or when someone requeues it (retry_item, force=True).
            with session_scope() as session:
                existing = session.get(IngestionItem, item_id)
                already_failed = (
                    existing is not None and existing.status == "FAILED" and existing.sha256_hash == sha256_hash
                )
            if already_failed:
                logger.info("item_id=%s already failed with this content; skipping until requeued or changed", item_id)
                metrics.items_skipped_duplicate += 1
                return

        with session_scope() as session:
            self._upsert_status(session, raw_item, "PROCESSING")

        try:
            # The hash is in the path, so a changed file gets its own archive object and an
            # unchanged re-archive is an idempotent no-op -- never a BlobAlreadyExistsError.
            safe_source_id = safe_path_segment(source_id)  # connector source ids contain ":" (invalid on Windows)
            safe_item_id = safe_path_segment(item_id)
            safe_file_name = safe_path_segment(raw_item.file_name or "raw.bin")
            archive_path = f"{safe_source_id}/{safe_item_id}/{sha256_hash[:16]}/{safe_file_name}"
            storage_ref = self.blob_store.put(archive_path, raw_bytes)
            with session_scope() as session:
                # Recorded now (before processing) so retry_item() can find the bytes if a later stage fails.
                session.execute(
                    pg_insert(IngestionItem)
                    .values(
                        item_id=item_id,
                        source_id=source_id,
                        content_type=raw_item.content_type.value,
                        storage_ref={"raw": storage_ref},
                    )
                    .on_conflict_do_update(
                        index_elements=["item_id"], set_={"storage_ref": {"raw": storage_ref}, "updated_at": func.now()}
                    )
                )
        except Exception as exc:
            self._fail_item(raw_item, "archive", exc, metrics)
            return

        try:
            with session_scope() as session:
                text = self._normalize_and_persist(session, raw_item)
                normalized_ref = None
                if text is not None:
                    # The normalized Markdown is what the Cognee consumer indexes; archived by content hash.
                    normalized_ref = self.blob_store.put(
                        f"{safe_source_id}/{safe_item_id}/{sha256_hash[:16]}/normalized.md", text.encode("utf-8")
                    )
                    session.execute(
                        update(IngestionItem)
                        .where(IngestionItem.item_id == item_id)
                        .values(storage_ref={"raw": storage_ref, "normalized": normalized_ref})
                    )
                version = dedup.register_version(session, item_id, sha256_hash, storage_ref)
                events.publish(
                    session,
                    events.TOPIC_ITEM_INGESTED,
                    item_id,
                    {
                        "source_id": source_id,
                        "item_id": item_id,
                        "content_type": raw_item.content_type.value,
                        "storage_ref": {"raw": storage_ref},
                        "normalized_ref": normalized_ref,
                        "file_name": raw_item.file_name,
                        "version": version,
                        "outcome": outcome.value,
                        "ingested_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self._upsert_status(session, raw_item, "DONE", sha256_hash=sha256_hash, current_version=version)
        except Exception as exc:
            self._fail_item(raw_item, "normalize-persist", exc, metrics)
            return

        metrics.items_processed += 1

    # -------------------------------------------------------------- stages

    def _upsert_status(
        self,
        session,
        raw_item: RawItem,
        status: str,
        error: str | None = None,
        sha256_hash: str | None = None,
        current_version: int | None = None,
    ) -> None:
        """Insert-or-update the item row. Always bumps updated_at; sets completed_at on terminal
        states (ON CONFLICT DO UPDATE bypasses the ORM's onupdate hooks, so both are explicit)."""
        metadata = dict(raw_item.source_metadata or {})
        if raw_item.file_name:
            metadata["file_name"] = raw_item.file_name

        update_values: dict = {
            "status": status,
            "error_reason": error,
            "source_metadata": metadata,
            "updated_at": func.now(),
            "completed_at": func.now() if status in _TERMINAL_STATUSES else None,
        }
        insert_values: dict = dict(update_values)
        if sha256_hash is not None:
            update_values["sha256_hash"] = insert_values["sha256_hash"] = sha256_hash
        if current_version is not None:
            update_values["current_version"] = insert_values["current_version"] = current_version

        session.execute(
            pg_insert(IngestionItem)
            .values(
                item_id=raw_item.item_id,
                source_id=raw_item.source_id,
                content_type=raw_item.content_type.value,
                **insert_values,
            )
            .on_conflict_do_update(index_elements=["item_id"], set_=update_values)
        )

    def _normalize_and_persist(self, session, raw_item: RawItem) -> str | None:
        """TABULAR: parse rows into Postgres and return None. Everything else: return the normalized text."""
        content_type = raw_item.content_type
        item_id = raw_item.item_id

        if content_type == ContentType.TABULAR:
            rows = self.tabular_parser.parse(raw_item.raw_bytes, raw_item.file_name or "")
            # New version replaces the old rows wholesale.
            session.execute(delete(TabularRow).where(TabularRow.item_id == item_id))
            for index, row in enumerate(rows):
                session.execute(
                    pg_insert(TabularRow)
                    .values(item_id=item_id, row_index=index, row_data=row)
                    .on_conflict_do_nothing(index_elements=["item_id", "row_index"])
                )
            return None

        if content_type == ContentType.DOCUMENT:
            text = self.document_normalizer.to_markdown(raw_item.raw_bytes, raw_item.file_name or "")
        else:
            text = (raw_item.raw_bytes or b"").decode("utf-8", errors="replace")

        return text  # chunking, embedding and graph extraction are Cognee's job (consumers/cognee_consumer.py)

    def _fail_item(self, raw_item: RawItem, stage: str, exc: Exception, metrics: RunMetrics) -> None:
        logger.error("stage=%s failed item_id=%s err=%s", stage, raw_item.item_id, exc)
        with session_scope() as session:
            # Make sure the item row exists (dedup-stage failures happen before the first upsert).
            failed_hash = dedup.compute_sha256(raw_item.raw_bytes or b"")
            self._upsert_status(session, raw_item, "FAILED", error=str(exc)[:500], sha256_hash=failed_hash)
            write_dead_letter(session, raw_item.item_id, raw_item.source_id, stage, str(exc), attempt_count=1)
        metrics.items_failed += 1
        metrics.items_dead_lettered += 1

    def _advance_cursor(self, ref) -> None:
        new_cursor = self.adapter.advance_cursor(ref)
        with session_scope() as session:
            checkpoint.set_checkpoint(session, self.adapter.source_id, str(new_cursor))
