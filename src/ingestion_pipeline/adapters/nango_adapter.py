"""
Adapter for one connected account (a Nango connection). Nango supplies a fresh OAuth access token; a
provider fetcher (connectors/providers) reads the provider's API with it. The orchestrator does the rest.
"""
from __future__ import annotations

import time

from ingestion_pipeline.connectors.nango_client import NangoClient
from ingestion_pipeline.connectors.providers.base import ChangeRef, ProviderFetcher
from ingestion_pipeline.core.adapter import Adapter
from ingestion_pipeline.core.types import RawItem

_TOKEN_TTL_SECONDS = 60


def parse_source_id(source_id: str) -> tuple[str, str]:
    """'nango:<integration_key>:<connection_id>' -> (integration_key, connection_id)."""
    parts = source_id.split(":", 2)
    if len(parts) != 3 or parts[0] != "nango" or not parts[1] or not parts[2]:
        raise ValueError(f"Not a Nango source id: {source_id!r}")
    return parts[1], parts[2]


class NangoAdapter(Adapter):
    def __init__(
        self,
        source_id: str,
        client: NangoClient,
        fetcher: ProviderFetcher,
        limit: int = 100,
        initial_days: int = 30,
        max_total: int = 0,
    ) -> None:
        self.source_id = source_id
        self._integration_key, self._connection_id = parse_source_id(source_id)
        self._client = client
        self._fetcher = fetcher
        self._limit = limit
        self._initial_days = initial_days
        self._max_total = max_total  # 0 = no cap on how many items this account may contribute
        self._token: tuple[str, float] | None = None

    def _access_token(self) -> str:
        if self._token and time.monotonic() - self._token[1] < _TOKEN_TTL_SECONDS:
            return self._token[0]
        token = self._client.get_access_token(self._connection_id, self._integration_key)
        self._token = (token, time.monotonic())
        return token

    def _already_known_external_ids(self) -> set[str]:
        from sqlalchemy import select

        from ingestion_pipeline.db.models import IngestionItem
        from ingestion_pipeline.db.session import session_scope

        prefix = f"{self.source_id}/"
        with session_scope() as session:
            ids = session.scalars(select(IngestionItem.item_id).where(IngestionItem.source_id == self.source_id)).all()
        return {i[len(prefix):] for i in ids if i.startswith(prefix)}

    def list_changes(self, cursor: str | None) -> list[ChangeRef]:
        if self._max_total > 0:
            known = self._already_known_external_ids()
            remaining = self._max_total - len(known)
            if remaining <= 0:
                return []  # the cap is reached; raising it makes the next scan pick up the next-newest items
            capped = self._fetcher.list_latest(self._access_token(), min(self._limit, remaining), self._initial_days, known)
            if capped is not None:
                return capped
        return self._fetcher.list_changes(self._access_token(), cursor, self._limit, self._initial_days)

    def fetch(self, ref: ChangeRef) -> RawItem:
        doc = self._fetcher.fetch(self._access_token(), ref)
        return RawItem(
            source_id=self.source_id,
            item_id=f"{self.source_id}/{ref.external_id}",
            content_type=doc.content_type,
            raw_bytes=doc.raw_bytes,
            source_metadata=doc.metadata,
            file_name=doc.file_name,
        )

    def advance_cursor(self, ref: ChangeRef) -> str:
        return ref.cursor
