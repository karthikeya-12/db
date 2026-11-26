"""
Check that the self-hosted Nango server is up and that NANGO_SECRET_KEY works.

    uv run python scripts/nango_check.py
"""
from __future__ import annotations

import sys

import httpx

from ingestion_pipeline.config import get_settings


def main() -> int:
    settings = get_settings()
    base = settings.nango_base_url.rstrip("/")
    print(f"Nango at {base}")
    try:
        health = httpx.get(f"{base}/health", timeout=10)
    except httpx.HTTPError as exc:
        print(f"  FAIL  cannot reach it: {exc}\n        is the stack running? docker compose -f deploy/nango/docker-compose.yml --env-file deploy/nango/.env up -d")
        return 1
    print(f"  PASS  server answers /health ({health.status_code})")

    if not settings.nango_secret_key:
        print("  FAIL  NANGO_SECRET_KEY is empty in .env (copy it from the Nango dashboard: Environment Settings)")
        return 1
    response = httpx.get(
        f"{base}/integrations", headers={"Authorization": f"Bearer {settings.nango_secret_key}"}, timeout=10
    )
    if response.status_code == 401:
        print("  FAIL  the secret key was rejected (wrong environment, or copied incompletely)")
        return 1
    if not response.is_success:
        print(f"  FAIL  unexpected answer {response.status_code}: {response.text[:200]}")
        return 1
    integrations = response.json().get("data", [])
    print(f"  PASS  secret key accepted; {len(integrations)} integration(s) configured: {[i.get('unique_key') for i in integrations]}")

    # Same calls the API makes: a connect session and the connections list.
    from ingestion_pipeline.connectors.nango_client import NangoClient, NangoError

    client = NangoClient()
    try:
        session = client.create_connect_session(user_id="check-script", email="check@example.com")
        print(f"  PASS  can create a connect session (expires {session.get('expires_at')})")
        connections = client.list_connections()
        print(f"  PASS  connections list readable ({len(connections)} connected account(s))")
    except NangoError as exc:
        print(f"  FAIL  {exc}")
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
