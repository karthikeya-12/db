"""
Single backend entrypoint: `python -m ingestion_pipeline.api.main`.

Combines the REST API (GET /api/items, /api/items/{item_id}, /api/runs,
/api/graph/items/{item_id}) with automatic document processing (formerly a
separate watcher.py process) into ONE process. Previously this required two
long-running commands started in separate terminals; now one `python -m
ingestion_pipeline.api.main` does both:

  - Serves the REST API the ingestion_pipeline_ui frontend polls. The
    frontend never touches Postgres or Neo4j directly, only this API.
  - On startup (FastAPI's lifespan hook), watches settings.watch_folder_path
    with a watchdog Observer and automatically runs the core ingestion
    pipeline (orchestrator.run()) followed by the entity/relationship
    consumer (consumers.entity_relationship_consumer.run_once()) whenever a
    new file settles there -- debounced, so a single file save/copy's burst
    of filesystem events collapses into one pipeline run. Also runs one
    catch-up pass in a background thread immediately at startup, in case
    files landed in the folder while nothing was running; it's threaded so
    a slow first ingestion run never blocks the API from serving requests.

Nothing here writes to Postgres or Neo4j directly except by calling the
existing orchestrator/consumer, exactly as before -- this module only adds
scheduling (the watcher) and reads (the REST endpoints) on top of code that
already existed.
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ingestion_pipeline.adapters.local_folder_adapter import LocalFolderAdapter
from ingestion_pipeline.adapters.upload_adapter import UploadAdapter
from ingestion_pipeline.api.auth import require_user, router as auth_router
from ingestion_pipeline.api.chat import router as chat_router
from ingestion_pipeline.api.connectors import router as connectors_router, webhook_router
from ingestion_pipeline.adapters.registry import build_orchestrator, register_adapter
from ingestion_pipeline.config import get_settings
from ingestion_pipeline.consumers.cognee_consumer import run_once as run_cognee_consumer_once
from ingestion_pipeline.connectors.scheduler import ConnectorScheduler
from ingestion_pipeline.core import auth as auth_core
from ingestion_pipeline.core import jobs
from ingestion_pipeline.core.jobs import JobWorker
from ingestion_pipeline.core.knowledge_graph import read_document_subgraph
from ingestion_pipeline.core.logging_config import configure_logging
from ingestion_pipeline.db.models import DeadLetterItem, IngestionItem, IngestionJob, PipelineRun
from ingestion_pipeline.db.session import ensure_schema, session_scope

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Triggers and worker -- every trigger only QUEUES a job; one worker drains the queue
# ---------------------------------------------------------------------------

LOCAL_SOURCE_ID = "local_folder"
UPLOAD_SOURCE_ID = "upload"


def _register_adapters() -> None:
    settings = get_settings()
    register_adapter(LOCAL_SOURCE_ID, lambda _source_id: LocalFolderAdapter(settings.watch_folder_path))
    register_adapter(UPLOAD_SOURCE_ID, lambda _source_id: UploadAdapter(settings.upload_folder_path))


def _drain_cognee_consumer(max_rounds: int = 50) -> None:
    """Index everything the ingestion run just published. Indexing problems are recorded per item
    (dead_letter_items) and must never fail the ingestion job that already succeeded."""
    try:
        for _ in range(max_rounds):
            summary = run_cognee_consumer_once()
            handled = summary["indexed"] + summary["skipped"] + summary["failed"]
            if summary["events_seen"] == 0 or handled == 0:
                break  # queue drained, or the head event is failing and will be retried next time
    except Exception:
        logger.exception("cognee indexing run failed")


def _handle_scan(job: dict) -> None:
    source_id = job["source_id"]
    try:
        summary = build_orchestrator(source_id).run()
    except Exception as exc:
        if source_id.startswith("nango:"):
            from ingestion_pipeline.connectors.service import record_sync_failure

            record_sync_failure(source_id, str(exc))
        raise
    logger.info("scan job complete: %s", summary)
    _drain_cognee_consumer()
    if source_id.startswith("nango:"):
        from ingestion_pipeline.connectors.service import record_sync_result

        record_sync_result(source_id, summary)
        # A full batch means there is more waiting: keep going without waiting for the next poll.
        if summary.get("items_identified", 0) >= get_settings().connector_max_items_per_scan and not summary.get("items_failed"):
            _queue_scan(source_id)


def _handle_retry_item(job: dict) -> None:
    summary = build_orchestrator(job["source_id"]).retry_item(job["payload"]["item_id"])
    logger.info("retry job complete: %s", summary)
    _drain_cognee_consumer()


def _queue_scan(source_id: str) -> None:
    with session_scope() as session:
        jobs.enqueue_scan(session, source_id)


class _DebouncedTrigger(FileSystemEventHandler):
    """Collapses a burst of filesystem events into ONE queued scan job, DEBOUNCE_SECONDS after the
    last event settles. It no longer runs the pipeline itself, so it cannot race anything."""

    def __init__(self, source_id: str) -> None:
        self._source_id = source_id
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        logger.info("file activity settled -- queueing scan for %s", self._source_id)
        try:
            _queue_scan(self._source_id)
        except Exception:
            logger.exception("could not queue scan")

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._schedule()

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._schedule()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    ensure_schema()
    auth_core.seed_admin_if_empty()
    _register_adapters()

    worker = JobWorker({jobs.KIND_SCAN: _handle_scan, jobs.KIND_RETRY_ITEM: _handle_retry_item})
    worker.start()
    connector_scheduler = ConnectorScheduler()
    connector_scheduler.start()
    _queue_scan(LOCAL_SOURCE_ID)  # catch-up pass for files that landed while nothing was running
    _queue_scan(UPLOAD_SOURCE_ID)

    from ingestion_pipeline.chat.agent import warm_up_agent

    threading.Thread(target=warm_up_agent, name="chat-warm-up", daemon=True).start()

    observer = Observer()
    observer.schedule(_DebouncedTrigger(LOCAL_SOURCE_ID), settings.watch_folder_path, recursive=False)
    observer.start()
    logger.info("watching %s for new files (debounce=%.1fs)", settings.watch_folder_path, DEBOUNCE_SECONDS)

    yield

    observer.stop()
    observer.join()
    connector_scheduler.stop()
    worker.stop()


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

app = FastAPI(title="ingestion_pipeline API", version="0.3.0", lifespan=lifespan, dependencies=[Depends(require_user)])
app.include_router(auth_router)
app.include_router(connectors_router)
app.include_router(chat_router)
app.include_router(webhook_router)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _settings.api_cors_origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST"],
    allow_credentials=True,
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/items")
def list_items(limit: int = 200) -> list[dict]:
    """Most-recently-updated items first -- what the dashboard's main table renders."""
    with session_scope() as session:
        rows = session.execute(
            select(IngestionItem).order_by(IngestionItem.updated_at.desc()).limit(limit)
        ).scalars().all()
        return [_item_to_dict(r) for r in rows]


@app.get("/api/items/{item_id}")
def get_item(item_id: str) -> dict:
    with session_scope() as session:
        row = session.get(IngestionItem, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        return _item_to_dict(row, include_metadata=True)


@app.get("/api/runs")
def list_runs(limit: int = 100) -> list[dict]:
    """Most-recent pipeline_runs rows first -- one row per orchestrator.run() call,
    whether triggered by the startup catch-up pass or the folder watcher."""
    with session_scope() as session:
        rows = session.execute(
            select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)
        ).scalars().all()
        return [
            {
                "run_id": r.run_id,
                "source_id": r.source_id,
                "items_identified": r.items_identified,
                "items_processed": r.items_processed,
                "items_failed": r.items_failed,
                "items_skipped_duplicate": r.items_skipped_duplicate,
                "items_dead_lettered": r.items_dead_lettered,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "duration_seconds": r.duration_seconds,
            }
            for r in rows
        ]


@app.post("/api/scan")
def trigger_scan(source_id: str = LOCAL_SOURCE_ID) -> dict:
    """Queue a scan ("sync now"). A scan already waiting for this source is reused."""
    with session_scope() as session:
        job_id = jobs.enqueue_scan(session, source_id)
    return {"queued": job_id is not None, "job_id": job_id}


@app.get("/api/uploads/formats")
def upload_formats() -> dict:
    """What the upload dropzone accepts."""
    from ingestion_pipeline.core.formats import SUPPORTED_EXTENSIONS

    return {"extensions": sorted(SUPPORTED_EXTENSIONS), "max_mb": get_settings().upload_max_mb}


@app.post("/api/uploads")
def upload_documents(files: list[UploadFile] = File(...)) -> dict:
    """Accept one or more files. Each is validated and saved independently, then ONE scan of the
    upload source is queued, so a bad file never blocks the good ones. Ingestion runs in the background;
    poll /api/items for progress."""
    from ingestion_pipeline.core.uploads import UploadRejected, save_upload

    settings = get_settings()
    results: list[dict] = []
    for file in files:
        try:
            saved = save_upload(
                settings.upload_folder_path, file.filename, file.file, settings.upload_max_mb * 1024 * 1024
            )
            results.append({"file_name": saved.file_name, "item_id": saved.item_id, "size_bytes": saved.size_bytes, "status": "queued"})
        except UploadRejected as exc:
            results.append({"file_name": file.filename, "status": "rejected", "error": str(exc), "status_code": exc.status_code})
        finally:
            file.file.close()

    if any(r["status"] == "queued" for r in results):
        _queue_scan(UPLOAD_SOURCE_ID)
    if not any(r["status"] == "queued" for r in results):
        raise HTTPException(status_code=results[0].get("status_code", 400) if results else 400, detail=results)
    return {"files": results}


@app.get("/api/jobs")
def list_jobs(limit: int = 100) -> list[dict]:
    with session_scope() as session:
        rows = session.execute(
            select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit)
        ).scalars().all()
        return [
            {
                "job_id": r.job_id,
                "source_id": r.source_id,
                "kind": r.kind,
                "status": r.status,
                "attempts": r.attempts,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]


@app.get("/api/dead-letters")
def list_dead_letters(limit: int = 100) -> list[dict]:
    with session_scope() as session:
        rows = session.execute(
            select(DeadLetterItem).order_by(DeadLetterItem.failed_at.desc()).limit(limit)
        ).scalars().all()
        return [
            {
                "id": r.id,
                "item_id": r.item_id,
                "source_id": r.source_id,
                "stage_failed": r.stage_failed,
                "error_message": r.error_message,
                "attempt_count": r.attempt_count,
                "failed_at": r.failed_at.isoformat() if r.failed_at else None,
                "requeued_at": r.requeued_at.isoformat() if r.requeued_at else None,
            }
            for r in rows
        ]


@app.post("/api/dead-letters/{dead_letter_id}/retry")
def retry_dead_letter(dead_letter_id: str, background: BackgroundTasks) -> dict:
    with session_scope() as session:
        try:
            job_id = jobs.requeue_dead_letter(session, dead_letter_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="dead-letter row not found")
    if job_id is None:
        # A "cognee-index" retry has no ingestion job: the event was re-published, so run the indexer now.
        background.add_task(_drain_cognee_consumer)
    return {"job_id": job_id}


@app.get("/api/graph/items/{item_id}")
def get_item_graph(item_id: str) -> dict:
    """The knowledge-graph neighbourhood of one document (chunks, entities, entity types and the
    relationships between them), read from the Neo4j graph Cognee built. See
    core/knowledge_graph.py for the shape: {"nodes": [...], "edges": [...]}."""
    return read_document_subgraph(item_id)


def _item_to_dict(r: IngestionItem, include_metadata: bool = False) -> dict:
    out = {
        "item_id": r.item_id,
        "source_id": r.source_id,
        "content_type": r.content_type,
        "status": r.status,
        "file_name": (r.source_metadata or {}).get("file_name"),
        "error_reason": r.error_reason,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }
    if include_metadata:
        out["storage_ref"] = r.storage_ref
        out["source_metadata"] = r.source_metadata
    return out


def main() -> None:
    import uvicorn

    uvicorn.run("ingestion_pipeline.api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
