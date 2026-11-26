"""
Small REST client for a (self-hosted) Nango server. Nango does the OAuth sign-in and keeps the tokens fresh;
everything else (listing and fetching provider data) is done by our own code with the access token from
`get_connection`. Free self-hosted Nango has no syncs, actions or webhooks, so none of those are used here.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx

from ingestion_pipeline.config import get_settings


class NangoError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class NangoConnection:
    connection_id: str
    integration_key: str
    provider: str
    created: str | None
    has_errors: bool
    end_user_email: str | None


class NangoClient:
    def __init__(
        self,
        base_url: str | None = None,
        secret_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.nango_base_url).rstrip("/")
        self.secret_key = secret_key if secret_key is not None else settings.nango_secret_key
        if not self.secret_key:
            raise NangoError("NANGO_SECRET_KEY is not set (see docs/NANGO_SETUP.md)")
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.secret_key}"},
            transport=transport,
            timeout=timeout,
        )

    # ------------------------------------------------------------------ plumbing

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise NangoError(f"Cannot reach Nango at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else str(body.get("error", ""))
            except Exception:  # noqa: BLE001
                detail = response.text[:200]
            raise NangoError(f"Nango answered {response.status_code}: {detail}", response.status_code)
        return response.json() if response.content else {}

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------- catalog

    def list_integrations(self) -> list[dict]:
        """Integrations configured in this Nango (each is a provider with OAuth app credentials)."""
        return self._request("GET", "/integrations").get("data", [])

    def list_providers(self) -> list[dict]:
        """Every provider Nango knows how to authorize against."""
        return self._request("GET", "/providers").get("data", [])

    # ------------------------------------------------------------------- sessions

    def create_connect_session(
        self, user_id: str, email: str | None = None, allowed_integrations: list[str] | None = None
    ) -> dict:
        """A short-lived token the browser uses to open Nango's Connect screen."""
        body: dict[str, Any] = {"end_user": {"id": user_id, **({"email": email} if email else {})}}
        if allowed_integrations:
            body["allowed_integrations"] = allowed_integrations
        return self._request("POST", "/connect/sessions", json=body)["data"]

    # ---------------------------------------------------------------- connections

    def list_connections(self) -> list[NangoConnection]:
        rows = self._request("GET", "/connections").get("connections", [])
        return [
            NangoConnection(
                connection_id=row["connection_id"],
                integration_key=row["provider_config_key"],
                provider=row.get("provider", ""),
                created=row.get("created"),
                has_errors=bool(row.get("errors")),
                end_user_email=(row.get("end_user") or {}).get("email"),
            )
            for row in rows
        ]

    def get_access_token(self, connection_id: str, integration_key: str) -> str:
        """A fresh OAuth access token for a connection (Nango refreshes it when needed)."""
        data = self._request("GET", f"/connections/{connection_id}", params={"provider_config_key": integration_key})
        token = (data.get("credentials") or {}).get("access_token")
        if not token:
            raise NangoError(f"Connection {connection_id} has no access token")
        return token

    def delete_connection(self, connection_id: str, integration_key: str) -> None:
        self._request("DELETE", f"/connections/{connection_id}", params={"provider_config_key": integration_key})


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret_key: str) -> bool:
    """Nango signs webhooks with the hex HMAC-SHA256 of the raw body, in the X-Nango-Hmac-Sha256 header."""
    if not signature or not secret_key:
        return False
    expected = hmac.new(secret_key.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())
