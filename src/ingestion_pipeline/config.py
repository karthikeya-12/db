"""
Central settings + secrets access.

Every adapter and component reads configuration and credentials through this
module only -- nothing reads os.environ directly anywhere else in the
codebase. Swapping the local .env backend for a managed secrets service
later means changing SecretsProvider's implementation here, not every call
site that needs a credential.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Callable

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database (SQLAlchemy + psycopg v3 driver -- no psycopg2)
    database_url: str = "postgresql+psycopg://ingestion:ingestion@localhost:5433/ingestion_pipeline"

    # Blob storage
    blob_store_backend: str = "local"
    blob_store_local_root: str = "./data/blobstore"

    # S3 (used once the S3 adapter is built -- safe to leave blank locally)
    aws_region: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_raw_bucket: str = ""

    # Embedding
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-large"
    embedding_batch_size: int = 100

    # Retry policy
    retry_max_attempts: int = 5
    retry_base_seconds: float = 2.0

    # Chunking (word-window strategy -- DOCUMENT content)
    chunk_min_words: int = 400
    chunk_max_words: int = 500
    chunk_overlap_words: int = 50

    # Logging
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton -- loaded once per process."""
    return Settings()


class SecretsProvider:
    """
    Single place every adapter calls, by connector name, to get credentials.

    Local dev backend reads from Settings/.env. In any deployed environment,
    swap the lookup in get_secret() for a managed secrets service call --
    no adapter code changes, because adapters only ever call get_secret().

    A connector needing OAuth token refresh (email, calendar, Slack/Teams --
    none built yet) registers a refresher callable; get_secret() would call
    it automatically once the cached token is flagged expired. The seam
    exists now so the first OAuth-based adapter doesn't require touching
    this class.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._refreshers: dict[str, Callable[[], str]] = {}

    def get_secret(self, connector_name: str, key: str) -> str:
        """Return one credential value, e.g. get_secret('aws', 'access_key_id') -> reads settings.aws_access_key_id."""
        namespaced = f"{connector_name}_{key}".lower()
        if hasattr(self._settings, namespaced):
            return getattr(self._settings, namespaced)
        if hasattr(self._settings, key.lower()):
            return getattr(self._settings, key.lower())
        raise KeyError(f"No secret configured for connector={connector_name!r} key={key!r}")

    def register_refresher(self, connector_name: str, refresher: Callable[[], str]) -> None:
        """Register a callable returning a fresh access token for connector_name."""
        self._refreshers[connector_name] = refresher

    def refresh(self, connector_name: str) -> str:
        """Force a token refresh for an OAuth connector. Raises if none registered."""
        if connector_name not in self._refreshers:
            raise NotImplementedError(f"No token refresher registered for connector={connector_name!r}")
        return self._refreshers[connector_name]()


@lru_cache
def get_secrets_provider() -> SecretsProvider:
    return SecretsProvider()
