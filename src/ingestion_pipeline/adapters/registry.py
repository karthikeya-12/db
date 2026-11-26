"""
Maps a source_id to a factory that builds its orchestrator. The job worker uses this to
turn a queued job ("scan source X") into a runnable pipeline, so new sources (uploads,
Nango connections, ...) register themselves here and nothing else needs to change.
"""
from __future__ import annotations

from typing import Callable

from ingestion_pipeline.core.adapter import Adapter

AdapterFactory = Callable[[str], Adapter]  # source_id -> Adapter

_FACTORIES: dict[str, AdapterFactory] = {}
_PREFIX_FACTORIES: dict[str, AdapterFactory] = {}


def register_adapter(source_id: str, factory: AdapterFactory) -> None:
    _FACTORIES[source_id] = factory


def register_adapter_prefix(prefix: str, factory: AdapterFactory) -> None:
    """For families like 'nango:<connection>' where each connection is its own source_id."""
    _PREFIX_FACTORIES[prefix] = factory


def build_adapter(source_id: str) -> Adapter:
    if source_id in _FACTORIES:
        return _FACTORIES[source_id](source_id)
    for prefix, factory in _PREFIX_FACTORIES.items():
        if source_id.startswith(prefix):
            return factory(source_id)
    raise KeyError(f"No adapter registered for source_id={source_id!r}")


def build_orchestrator(source_id: str):
    from ingestion_pipeline.core.blobstore import get_blob_store
    from ingestion_pipeline.core.orchestrator import PipelineOrchestrator

    return PipelineOrchestrator(
        adapter=build_adapter(source_id), blob_store=get_blob_store()
    )


def build_nango_adapter(source_id: str) -> Adapter:
    """Factory for every 'nango:<integration>:<connection>' source."""
    from sqlalchemy import select

    from ingestion_pipeline.adapters.nango_adapter import NangoAdapter, parse_source_id
    from ingestion_pipeline.config import get_settings
    from ingestion_pipeline.connectors.nango_client import NangoClient
    from ingestion_pipeline.connectors.providers import get_fetcher
    from ingestion_pipeline.db.models import ConnectorConnection
    from ingestion_pipeline.db.session import session_scope

    integration_key, connection_id = parse_source_id(source_id)
    with session_scope() as session:
        row = session.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.integration_key == integration_key,
                ConnectorConnection.nango_connection_id == connection_id,
            )
        )
        provider = row.provider if row else integration_key
    settings = get_settings()
    return NangoAdapter(
        source_id,
        NangoClient(),
        get_fetcher(provider),
        limit=settings.connector_max_items_per_scan,
        # Gmail always uses the "newest not yet ingested" listing, so raising or removing the cap reaches older mail too.
        max_total=(settings.gmail_max_mails or 10**9) if provider == "google-mail" else 0,
        initial_days=settings.connector_initial_days,
    )


register_adapter_prefix("nango:", build_nango_adapter)
