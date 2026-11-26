"""NangoAdapter through the real orchestrator (Postgres): cursor progress, versions, retries, and scheduling."""
from __future__ import annotations

import os

import pytest

pgserver = pytest.importorskip("pgserver")

from sqlalchemy import select, text  # noqa: E402

from ingestion_pipeline.adapters.nango_adapter import NangoAdapter, parse_source_id  # noqa: E402
from ingestion_pipeline.connectors.providers.base import ChangeRef, FetchedDocument, ProviderFetcher  # noqa: E402
from ingestion_pipeline.core.types import ContentType  # noqa: E402

SOURCE = "nango:google-mail:conn-1"


@pytest.fixture(scope="module", autouse=True)
def _database(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata_adapter"))
    os.environ["DATABASE_URL"] = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["NANGO_SECRET_KEY"] = "k"
    os.environ["RETRY_BASE_SECONDS"] = "0.01"
    from ingestion_pipeline.config import get_settings
    import ingestion_pipeline.db.session as session_module

    get_settings.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None
    assert session_module.ensure_schema()
    yield
    session_module.get_engine().dispose()
    server.cleanup()


@pytest.fixture(autouse=True)
def _clean(_database):
    from ingestion_pipeline.db.session import get_engine

    with get_engine().begin() as connection:
        connection.execute(
            text(
                "TRUNCATE ingestion_jobs, dead_letter_items, ingestion_events, event_consumer_offsets, tabular_rows, "
                "ingestion_item_versions, dedup_registry, connector_checkpoints, pipeline_runs, ingestion_items, connector_connections CASCADE"
            )
        )


class FakeClient:
    def __init__(self):
        self.token_calls = 0

    def get_access_token(self, connection_id, integration_key):
        assert (connection_id, integration_key) == ("conn-1", "google-mail")
        self.token_calls += 1
        return "TOK"


class FakeFetcher(ProviderFetcher):
    provider = "google-mail"

    def list_latest(self, token, limit, initial_days, skip_ids):
        fresh = [(c, i) for i, (c, _b) in self.items.items() if i not in skip_ids]
        newest = sorted(fresh, reverse=True)[:limit]
        return [ChangeRef(i, i, c) for c, i in sorted(newest)]

    def __init__(self):
        self.items = {}  # id -> (cursor, body)
        self.fail_on = set()
        self.cursors_seen = []

    def list_changes(self, token, cursor, limit, initial_days):
        assert token == "TOK"
        self.cursors_seen.append(cursor)
        newer = [(c, i) for i, (c, _b) in self.items.items() if cursor is None or int(c) > int(cursor)]
        return [ChangeRef(i, i, c) for c, i in sorted(newer)][:limit]

    def fetch(self, token, ref):
        if ref.external_id in self.fail_on:
            raise RuntimeError("provider exploded")
        return FetchedDocument(f"{ref.title}.md", ContentType.MESSAGE, self.items[ref.external_id][1].encode(), {"title": ref.title})


def orchestrator(fetcher, client=None, limit=100, max_total=0):
    from ingestion_pipeline.core.blobstore import get_blob_store
    from ingestion_pipeline.core.orchestrator import PipelineOrchestrator

    class Blob:
        def __init__(self):
            self.data = {}

        def put(self, key, data):
            self.data[key] = data
            return key

        def get(self, key):
            return self.data[key]

    adapter = NangoAdapter(SOURCE, client or FakeClient(), fetcher, limit=limit, max_total=max_total)
    return PipelineOrchestrator(adapter=adapter, blob_store=Blob(), document_normalizer=None)


def test_parse_source_id():
    assert parse_source_id("nango:google-mail:a:b") == ("google-mail", "a:b")
    for bad in ("nango:x", "local:a:b", "nango::b"):
        with pytest.raises(ValueError):
            parse_source_id(bad)


def test_cursor_moves_forward_and_only_new_items_are_ingested():
    from ingestion_pipeline.db.models import ConnectorCheckpoint, IngestionItem
    from ingestion_pipeline.db.session import session_scope

    fetcher = FakeFetcher()
    fetcher.items = {"a": ("100", "alpha"), "b": ("200", "beta")}
    orch = orchestrator(fetcher)
    assert orch.run()["items_processed"] == 2
    fetcher.items["c"] = ("300", "gamma")
    summary = orch.run()
    assert summary["items_identified"] == 1 and summary["items_processed"] == 1
    assert fetcher.cursors_seen == [None, "200"]
    with session_scope() as s:
        ids = sorted(s.scalars(select(IngestionItem.item_id)))
        assert ids == [f"{SOURCE}/a", f"{SOURCE}/b", f"{SOURCE}/c"]
        item = s.get(IngestionItem, f"{SOURCE}/a")
        assert item.status == "DONE" and item.source_metadata["file_name"] == "a.md"


def test_failed_item_does_not_move_cursor_past_it():
    from ingestion_pipeline.core import checkpoint
    from ingestion_pipeline.db.session import session_scope

    fetcher = FakeFetcher()
    fetcher.items = {"a": ("100", "alpha"), "b": ("200", "beta"), "c": ("300", "gamma")}
    fetcher.fail_on = {"b"}
    summary = orchestrator(fetcher).run()
    assert summary["items_failed"] == 1
    with session_scope() as s:
        assert checkpoint.get_checkpoint(s, SOURCE) == "100"  # stopped before the failure, so b is tried again


def test_token_is_reused_within_a_run():
    fetcher = FakeFetcher()
    fetcher.items = {str(i): (str(i * 10), f"body {i}") for i in range(1, 6)}
    client = FakeClient()
    orchestrator(fetcher, client).run()
    assert client.token_calls == 1


def test_changed_content_becomes_a_new_version():
    from ingestion_pipeline.db.models import IngestionItem
    from ingestion_pipeline.db.session import session_scope

    fetcher = FakeFetcher()
    fetcher.items = {"a": ("100", "v1")}
    orch = orchestrator(fetcher)
    orch.run()
    fetcher.items["a"] = ("150", "v2")
    assert orch.run()["items_processed"] == 1
    with session_scope() as s:
        assert s.get(IngestionItem, f"{SOURCE}/a").current_version == 2


def test_poll_once_queues_only_active_enabled_connections():
    from ingestion_pipeline.connectors.nango_client import NangoConnection
    from ingestion_pipeline.connectors.scheduler import poll_once
    from ingestion_pipeline.db.models import ConnectorConnection, IngestionJob
    from ingestion_pipeline.db.session import session_scope

    class Nango:
        def list_connections(self):
            mk = lambda cid, key, err=False: NangoConnection(cid, key, key, None, err, None)
            return [mk("c1", "slack"), mk("c2", "notion"), mk("c3", "google-mail", err=True)]

    with session_scope() as s:
        s.add(ConnectorConnection(nango_connection_id="c2", integration_key="notion", provider="notion", enabled=False))
    queued = poll_once(Nango())
    assert queued == ["nango:slack:c1"]  # c2 paused, c3 has an auth error
    assert poll_once(Nango()) == []  # a scan is already waiting: not duplicated
    with session_scope() as s:
        assert [j.source_id for j in s.scalars(select(IngestionJob))] == ["nango:slack:c1"]


def test_account_cap_keeps_the_newest_and_a_higher_cap_brings_in_more():
    from ingestion_pipeline.db.models import IngestionItem
    from ingestion_pipeline.db.session import session_scope

    fetcher = FakeFetcher()
    fetcher.items = {f"m{n}": (str(n * 100), f"body {n}") for n in range(1, 6)}  # m5 is the newest

    def ingested():
        with session_scope() as s:
            return sorted(i.split("/")[-1] for i in s.scalars(select(IngestionItem.item_id)))

    assert orchestrator(fetcher, max_total=3).run()["items_processed"] == 3
    assert ingested() == ["m3", "m4", "m5"]  # the newest three
    assert orchestrator(fetcher, max_total=3).run()["items_identified"] == 0  # cap reached: nothing more

    assert orchestrator(fetcher, max_total=4).run()["items_processed"] == 1  # raised: next-newest
    assert ingested() == ["m2", "m3", "m4", "m5"]
    assert orchestrator(fetcher, max_total=10**9).run()["items_processed"] == 1  # no cap (GMAIL_MAX_MAILS=0)
    assert ingested() == ["m1", "m2", "m3", "m4", "m5"]
