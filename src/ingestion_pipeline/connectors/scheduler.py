"""Checks every enabled connected account for new data on a timer by queueing a scan job for it."""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from ingestion_pipeline.config import get_settings
from ingestion_pipeline.connectors.nango_client import NangoClient, NangoError
from ingestion_pipeline.connectors.service import mirror_connections, source_id_for
from ingestion_pipeline.core import jobs
from ingestion_pipeline.db.models import ConnectorConnection
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)


def poll_once(client: NangoClient | None = None) -> list[str]:
    """Refresh the connection list from Nango, then queue a scan for each active, enabled connection.
    Returns the source_ids that were queued (a scan already waiting for a source is not duplicated)."""
    own_client = client is None
    client = client or NangoClient()
    try:
        with session_scope() as session:
            mirror_connections(session, client)
        queued: list[str] = []
        with session_scope() as session:
            rows = session.scalars(
                select(ConnectorConnection).where(ConnectorConnection.status == "ACTIVE", ConnectorConnection.enabled.is_(True))
            ).all()
            for row in rows:
                source_id = source_id_for(row)
                if jobs.enqueue_scan(session, source_id):
                    queued.append(source_id)
        return queued
    finally:
        if own_client:
            client.close()


class ConnectorScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="connector-scheduler", daemon=True)

    def start(self) -> None:
        if not get_settings().nango_secret_key:
            logger.info("NANGO_SECRET_KEY not set: connector polling is off")
            return
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        interval = max(1, get_settings().connector_poll_minutes) * 60
        while not self._stop.is_set():
            try:
                queued = poll_once()
                if queued:
                    logger.info("connector poll queued %d scan(s)", len(queued))
            except NangoError as exc:
                logger.warning("connector poll skipped: %s", exc)
            except Exception:
                logger.exception("connector poll failed")
            self._stop.wait(interval)
