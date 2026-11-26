"""
Integration tests for the ingestion core against a REAL Postgres, started in-process by
`pgserver` (pip install pgserver). Skipped automatically if pgserver is not installed. Embeddings and
document conversion are faked so no network or heavy model is needed.
"""
from __future__ import annotations

import os

import pytest

pgserver = pytest.importorskip("pgserver")

from sqlalchemy import select, text  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _database(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"))
    os.environ["DATABASE_URL"] = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["RETRY_BASE_SECONDS"] = "0.01"

    from ingestion_pipeline.config import get_settings
    import ingestion_pipeline.db.session as session_module

    get_settings.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None
    assert session_module.ensure_schema(), "sql/schema.sql not found"
    yield
    session_module.get_engine().dispose()
    server.cleanup()


@pytest.fixture(autouse=True)
def _clean_tables(_database):
    from ingestion_pipeline.db.session import get_engine

    with get_engine().begin() as connection:
        connection.execute(
            text(
                "TRUNCATE ingestion_jobs, dead_letter_items, ingestion_events, event_consumer_offsets, "
                "tabular_rows, ingestion_item_versions, dedup_registry, "
                "connector_checkpoints, pipeline_runs, ingestion_items CASCADE"
            )
        )


# ----------------------------------------------------------------------------- fakes

from ingestion_pipeline.core.adapter import Adapter  # noqa: E402
from ingestion_pipeline.core.blobstore import LocalFileSystemBlobStore  # noqa: E402
from ingestion_pipeline.core.normalizer import DocumentNormalizer  # noqa: E402
from ingestion_pipeline.core.types import ContentType, RawItem  # noqa: E402


class FakeNormalizer(DocumentNormalizer):
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def to_markdown(self, raw_bytes: bytes, file_name: str) -> str:
        if self.fail:
            raise ValueError("conversion exploded")
        return raw_bytes.decode("utf-8")


class ListAdapter(Adapter):
    """files: {name: bytes | Exception}. Exceptions simulate a failing fetch."""

    source_id = "test_src"

    def __init__(self, files: dict) -> None:
        self.files = files
        self.advanced: list[str] = []

    def list_changes(self, cursor):
        return list(self.files)

    def fetch(self, ref):
        data = self.files[ref]
        if isinstance(data, Exception):
            raise data
        return RawItem(
            source_id=self.source_id,
            item_id=f"test_src:{ref}",
            content_type=ContentType.DOCUMENT,
            raw_bytes=data,
            file_name=ref,
        )

    def advance_cursor(self, ref):
        self.advanced.append(ref)
        return ref


def words(prefix: str, n: int) -> bytes:
    return " ".join(f"{prefix}{i}" for i in range(n)).encode()


def make_orchestrator(adapter, tmp_path, normalizer=None):
    from ingestion_pipeline.core.orchestrator import PipelineOrchestrator

    return PipelineOrchestrator(
        adapter=adapter,
        blob_store=LocalFileSystemBlobStore(tmp_path / "blobs"),
        document_normalizer=normalizer or FakeNormalizer(),
    )


def get_item(item_id):
    from ingestion_pipeline.db.models import IngestionItem
    from ingestion_pipeline.db.session import session_scope

    with session_scope() as session:
        return session.get(IngestionItem, item_id)


def normalized_text(item_id, tmp_path):
    """The archived normalized Markdown of the item's CURRENT version (what Cognee is given)."""
    from ingestion_pipeline.core.blobstore import LocalFileSystemBlobStore

    ref = get_item(item_id).storage_ref["normalized"]
    return LocalFileSystemBlobStore(tmp_path / "blobs").get(ref).decode("utf-8")


# ------------------------------------------------------------------------ P0.2 status

def test_new_item_ends_done_with_timestamps_and_version(tmp_path):
    orch = make_orchestrator(ListAdapter({"a.txt": words("w", 100)}), tmp_path)
    summary = orch.run()
    item = get_item("test_src:a.txt")
    assert summary["items_processed"] == 1
    assert item.status == "DONE"
    assert item.current_version == 1
    assert item.completed_at is not None and item.updated_at is not None
    assert normalized_text("test_src:a.txt", tmp_path) == words("w", 100).decode()


def test_duplicate_is_skipped_and_item_stays_done(tmp_path):
    orch = make_orchestrator(ListAdapter({"a.txt": words("w", 100)}), tmp_path)
    orch.run()
    before = get_item("test_src:a.txt")
    summary = orch.run()
    after = get_item("test_src:a.txt")
    assert summary["items_skipped_duplicate"] == 1 and summary["items_processed"] == 0
    assert after.status == "DONE"  # never flips back to PROCESSING / stuck
    assert after.updated_at == before.updated_at


# --------------------------------------------------------------------- P0.3 versioning

def test_changed_content_becomes_a_new_version_and_the_event_carries_it(tmp_path):
    from ingestion_pipeline.db.models import IngestionEvent
    from ingestion_pipeline.db.session import session_scope

    adapter = ListAdapter({"a.txt": words("old", 300)})
    orch = make_orchestrator(adapter, tmp_path)
    orch.run()

    adapter.files["a.txt"] = words("new", 200)
    summary = orch.run()

    assert summary["items_processed"] == 1 and summary["items_failed"] == 0
    assert get_item("test_src:a.txt").current_version == 2
    assert normalized_text("test_src:a.txt", tmp_path).startswith("new0")  # points at the NEW version
    assert len(list((tmp_path / "blobs").rglob("a.txt"))) == 2  # both raw versions archived, no BlobAlreadyExists
    with session_scope() as session:
        payloads = [e.payload for e in session.execute(select(IngestionEvent).order_by(IngestionEvent.event_id)).scalars()]
    assert [p["version"] for p in payloads] == [1, 2] and [p["outcome"] for p in payloads] == ["NEW", "UPDATE"]
    assert all(p["normalized_ref"] and p["file_name"] == "a.txt" for p in payloads)


def test_revert_to_earlier_content_is_a_new_version_not_a_duplicate(tmp_path):
    adapter = ListAdapter({"a.txt": words("A", 50)})
    orch = make_orchestrator(adapter, tmp_path)
    orch.run()
    adapter.files["a.txt"] = words("B", 50)
    orch.run()
    adapter.files["a.txt"] = words("A", 50)
    summary = orch.run()
    assert summary["items_processed"] == 1
    assert get_item("test_src:a.txt").current_version == 3
    assert normalized_text("test_src:a.txt", tmp_path).startswith("A0")


# ----------------------------------------------------- P0.5 failures, retry, cursor

def test_failed_item_is_not_retried_every_scan_and_can_be_requeued(tmp_path):
    from ingestion_pipeline.core import jobs
    from ingestion_pipeline.core.retry import requeue_dead_letter
    from ingestion_pipeline.db.models import DeadLetterItem
    from ingestion_pipeline.db.session import session_scope

    adapter = ListAdapter({"bad.txt": words("w", 50)})
    broken = make_orchestrator(adapter, tmp_path, normalizer=FakeNormalizer(fail=True))
    broken.run()
    broken.run()  # second scan of the same bad content must not add more dead letters

    with session_scope() as session:
        letters = list(session.execute(select(DeadLetterItem)).scalars())
    assert len(letters) == 1
    assert get_item("test_src:bad.txt").status == "FAILED"

    with session_scope() as session:
        job_id = requeue_dead_letter(session, letters[0].id)
    assert job_id

    fixed = make_orchestrator(adapter, tmp_path)
    worker = jobs.JobWorker({jobs.KIND_RETRY_ITEM: lambda job: fixed.retry_item(job["payload"]["item_id"])})
    assert worker.run_once() is True
    item = get_item("test_src:bad.txt")
    assert item.status == "DONE" and item.current_version == 1 and item.error_reason is None


def test_changed_content_retries_a_previously_failed_item(tmp_path):
    adapter = ListAdapter({"bad.txt": words("w", 50)})
    make_orchestrator(adapter, tmp_path, normalizer=FakeNormalizer(fail=True)).run()
    adapter.files["bad.txt"] = words("fixed", 50)
    summary = make_orchestrator(adapter, tmp_path).run()
    assert summary["items_processed"] == 1
    assert get_item("test_src:bad.txt").status == "DONE"


def test_fetch_failure_does_not_let_the_cursor_skip_past_it(tmp_path):
    from ingestion_pipeline.core import checkpoint
    from ingestion_pipeline.db.session import session_scope

    adapter = ListAdapter({"a.txt": ValueError("cannot read"), "b.txt": words("w", 20)})
    summary = make_orchestrator(adapter, tmp_path).run()
    assert summary["items_failed"] == 1 and summary["items_processed"] == 1
    assert adapter.advanced == []  # b succeeded AFTER a failed -> cursor must stay put
    with session_scope() as session:
        assert checkpoint.get_checkpoint(session, "test_src") is None


# -------------------------------------------------------------------------- P0.4 queue

def test_scan_jobs_coalesce_and_a_source_never_runs_twice_at_once():
    from ingestion_pipeline.core import jobs
    from ingestion_pipeline.db.session import session_scope

    with session_scope() as session:
        first = jobs.enqueue_scan(session, "src")
        second = jobs.enqueue_scan(session, "src")
    assert first is not None and second is None  # burst of triggers -> ONE queued scan

    with session_scope() as session:
        running = jobs.claim_next(session)
    assert running["source_id"] == "src"

    with session_scope() as session:
        assert jobs.enqueue_scan(session, "src") is not None  # a new scan may queue while one runs...
    with session_scope() as session:
        assert jobs.claim_next(session) is None  # ...but is not started until the running one finishes
    with session_scope() as session:
        jobs.finish_job(session, running["job_id"], None, running["attempts"])
    with session_scope() as session:
        assert jobs.claim_next(session) is not None


def test_failing_job_is_retried_then_marked_failed():
    from ingestion_pipeline.core import jobs
    from ingestion_pipeline.db.models import IngestionJob
    from ingestion_pipeline.db.session import get_engine, session_scope

    with session_scope() as session:
        job_id = jobs.enqueue_scan(session, "src")

    def boom(job):
        raise RuntimeError("nope")

    worker = jobs.JobWorker({jobs.KIND_SCAN: boom})
    for _ in range(jobs.MAX_ATTEMPTS):
        with get_engine().begin() as connection:
            connection.execute(text("UPDATE ingestion_jobs SET run_after = now()"))  # skip the backoff wait
        assert worker.run_once() is True

    with session_scope() as session:
        job = session.get(IngestionJob, job_id)
    assert job.status == "FAILED" and job.attempts == jobs.MAX_ATTEMPTS and "nope" in job.error
    assert worker.run_once() is False


def test_job_left_running_by_a_crash_is_recovered():
    from ingestion_pipeline.core import jobs
    from ingestion_pipeline.db.session import get_engine, session_scope

    with session_scope() as session:
        jobs.enqueue_scan(session, "src")
    with session_scope() as session:
        jobs.claim_next(session)
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE ingestion_jobs SET started_at = now() - interval '2 hours'"))
    with session_scope() as session:
        assert jobs.recover_stale_running(session) == 1
    with session_scope() as session:
        assert jobs.claim_next(session) is not None


def test_stale_recovery_survives_a_queued_scan_for_the_same_source():
    """Regression: requeueing a stale RUNNING scan while a QUEUED one exists violated the unique index and recovered nothing."""
    from sqlalchemy import text

    from ingestion_pipeline.core import jobs
    from ingestion_pipeline.db.session import get_engine, session_scope

    with get_engine().begin() as connection:
        connection.execute(text("TRUNCATE ingestion_jobs"))
        for job_id, status in (("stale1", "RUNNING"), ("stale2", "RUNNING"), ("waiting", "QUEUED")):
            connection.execute(
                text("INSERT INTO ingestion_jobs (job_id, source_id, kind, status, started_at) VALUES (:j, 'src', 'scan', :s, now() - interval '2 hours')"),
                {"j": job_id, "s": status},
            )
        connection.execute(
            text("INSERT INTO ingestion_jobs (job_id, source_id, kind, status, started_at) VALUES ('other', 'solo', 'scan', 'RUNNING', now() - interval '2 hours')")
        )
    with session_scope() as session:
        assert jobs.recover_stale_running(session, minutes=0) == 1  # only 'solo' goes back to the queue
    with get_engine().begin() as connection:
        states = dict(connection.execute(text("SELECT job_id, status FROM ingestion_jobs")).all())
    assert states == {"stale1": "FAILED", "stale2": "FAILED", "waiting": "QUEUED", "other": "QUEUED"}
    with session_scope() as session:
        assert jobs.claim_next(session) is not None  # the source is no longer blocked


# ------------------------------------------------------------------ P0.7 schema parity

def test_schema_sql_has_every_table_and_column_the_models_declare():
    from sqlalchemy import inspect

    from ingestion_pipeline.db.models import Base
    from ingestion_pipeline.db.session import get_engine

    inspector = inspect(get_engine())
    for table in Base.metadata.sorted_tables:
        assert inspector.has_table(table.name), f"{table.name} missing from sql/schema.sql"
        in_db = {column["name"] for column in inspector.get_columns(table.name)}
        in_models = {column.name for column in table.columns}
        assert in_models == in_db, f"{table.name}: models-only={in_models - in_db} schema-only={in_db - in_models}"
