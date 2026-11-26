"""Auth against a real Postgres (pgserver) through a minimal FastAPI app that uses the same router and dependency."""
from __future__ import annotations

import os
import time

import pytest

pgserver = pytest.importorskip("pgserver")
pytest.importorskip("jwt")
pytest.importorskip("argon2")

import jwt  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module", autouse=True)
def _database(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata_auth"))
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


@pytest.fixture(autouse=True)
def _clean(_database):
    from ingestion_pipeline.api import auth as api_auth
    from ingestion_pipeline.db.session import get_engine

    api_auth.throttle._failures.clear()
    api_auth.signup_throttle._failures.clear()
    with get_engine().begin() as connection:
        connection.execute(text("TRUNCATE app_users CASCADE"))


@pytest.fixture
def client():
    from ingestion_pipeline.api.auth import require_user, router

    app = FastAPI(dependencies=[Depends(require_user)])
    app.include_router(router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/items")
    def items():
        return []

    return TestClient(app)


def _make_user(email="Reader@Example.com"):
    from ingestion_pipeline.core.auth import create_user

    return create_user(email, PASSWORD)


def test_protected_routes_need_login_but_health_is_public(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/items").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_login_flow(client):
    _make_user()
    assert client.post("/api/auth/login", json={"email": "reader@example.com", "password": "nope"}).status_code == 401
    ok = client.post("/api/auth/login", json={"email": "READER@example.com", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "reader@example.com"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/items").status_code == 200
    assert client.get("/api/auth/me").json()["user"]["email"] == "reader@example.com"
    client.post("/api/auth/logout")
    assert client.get("/api/items").status_code == 401


def test_unknown_user_and_wrong_password_look_identical(client):
    _make_user()
    a = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    b = client.post("/api/auth/login", json={"email": "reader@example.com", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_throttle_blocks_after_repeated_failures(client):
    _make_user()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "reader@example.com", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "reader@example.com", "password": PASSWORD}).status_code == 429


def test_inactive_user_cannot_sign_in_or_keep_session(client):
    from ingestion_pipeline.db.session import get_engine

    _make_user()
    assert client.post("/api/auth/login", json={"email": "reader@example.com", "password": PASSWORD}).status_code == 200
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE app_users SET is_active = false"))
    assert client.get("/api/items").status_code == 401  # existing session stops working immediately
    assert client.post("/api/auth/login", json={"email": "reader@example.com", "password": PASSWORD}).status_code == 401


def test_tampered_and_expired_tokens_rejected(client):
    from ingestion_pipeline.api.auth import COOKIE_NAME
    from ingestion_pipeline.core import auth as core

    user = _make_user()
    secret = os.environ["AUTH_SECRET"]
    forged = jwt.encode({"sub": user["user_id"], "exp": time.time() + 60}, "other-secret-" + "y" * 40, algorithm="HS256")
    expired = jwt.encode({"sub": user["user_id"], "exp": time.time() - 5}, secret, algorithm="HS256")
    for token in (forged, expired, "garbage"):
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/api/items").status_code == 401
    assert core.read_token(core.create_token(user["user_id"])) == user["user_id"]


def test_create_user_validation():
    from ingestion_pipeline.core.auth import AuthError, create_user

    _make_user("a@example.com")
    with pytest.raises(AuthError):
        create_user("A@EXAMPLE.COM", PASSWORD)  # duplicate, case-insensitive
    with pytest.raises(AuthError):
        create_user("not-an-email", PASSWORD)
    with pytest.raises(AuthError):
        create_user("b@example.com", "short")


def test_seed_admin_only_when_empty(monkeypatch):
    from ingestion_pipeline.config import get_settings
    from ingestion_pipeline.core import auth as core

    monkeypatch.setenv("ADMIN_EMAIL", "boss@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    try:
        assert core.seed_admin_if_empty() is True
        assert core.authenticate("boss@example.com", PASSWORD) is not None
        assert core.seed_admin_if_empty() is False  # users exist now
    finally:
        monkeypatch.delenv("ADMIN_EMAIL")
        monkeypatch.delenv("ADMIN_PASSWORD")
        get_settings.cache_clear()


def test_operator_can_reset_a_password(client):
    from ingestion_pipeline.core.auth import authenticate, set_password

    _make_user()
    assert set_password("reader@example.com", "a brand new password") is True
    assert authenticate("reader@example.com", PASSWORD) is None
    assert authenticate("reader@example.com", "a brand new password") is not None
    assert set_password("nobody@example.com", "a brand new password") is False


def test_signup_creates_account_and_signs_in(client):
    assert client.get("/api/auth/config").json() == {"signup_enabled": True, "invite_required": False}
    response = client.post("/api/auth/signup", json={"email": "New@Example.com", "password": PASSWORD})
    assert response.status_code == 200 and response.json()["user"]["email"] == "new@example.com"
    assert client.get("/api/auth/me").status_code == 200  # cookie was set
    assert client.post("/api/auth/signup", json={"email": "new@example.com", "password": PASSWORD}).status_code == 409
    assert client.post("/api/auth/signup", json={"email": "x@example.com", "password": "short"}).status_code == 422


def test_signup_invite_code_and_switch(client, monkeypatch):
    from ingestion_pipeline.config import get_settings

    monkeypatch.setenv("SIGNUP_INVITE_CODE", "open-sesame")
    get_settings.cache_clear()
    assert client.get("/api/auth/config").json()["invite_required"] is True
    body = {"email": "a@example.com", "password": PASSWORD}
    assert client.post("/api/auth/signup", json=body).status_code == 403
    assert client.post("/api/auth/signup", json={**body, "invite_code": "wrong"}).status_code == 403
    assert client.post("/api/auth/signup", json={**body, "invite_code": "open-sesame"}).status_code == 200

    monkeypatch.setenv("ALLOW_SIGNUP", "false")
    get_settings.cache_clear()
    assert client.post("/api/auth/signup", json={**body, "email": "b@example.com", "invite_code": "open-sesame"}).status_code == 403
    monkeypatch.undo()
    get_settings.cache_clear()
