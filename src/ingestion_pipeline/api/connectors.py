"""Connector endpoints: Nango connect sessions, the connections list, settings, and the (optional) webhook."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from ingestion_pipeline.config import get_settings
from ingestion_pipeline.connectors.nango_client import NangoClient, NangoError, verify_webhook_signature
from ingestion_pipeline.connectors.service import connection_to_dict, mirror_connections, source_id_for
from ingestion_pipeline.core import jobs
from ingestion_pipeline.db.models import ConnectorConnection, IngestionItem, PipelineRun
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])
webhook_router = APIRouter(tags=["connectors"])


def get_client() -> NangoClient:
    """Overridable in tests."""
    try:
        return NangoClient()
    except NangoError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _nango_call(fn):
    try:
        return fn()
    except NangoError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class SessionBody(BaseModel):
    integration_key: str | None = None  # limit the Connect screen to one integration


@router.get("/catalog")
def catalog() -> dict:
    """Everything the Connectors screen needs: each connector we offer, whether its Nango integration is set up,
    and the accounts connected to it. Still answers (with `nango.ok = false`) when Nango is unreachable."""
    from ingestion_pipeline.connectors.catalog import CATEGORY_ORDER, catalog_dicts

    nango = {"ok": True, "error": None}
    integrations: dict[str, str] = {}  # provider -> integration key
    try:
        client = get_client()
        try:
            for item in client.list_integrations():
                integrations.setdefault(item["provider"], item["unique_key"])
            with session_scope() as session:
                mirror_connections(session, client, kick_new=True)
        finally:
            client.close()
    except HTTPException as exc:  # no secret key configured
        nango = {"ok": False, "error": str(exc.detail)}
    except NangoError as exc:
        nango = {"ok": False, "error": str(exc)}

    with session_scope() as session:
        rows = session.scalars(
            select(ConnectorConnection).where(ConnectorConnection.status != "DISCONNECTED").order_by(ConnectorConnection.created_at)
        ).all()
        by_provider: dict[str, list[dict]] = {}
        for row in rows:
            by_provider.setdefault(row.provider, []).append(connection_to_dict(row))

    providers = [
        {**entry, "integration_key": integrations.get(entry["provider"]), "connections": by_provider.get(entry["provider"], [])}
        for entry in catalog_dicts()
    ]
    return {"nango": nango, "categories": CATEGORY_ORDER, "providers": providers}


@router.get("/integrations")
def list_integrations() -> list[dict]:
    """Integrations configured in Nango (providers whose OAuth app has been set up)."""
    client = get_client()
    try:
        rows = _nango_call(client.list_integrations)
    finally:
        client.close()
    return [
        {"key": r["unique_key"], "provider": r["provider"], "display_name": r.get("display_name") or r["provider"], "logo": r.get("logo")}
        for r in rows
    ]


@router.post("/session")
def create_session(body: SessionBody, request: Request) -> dict:
    """A token for the Connect screen. Opens Nango's sign-in for the chosen integration."""
    user = request.state.user
    client = get_client()
    try:
        data = _nango_call(
            lambda: client.create_connect_session(
                user_id=user["user_id"],
                email=user["email"],
                allowed_integrations=[body.integration_key] if body.integration_key else None,
            )
        )
    finally:
        client.close()
    return {"token": data["token"], "expires_at": data.get("expires_at"), "connect_link": data.get("connect_link")}


@router.get("/connections")
def list_connections(refresh: bool = True) -> list[dict]:
    """Connected accounts. By default re-reads Nango first, so a just-finished sign-in shows up."""
    if refresh:
        client = get_client()
        try:
            with session_scope() as session:
                _nango_call(lambda: mirror_connections(session, client, kick_new=True))
        finally:
            client.close()
    with session_scope() as session:
        rows = session.scalars(
            select(ConnectorConnection).where(ConnectorConnection.status != "DISCONNECTED").order_by(ConnectorConnection.created_at)
        ).all()
        return [connection_to_dict(r) for r in rows]


@router.get("/connections/{connection_id}")
def connection_detail(connection_id: str) -> dict:
    """One connected account: its state, what it has brought in, and its recent sync runs."""
    with session_scope() as session:
        row = session.get(ConnectorConnection, connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="connection not found")
        source_id = source_id_for(row)
        connection = connection_to_dict(row)
        counts = dict(
            session.execute(
                select(IngestionItem.status, func.count()).where(IngestionItem.source_id == source_id).group_by(IngestionItem.status)
            ).all()
        )
        items = session.scalars(
            select(IngestionItem).where(IngestionItem.source_id == source_id).order_by(IngestionItem.updated_at.desc()).limit(15)
        ).all()
        runs = session.scalars(
            select(PipelineRun).where(PipelineRun.source_id == source_id).order_by(PipelineRun.started_at.desc()).limit(8)
        ).all()
        from ingestion_pipeline.connectors.catalog import CATALOG

        name = next((e.name for e in CATALOG if e.provider == row.provider), row.provider)
        return {
            "name": name,
            "connection": connection,
            "stats": {"documents": counts.get("DONE", 0), "failed": counts.get("FAILED", 0), "in_progress": counts.get("NEW", 0) + counts.get("PROCESSING", 0)},
            "recent_items": [
                {
                    "item_id": i.item_id,
                    "file_name": (i.source_metadata or {}).get("file_name"),
                    "status": i.status,
                    "updated_at": i.updated_at.isoformat() if i.updated_at else None,
                }
                for i in items
            ],
            "recent_runs": [
                {
                    "run_id": r.run_id,
                    "items_identified": r.items_identified,
                    "items_processed": r.items_processed,
                    "items_failed": r.items_failed,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "duration_seconds": r.duration_seconds,
                }
                for r in runs
            ],
        }


@router.post("/connections/{connection_id}/sync")
def sync_now(connection_id: str) -> dict:
    """Queue a check for new data right away."""
    with session_scope() as session:
        row = session.get(ConnectorConnection, connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="connection not found")
        if row.status == "DISCONNECTED":
            raise HTTPException(status_code=409, detail="this account is disconnected")
        if row.status == "ERROR":
            raise HTTPException(status_code=409, detail="sign-in needs to be renewed before syncing")
        job_id = jobs.enqueue_scan(session, source_id_for(row))
    return {"queued": job_id is not None, "job_id": job_id}


class UpdateBody(BaseModel):
    enabled: bool


@router.post("/connections/{connection_id}")
def update_connection(connection_id: str, body: UpdateBody) -> dict:
    """Pause or resume syncing for one connection."""
    with session_scope() as session:
        row = session.get(ConnectorConnection, connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="connection not found")
        row.enabled = body.enabled
        return connection_to_dict(row)


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str) -> dict:
    """Disconnect: revoke it in Nango and stop syncing. Documents already ingested stay in the library."""
    with session_scope() as session:
        row = session.get(ConnectorConnection, connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="connection not found")
        nango_id, key = row.nango_connection_id, row.integration_key
    client = get_client()
    try:
        _nango_call(lambda: client.delete_connection(nango_id, key))
    finally:
        client.close()
    with session_scope() as session:
        row = session.get(ConnectorConnection, connection_id)
        if row is not None:
            row.status = "DISCONNECTED"
            row.enabled = False
    return {"ok": True}


@webhook_router.post("/api/nango/webhook")
async def nango_webhook(request: Request) -> dict:
    """Nango -> us. Public (Nango has no login) but every request must carry a valid HMAC signature.
    Free self-hosted Nango does not send webhooks; this is here for deployments that do."""
    raw = await request.body()
    if not verify_webhook_signature(raw, request.headers.get("x-nango-hmac-sha256"), get_settings().nango_secret_key):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    if payload.get("type") == "auth":
        client = get_client()
        try:
            with session_scope() as session:
                mirror_connections(session, client, kick_new=True)
        finally:
            client.close()
    logger.info("nango webhook handled: type=%s", payload.get("type"))
    return {"ok": True}
