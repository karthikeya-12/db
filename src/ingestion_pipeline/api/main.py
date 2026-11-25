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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ingestion_pipeline.adapters.local_folder_adapter import LocalFolderAdapter
from ingestion_pipeline.config import get_settings
from ingestion_pipeline.consumers.entity_relationship_consumer import run_once as run_entity_consumer_once
from ingestion_pipeline.core.blobstore import get_blob_store
from ingestion_pipeline.core.embedder import get_embedder
from ingestion_pipeline.core.graph_store import get_graph_store
from ingestion_pipeline.core.logging_config import configure_logging
from ingestion_pipeline.core.orchestrator import PipelineOrchestrator
from ingestion_pipeline.db.models import IngestionItem, PipelineRun
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Automatic processing -- formerly watcher.py, now runs inside this process
# ---------------------------------------------------------------------------


def _run_pipeline_once(watch_path: str) -> None:
    adapter = LocalFolderAdapter(watch_path)
    orchestrator = PipelineOrchestrator(adapter=adapter, blob_store=get_blob_store(), embedder=get_embedder())
    try:
        summary = orchestrator.run()
        logger.info("watcher-triggered ingestion run complete: %s", summary)
    except Exception:
        logger.exception("watcher-triggered ingestion run failed")
        return  # don't run the graph consumer against a run that itself failed

    try:
        consumer_summary = run_entity_consumer_once()
        logger.info("watcher-triggered entity/relationship run complete: %s", consumer_summary)
    except Exception:
        logger.exception("watcher-triggered entity/relationship run failed")


class _DebouncedTrigger(FileSystemEventHandler):
    """Collapses a burst of filesystem events into one pipeline run, fired
    DEBOUNCE_SECONDS after the last event settles. Safe to over-trigger --
    orchestrator.run()'s dedup/checkpoint logic already makes an extra
    trigger a cheap no-op, so debouncing is purely to avoid redundant runs,
    not a correctness requirement."""

    def __init__(self, watch_path: str) -> None:
        self._watch_path = watch_path
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
        logger.info("file activity settled -- triggering pipeline run")
        _run_pipeline_once(self._watch_path)

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
    watch_path = settings.watch_folder_path

    threading.Thread(target=_run_pipeline_once, args=(watch_path,), daemon=True).start()

    handler = _DebouncedTrigger(watch_path)
    observer = Observer()
    observer.schedule(handler, watch_path, recursive=False)
    observer.start()
    logger.info("watching %s for new files (debounce=%.1fs)", watch_path, DEBOUNCE_SECONDS)

    yield

    observer.stop()
    observer.join()


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

app = FastAPI(title="ingestion_pipeline API", version="0.2.0", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _settings.api_cors_origins.split(",") if origin.strip()],
    allow_methods=["GET"],
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


@app.get("/api/graph/items/{item_id}")
def get_item_graph(item_id: str) -> dict:
    """Concept/Statement/Chunk subgraph for one item, read live from Neo4j -- see
    core/graph_store.py::read_item_subgraph for the query and node/edge shape."""
    graph_store = get_graph_store()
    try:
        return graph_store.read_item_subgraph(item_id)
    finally:
        graph_store.close()


def _item_to_dict(r: IngestionItem, include_metadata: bool = False) -> dict:
    out = {
        "item_id": r.item_id,
        "source_id": r.source_id,
        "content_type": r.content_type,
        "status": r.status,
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
