"""Chat API against a real Postgres (pgserver) with a scripted agent."""
from __future__ import annotations

import json
import os

import pytest

pgserver = pytest.importorskip("pgserver")
pytest.importorskip("jwt")
pytest.importorskip("argon2")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module", autouse=True)
def _database(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata_chat"))
    os.environ["DATABASE_URL"] = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    os.environ["AUTH_SECRET"] = "test-secret-" + "x" * 40

    from ingestion_pipeline.config import get_settings
    import ingestion_pipeline.db.session as session_module

    get_settings.cache_clear()
    session_module._engine = None
    session_module._SessionLocal = None
    assert session_module.ensure_schema()
    yield
    session_module.get_engine().dispose()
    server.cleanup()


class ScriptedAgent:
    def __init__(self):
        self.histories = []
        self.fail = False

    def stream(self, question, history=None):
        self.histories.append(list(history or []))
        if self.fail:
            raise RuntimeError("model unavailable")
        src = [{"n": 1, "title": "ada.md", "item_id": "local:ada.md", "source_id": "local_folder", "snippet": "s", "cited": True}]
        yield {"type": "sources", "sources": src}
        yield {"type": "token", "text": "Hello "}
        yield {"type": "token", "text": "[1]"}
        yield {"type": "done", "answer": "Hello [1]", "sources": src}


@pytest.fixture
def setup(_database, monkeypatch):
    from ingestion_pipeline.api import auth as api_auth
    from ingestion_pipeline.api import chat as chat_api
    from ingestion_pipeline.core.auth import create_user
    from ingestion_pipeline.db.session import get_engine

    api_auth.throttle._failures.clear()
    with get_engine().begin() as connection:
        connection.execute(text("TRUNCATE app_users CASCADE"))
    agent = ScriptedAgent()
    monkeypatch.setattr(chat_api, "get_agent", lambda: agent)

    app = FastAPI(dependencies=[Depends(api_auth.require_user)])
    app.include_router(api_auth.router)
    app.include_router(chat_api.router)

    def login(email):
        create_user(email, PASSWORD)
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
        return c

    return app, agent, login


def parse_sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        out.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return out


def test_requires_login(setup):
    app, _, _ = setup
    assert TestClient(app).post("/api/chat/stream", json={"message": "hi"}).status_code == 401
    assert TestClient(app).get("/api/chat/conversations").status_code == 401


def test_stream_persists_conversation_and_memory(setup):
    _, agent, login = setup
    client = login("a@example.com")

    events = parse_sse(client.post("/api/chat/stream", json={"message": "Who did Ada work with?"}).text)
    assert [e[0] for e in events] == ["conversation", "sources", "token", "token", "done"]
    conversation_id = events[0][1]["id"]
    assert events[0][1]["title"] == "Who did Ada work with?" and agent.histories[0] == []

    # a follow-up in the same conversation sees the earlier turns
    client.post("/api/chat/stream", json={"message": "and then?", "conversation_id": conversation_id})
    assert [m["role"] for m in agent.histories[1]] == ["user", "assistant"] and agent.histories[1][1]["content"] == "Hello [1]"

    detail = client.get(f"/api/chat/conversations/{conversation_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["sources"][0]["title"] == "ada.md"
    assert client.get("/api/chat/conversations").json()[0]["id"] == conversation_id


def test_conversations_are_private_to_their_owner(setup):
    _, _, login = setup
    alice, bob = login("alice@example.com"), login("bob@example.com")
    cid = parse_sse(alice.post("/api/chat/stream", json={"message": "secret"}).text)[0][1]["id"]
    assert bob.get(f"/api/chat/conversations/{cid}").status_code == 404
    assert bob.delete(f"/api/chat/conversations/{cid}").status_code == 404
    assert bob.post("/api/chat/stream", json={"message": "x", "conversation_id": cid}).status_code == 404
    assert bob.get("/api/chat/conversations").json() == []


def test_delete_and_validation(setup):
    _, _, login = setup
    client = login("a@example.com")
    cid = parse_sse(client.post("/api/chat/stream", json={"message": "hello"}).text)[0][1]["id"]
    assert client.delete(f"/api/chat/conversations/{cid}").status_code == 200
    assert client.get(f"/api/chat/conversations/{cid}").status_code == 404
    assert client.post("/api/chat/stream", json={"message": ""}).status_code == 422
    assert client.post("/api/chat/stream", json={"message": "x" * 5000}).status_code == 422


def test_agent_failure_is_reported_as_an_error_event(setup):
    _, agent, login = setup
    client = login("a@example.com")
    agent.fail = True
    events = parse_sse(client.post("/api/chat/stream", json={"message": "hi"}).text)
    assert [e[0] for e in events] == ["conversation", "error"]
    cid = events[0][1]["id"]
    # the question is kept so the user can see what was asked
    assert [m["role"] for m in client.get(f"/api/chat/conversations/{cid}").json()["messages"]] == ["user"]


def test_indexing_status_counts_pending_events(setup):
    from ingestion_pipeline.consumers.cognee_consumer import CONSUMER_NAME
    from ingestion_pipeline.core import events
    from ingestion_pipeline.db.session import get_engine

    _, _, login = setup
    client = login("a@example.com")
    with get_engine().begin() as c:
        c.execute(text("TRUNCATE ingestion_events, event_consumer_offsets, ingestion_items CASCADE"))
        for i in range(3):
            c.execute(text("INSERT INTO ingestion_items (item_id, source_id, content_type, sha256_hash, status) VALUES (:i, 'nango:google-mail:x', 'MESSAGE', :h, 'DONE')"), {"i": f"nango:google-mail:x/{i}", "h": str(i)})
            c.execute(
                text("INSERT INTO ingestion_events (topic, item_id, payload) VALUES (:t, :i, CAST(:p AS jsonb))"),
                {"t": events.TOPIC_ITEM_INGESTED, "i": f"nango:google-mail:x/{i}",
                 "p": json.dumps({"source_id": "nango:google-mail:x", "content_type": "MESSAGE", "normalized_ref": "local://n"})},
            )
        first = c.execute(text("SELECT min(event_id) FROM ingestion_events")).scalar()
        c.execute(text("INSERT INTO event_consumer_offsets (consumer_name, topic, last_event_id) VALUES (:n, :t, :e)"),
                  {"n": CONSUMER_NAME, "t": events.TOPIC_ITEM_INGESTED, "e": first})
    status = client.get("/api/chat/status").json()
    assert (status["total"], status["indexed"], status["pending"]) == (3, 1, 2)
    assert status["sources"][0]["source_id"] == "nango:google-mail:x"
