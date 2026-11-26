"""Nango client (mock HTTP), connection mirroring and the connector API (real Postgres via pgserver)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import hmac
import json
import os

import httpx
import pytest

pgserver = pytest.importorskip("pgserver")

from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from ingestion_pipeline.connectors.nango_client import NangoClient, NangoError, verify_webhook_signature  # noqa: E402

SECRET = "nango-secret-key-for-tests"


@pytest.fixture(scope="module", autouse=True)
def _database(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata_nango"))
    os.environ["DATABASE_URL"] = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["NANGO_SECRET_KEY"] = SECRET

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
        connection.execute(text("TRUNCATE connector_connections, ingestion_jobs, pipeline_runs, ingestion_items CASCADE"))


class FakeNango:
    """Stands in for Nango's HTTP API."""

    def __init__(self):
        self.connections = []
        self.deleted = []
        self.sessions = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        path = request.url.path
        if request.method == "GET" and path == "/connections":
            return httpx.Response(200, json={"connections": self.connections})
        if request.method == "GET" and path.startswith("/connections/"):
            if path.endswith("missing"):
                return httpx.Response(404, json={"error": {"message": "not found"}})
            return httpx.Response(200, json={"credentials": {"access_token": "tok-123"}})
        if request.method == "DELETE" and path.startswith("/connections/"):
            self.deleted.append((path.rsplit("/", 1)[1], request.url.params["provider_config_key"]))
            return httpx.Response(204)
        if request.method == "POST" and path == "/connect/sessions":
            self.sessions.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"token": "sess-tok", "expires_at": "2030-01-01T00:00:00Z", "connect_link": "http://x"}})
        if request.method == "GET" and path == "/integrations":
            return httpx.Response(200, json={"data": [{"unique_key": "google-mail", "provider": "google-mail", "display_name": "Gmail", "logo": "l"}]})
        return httpx.Response(404, json={"error": {"message": "unexpected " + path}})

    def client(self) -> NangoClient:
        return NangoClient(base_url="http://nango.test", secret_key=SECRET, transport=httpx.MockTransport(self.handler))


def conn(connection_id, key="google-mail", errors=False, email="a@b.com"):
    return {
        "connection_id": connection_id,
        "provider_config_key": key,
        "provider": key,
        "created": "2026-01-01T00:00:00Z",
        "errors": [{"type": "auth", "log_id": "x"}] if errors else [],
        "end_user": {"email": email},
    }


def test_signature_verification():
    body = b'{"type":"auth"}'
    good = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, good, SECRET)
    assert verify_webhook_signature(body, good.upper(), SECRET)
    assert not verify_webhook_signature(body, good, "other-key")
    assert not verify_webhook_signature(body + b" ", good, SECRET)
    assert not verify_webhook_signature(body, None, SECRET)
    assert not verify_webhook_signature(body, "zz", SECRET)


def test_client_calls():
    fake = FakeNango()
    fake.connections = [conn("c1")]
    client = fake.client()
    listed = client.list_connections()
    assert [(c.connection_id, c.integration_key, c.has_errors) for c in listed] == [("c1", "google-mail", False)]
    assert client.get_access_token("c1", "google-mail") == "tok-123"
    assert client.create_connect_session("u1", "me@x.com", ["google-mail"])["token"] == "sess-tok"
    assert fake.sessions[0] == {"end_user": {"id": "u1", "email": "me@x.com"}, "allowed_integrations": ["google-mail"]}
    with pytest.raises(NangoError) as err:
        client.get_access_token("missing", "google-mail")
    assert err.value.status_code == 404


def test_client_requires_secret_key():
    with pytest.raises(NangoError):
        NangoClient(base_url="http://x", secret_key="")


def test_mirror_adds_updates_and_disconnects():
    from ingestion_pipeline.connectors.service import mirror_connections
    from ingestion_pipeline.db.session import session_scope

    fake = FakeNango()
    client = fake.client()
    fake.connections = [conn("c1"), conn("c2", key="slack", errors=True)]
    with session_scope() as s:
        rows = {r.nango_connection_id: r for r in mirror_connections(s, client, kick_new=True)}
        assert rows["c1"].status == "ACTIVE" and rows["c2"].status == "ERROR"
        rows["c1"].enabled = False  # a user setting
    from ingestion_pipeline.db.models import IngestionJob
    from sqlalchemy import select as _select

    with session_scope() as s:
        # only the healthy new connection was queued for its first scan; the one with an auth error was not
        assert [j.source_id for j in s.scalars(_select(IngestionJob))] == ["nango:google-mail:c1"]
    fake.connections = [conn("c2", key="slack")]  # c1 vanished, c2 recovered
    with session_scope() as s:
        rows = {r.nango_connection_id: r for r in mirror_connections(s, client)}
        assert rows["c1"].status == "DISCONNECTED"
        assert rows["c2"].status == "ACTIVE" and rows["c2"].last_error is None
    fake.connections = [conn("c1"), conn("c2", key="slack")]  # c1 reconnected: keeps its setting
    with session_scope() as s:
        rows = {r.nango_connection_id: r for r in mirror_connections(s, client)}
        assert rows["c1"].status == "ACTIVE" and rows["c1"].enabled is False


@pytest.fixture
def api(monkeypatch):
    from ingestion_pipeline.api import connectors as api_connectors

    fake = FakeNango()
    monkeypatch.setattr(api_connectors, "get_client", fake.client)

    def fake_user(request: Request):
        request.state.user = {"user_id": "u1", "email": "me@x.com"}

    app = FastAPI(dependencies=[Depends(fake_user)])
    app.include_router(api_connectors.router)
    app.include_router(api_connectors.webhook_router)
    return TestClient(app), fake


def test_api_flow(api):
    client, fake = api
    fake.connections = [conn("c1")]
    assert client.get("/api/connectors/integrations").json()[0]["key"] == "google-mail"
    assert client.post("/api/connectors/session", json={"integration_key": "google-mail"}).json()["token"] == "sess-tok"
    assert fake.sessions[-1]["end_user"]["id"] == "u1"

    listed = client.get("/api/connectors/connections").json()
    assert len(listed) == 1 and listed[0]["status"] == "ACTIVE" and listed[0]["source_id"] == "nango:google-mail:c1"
    cid = listed[0]["id"]

    assert client.post(f"/api/connectors/connections/{cid}", json={"enabled": False}).json()["enabled"] is False
    assert client.post("/api/connectors/connections/nope", json={"enabled": True}).status_code == 404

    assert client.delete(f"/api/connectors/connections/{cid}").json() == {"ok": True}
    assert fake.deleted == [("c1", "google-mail")]
    fake.connections = []
    assert client.get("/api/connectors/connections").json() == []


def test_webhook_requires_valid_signature(api):
    client, fake = api
    fake.connections = [conn("c9")]
    body = json.dumps({"type": "auth", "operation": "creation"}).encode()
    assert client.post("/api/nango/webhook", content=body).status_code == 401
    assert client.post("/api/nango/webhook", content=body, headers={"x-nango-hmac-sha256": "bad"}).status_code == 401
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert client.post("/api/nango/webhook", content=body, headers={"x-nango-hmac-sha256": signature}).status_code == 200
    assert [c["id"] for c in client.get("/api/connectors/connections", params={"refresh": "false"}).json()] != []


def test_catalog_combines_integrations_and_connections(api):
    client, fake = api
    fake.connections = [conn("c1")]  # a Gmail account is connected
    data = client.get("/api/connectors/catalog").json()
    assert data["nango"]["ok"] is True
    by_name = {p["name"]: p for p in data["providers"]}
    gmail = by_name["Gmail"]
    assert gmail["integration_key"] == "google-mail" and gmail["supported"] is True
    assert [c["status"] for c in gmail["connections"]] == ["ACTIVE"]
    assert by_name["Slack"]["integration_key"] is None and by_name["Slack"]["connections"] == []
    assert "Email & Calendar" in data["categories"]


def test_catalog_survives_nango_being_down(api, monkeypatch):
    from ingestion_pipeline.api import connectors as api_connectors

    client, _fake = api

    def down():
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Cannot reach Nango")

    monkeypatch.setattr(api_connectors, "get_client", down)
    data = client.get("/api/connectors/catalog").json()
    assert data["nango"] == {"ok": False, "error": "Cannot reach Nango"}
    assert len(data["providers"]) >= 5


def test_detail_and_sync_now(api):
    from ingestion_pipeline.db.models import IngestionItem, PipelineRun
    from ingestion_pipeline.db.session import session_scope

    client, fake = api
    fake.connections = [conn("c1")]
    cid = client.get("/api/connectors/connections", params={"refresh": "false"}).json() or client.get("/api/connectors/connections").json()
    cid = cid[0]["id"]
    source = "nango:google-mail:c1"
    with session_scope() as s:
        s.add(IngestionItem(item_id=f"{source}/m1", source_id=source, content_type="MESSAGE", status="DONE", source_metadata={"file_name": "Hello.md"}))
        s.add(IngestionItem(item_id=f"{source}/m2", source_id=source, content_type="MESSAGE", status="FAILED"))
        s.add(IngestionItem(item_id="other/x", source_id="other", content_type="DOCUMENT", status="DONE"))
        s.add(PipelineRun(run_id="r1", source_id=source, items_identified=2, items_processed=1, items_failed=1, started_at=datetime.now(timezone.utc), duration_seconds=1.5))
    detail = client.get(f"/api/connectors/connections/{cid}").json()
    assert detail["stats"] == {"documents": 1, "failed": 1, "in_progress": 0}
    assert {i["file_name"] for i in detail["recent_items"]} == {"Hello.md", None}
    assert detail["recent_runs"][0]["items_processed"] == 1
    assert client.get("/api/connectors/connections/nope").status_code == 404

    from sqlalchemy import text as _text

    with session_scope() as s:
        s.execute(_text("DELETE FROM ingestion_jobs"))  # discovering the connection already queued its first scan
    first = client.post(f"/api/connectors/connections/{cid}/sync").json()
    assert first["queued"] is True
    assert client.post(f"/api/connectors/connections/{cid}/sync").json()["queued"] is False  # already waiting
