"""
SQLAlchemy engine/session setup.

Uses the `psycopg` (v3) driver -- postgresql+psycopg:// in DATABASE_URL.
All DB access in this codebase goes through SQLAlchemy Core/ORM; no module
opens a raw psycopg2 connection anywhere.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ingestion_pipeline.config import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back and re-raises on exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_schema() -> bool:
    """Apply sql/schema.sql (idempotent) so a fresh or older database is brought up to date
    without a manual pgAdmin step. Returns False if the file can't be found (e.g. installed
    as a wheel) -- the caller should then rely on the documented manual setup."""
    from pathlib import Path

    schema_path = Path(__file__).resolve().parents[3] / "sql" / "schema.sql"
    if not schema_path.exists():
        return False
    with get_engine().begin() as connection:
        connection.exec_driver_sql(schema_path.read_text(encoding="utf-8"))
    return True
