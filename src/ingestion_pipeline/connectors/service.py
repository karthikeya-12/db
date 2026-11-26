"""Our record of Nango connections: mirroring Nango's list into Postgres and the per-connection settings."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ingestion_pipeline.connectors.nango_client import NangoClient
from ingestion_pipeline.db.models import ConnectorConnection


def source_id_for(connection: ConnectorConnection) -> str:
    """The ingestion source_id for one connection (also the prefix of its items' ids)."""
    return f"nango:{connection.integration_key}:{connection.nango_connection_id}"


def connection_to_dict(row: ConnectorConnection) -> dict:
    return {
        "id": row.id,
        "integration_key": row.integration_key,
        "provider": row.provider,
        "display_name": row.display_name or row.provider,
        "status": row.status,
        "enabled": row.enabled,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "last_error": row.last_error,
        "source_id": source_id_for(row),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def mirror_connections(session, client: NangoClient, kick_new: bool = False) -> list[ConnectorConnection]:
    """Make our table match Nango's list: add new connections, refresh status, mark vanished ones DISCONNECTED.
    Returns every row afterwards. (Free self-hosted Nango sends no auth webhook, so this runs on demand and on a timer.)
    With kick_new, a connection that has just appeared (or just recovered) gets its first scan queued immediately
    instead of waiting for the next poll."""
    remote = client.list_connections()
    existing = {(r.nango_connection_id, r.integration_key): r for r in session.scalars(select(ConnectorConnection))}
    now = datetime.now(timezone.utc)
    seen: set[tuple[str, str]] = set()
    to_scan: list[ConnectorConnection] = []

    for item in remote:
        key = (item.connection_id, item.integration_key)
        seen.add(key)
        row = existing.get(key)
        status = "ERROR" if item.has_errors else "ACTIVE"
        if row is None:
            row = ConnectorConnection(
                nango_connection_id=item.connection_id,
                integration_key=item.integration_key,
                provider=item.provider or item.integration_key,
                display_name=item.end_user_email,
                status=status,
                last_error="Sign-in needs to be renewed" if item.has_errors else None,
            )
            session.add(row)
            existing[key] = row
            if status == "ACTIVE":
                to_scan.append(row)
        else:
            if row.status != "ACTIVE" and status == "ACTIVE" and row.enabled:
                to_scan.append(row)
            row.status = status
            row.updated_at = now
            if item.has_errors:
                row.last_error = "Sign-in needs to be renewed"
            elif row.last_error == "Sign-in needs to be renewed":
                row.last_error = None

    for key, row in existing.items():
        if key not in seen and row.status != "DISCONNECTED":
            row.status = "DISCONNECTED"
            row.updated_at = now

    session.flush()
    if kick_new:
        from ingestion_pipeline.core import jobs

        for row in to_scan:
            jobs.enqueue_scan(session, source_id_for(row))
    return list(existing.values())


def record_sync_result(source_id: str, summary: dict) -> None:
    """After a scan of a connection: remember when it ran and whether anything failed."""
    from ingestion_pipeline.adapters.nango_adapter import parse_source_id
    from ingestion_pipeline.db.session import session_scope

    integration_key, connection_id = parse_source_id(source_id)
    with session_scope() as session:
        row = session.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.integration_key == integration_key,
                ConnectorConnection.nango_connection_id == connection_id,
            )
        )
        if row is None:
            return
        row.last_synced_at = datetime.now(timezone.utc)
        failed = summary.get("items_failed", 0)
        row.last_error = f"{failed} item(s) could not be ingested" if failed else (None if row.status != "ERROR" else row.last_error)


def record_sync_failure(source_id: str, error: str) -> None:
    from ingestion_pipeline.adapters.nango_adapter import parse_source_id
    from ingestion_pipeline.db.session import session_scope

    integration_key, connection_id = parse_source_id(source_id)
    with session_scope() as session:
        row = session.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.integration_key == integration_key,
                ConnectorConnection.nango_connection_id == connection_id,
            )
        )
        if row is not None:
            row.last_error = error[:300]
