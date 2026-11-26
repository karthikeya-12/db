"""
Postgres-backed work queue + single worker.

Why this exists: previously a startup thread and a debounced file-watcher timer
could each call orchestrator.run() at the same time, racing on the same files
(non-atomic dedup, blob writes, duplicate events). Now every trigger -- file
activity, an upload, a connector webhook, a manual "sync now", a dead-letter
retry -- only INSERTS a job. One worker drains the queue:

  * jobs are claimed with FOR UPDATE SKIP LOCKED, so even several processes
    can share the queue safely;
  * a source never has two RUNNING jobs at once;
  * a burst of scan triggers collapses into one queued scan per source
    (partial unique index in sql/schema.sql);
  * failed jobs are retried with backoff up to MAX_ATTEMPTS, then marked FAILED;
  * jobs stuck RUNNING after a crash are put back in the queue.

Job kinds:  scan        run the source's orchestrator over list_changes()
            retry_item  re-run one failed item from its archived raw bytes
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import timedelta
from typing import Callable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ingestion_pipeline.db.models import DeadLetterItem, IngestionItem, IngestionJob
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

KIND_SCAN = "scan"
KIND_RETRY_ITEM = "retry_item"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 30
STALE_RUNNING_MINUTES = 30

_CLAIM_SQL = text(
    """
    UPDATE ingestion_jobs SET status = 'RUNNING', started_at = now(), attempts = attempts + 1
    WHERE job_id = (
        SELECT j.job_id FROM ingestion_jobs j
        WHERE j.status = 'QUEUED' AND j.run_after <= now()
          AND NOT EXISTS (
              SELECT 1 FROM ingestion_jobs r WHERE r.status = 'RUNNING' AND r.source_id = j.source_id
          )
        ORDER BY j.created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING job_id, source_id, kind, payload, attempts
    """
)


def enqueue_scan(session, source_id: str) -> str | None:
    """Queue a scan for source_id unless one is already waiting. Returns the new job_id, or None."""
    job_id = uuid.uuid4().hex
    result = session.execute(
        pg_insert(IngestionJob)
        .values(job_id=job_id, source_id=source_id, kind=KIND_SCAN, payload={})
        .on_conflict_do_nothing()  # uq_ingestion_jobs_one_queued_scan
        .returning(IngestionJob.job_id)
    ).first()
    return result[0] if result else None


def enqueue_retry_item(session, source_id: str, item_id: str) -> str:
    job_id = uuid.uuid4().hex
    session.add(IngestionJob(job_id=job_id, source_id=source_id, kind=KIND_RETRY_ITEM, payload={"item_id": item_id}))
    return job_id


def requeue_dead_letter(session, dead_letter_id: str) -> str | None:
    """Retry a dead-lettered item: mark the row requeued, reset the item to NEW, and queue a
    retry_item job that re-runs it from its archived bytes. Returns the job_id.

    A "cognee-index" failure happened AFTER ingestion succeeded, so re-running ingestion would be
    skipped as a duplicate; instead the item.ingested event is re-published for the Cognee consumer
    and None is returned (no job)."""
    from datetime import datetime, timezone

    row = session.get(DeadLetterItem, dead_letter_id)
    if row is None:
        raise KeyError(f"No dead-letter row with id={dead_letter_id!r}")
    row.requeued_at = datetime.now(timezone.utc)
    item = session.get(IngestionItem, row.item_id)

    if row.stage_failed == "cognee-index":
        from ingestion_pipeline.core import events

        if item is None:
            raise KeyError(f"Item {row.item_id!r} for dead-letter {dead_letter_id!r} no longer exists")
        refs = item.storage_ref or {}
        events.publish(
            session,
            events.TOPIC_ITEM_INGESTED,
            item.item_id,
            {
                "source_id": item.source_id,
                "item_id": item.item_id,
                "content_type": item.content_type,
                "storage_ref": {"raw": refs.get("raw")},
                "normalized_ref": refs.get("normalized"),
                "file_name": (item.source_metadata or {}).get("file_name"),
                "version": item.current_version,
                "outcome": "REINDEX",
            },
        )
        return None

    if item is not None:
        item.status = "NEW"
        item.error_reason = None
    return enqueue_retry_item(session, row.source_id, row.item_id)


def claim_next(session) -> dict | None:
    row = session.execute(_CLAIM_SQL).mappings().first()
    return dict(row) if row else None


def finish_job(session, job_id: str, error: str | None, attempts: int) -> None:
    job = session.get(IngestionJob, job_id)
    if job is None:
        return
    if error is None:
        job.status, job.error = "DONE", None
        job.finished_at = text("now()")
    elif attempts < MAX_ATTEMPTS:
        job.status, job.error = "QUEUED", error[:1000]
        job.run_after = text(f"now() + interval '{BACKOFF_SECONDS * attempts} seconds'")
    else:
        job.status, job.error = "FAILED", error[:1000]
        job.finished_at = text("now()")


def recover_stale_running(session, minutes: int = STALE_RUNNING_MINUTES) -> int:
    """Put jobs left RUNNING by a dead process back in the queue. At most one scan per source may be queued
    (unique index), so a stale scan is requeued only if no scan for that source is waiting already; otherwise
    it is closed as superseded. A plain UPDATE would hit that index and recover nothing, leaving the source blocked."""
    stale = session.execute(
        text(
            "SELECT job_id, source_id, kind FROM ingestion_jobs "
            "WHERE status = 'RUNNING' AND started_at < now() - make_interval(mins => :m) ORDER BY started_at DESC"
        ),
        {"m": minutes},
    ).all()
    requeued = 0
    for job_id, source_id, kind in stale:
        waiting = kind == "scan" and session.execute(
            text("SELECT 1 FROM ingestion_jobs WHERE kind = 'scan' AND status = 'QUEUED' AND source_id = :s LIMIT 1"),
            {"s": source_id},
        ).first()
        if waiting:
            session.execute(
                text("UPDATE ingestion_jobs SET status = 'FAILED', error = 'interrupted; superseded by a queued scan', finished_at = now() WHERE job_id = :j"),
                {"j": job_id},
            )
        else:
            session.execute(text("UPDATE ingestion_jobs SET status = 'QUEUED' WHERE job_id = :j"), {"j": job_id})
            requeued += 1
    return requeued


# handler(job dict) -> None; raises on failure
JobHandler = Callable[[dict], None]


class JobWorker:
    """Single background thread that drains the queue. Construct with one handler per job kind."""

    def __init__(self, handlers: dict[str, JobHandler], poll_seconds: float = 2.0) -> None:
        self._handlers = handlers
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """Claim and run at most one job. Returns True if a job was run."""
        with session_scope() as session:
            job = claim_next(session)
        if job is None:
            return False

        error: str | None = None
        try:
            handler = self._handlers.get(job["kind"])
            if handler is None:
                raise ValueError(f"no handler for job kind {job['kind']!r}")
            handler(job)
        except Exception as exc:  # noqa: BLE001 -- a job failure must never kill the worker
            logger.exception("job failed job_id=%s kind=%s", job["job_id"], job["kind"])
            error = f"{type(exc).__name__}: {exc}"

        with session_scope() as session:
            finish_job(session, job["job_id"], error, job["attempts"])
        return True

    def _loop(self) -> None:
        try:
            with session_scope() as session:
                recovered = recover_stale_running(session, minutes=0)  # a fresh process has nothing running: any RUNNING job is stale
            if recovered:
                logger.warning("re-queued %d job(s) left RUNNING by a previous process", recovered)
        except Exception:  # noqa: BLE001
            logger.exception("stale-job recovery failed")

        while not self._stop.is_set():
            try:
                ran = self.run_once()
            except Exception:  # noqa: BLE001 -- e.g. database briefly unavailable
                logger.exception("worker iteration failed")
                ran = False
            if not ran:
                self._stop.wait(self._poll_seconds)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="ingestion-job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
