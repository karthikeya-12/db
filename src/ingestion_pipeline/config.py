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

    # Neo4j -- used only by the entity/relationship consumer (consumers/entity_relationship_consumer.py),
    # never by the core ingestion pipeline itself. Fill in your own instance's details.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = ""  # Neo4j database all graph data is written to ("" -> the server default)

    # Entity/relationship extraction (separate from the embedding model above --
    # this is a chat-completion call, not an embedding call)
    entity_extraction_model: str = "gpt-4o-mini"

    # Cognee -- owns chunking, embeddings, entity/relationship extraction and graph + vector
    # indexing (see core/cognee_setup.py). Every value falls back to an existing setting above,
    # so a .env written before Cognee was added keeps working.
    cognee_llm_model: str = ""  # empty -> "openai/<entity_extraction_model>"
    cognee_llm_api_key: str = ""  # empty -> embedding_api_key (same OpenAI key)
    embedding_dimensions: int = 3072  # text-embedding-3-large
    cognee_data_root: str = "./data/cognee"  # Cognee's own metadata DB, vector store and ingested copies

    # Chat (chat/agent.py): the model that writes answers, and how much it reads per question.
    chat_model: str = "gpt-4o-mini"
    chat_top_k: int = 8  # passages retrieved per question
    chat_history_turns: int = 6  # earlier messages given to the model for follow-up questions
    chat_use_graph: bool = True  # also pull related facts from the knowledge graph

    # Local-folder watcher (watcher.py) -- the folder cli.py and watcher.py both
    # point LocalFolderAdapter at; kept here so both entrypoints agree without
    # hardcoding the path twice.
    watch_folder_path: str = "./data/incoming"
    upload_folder_path: str = "./data/uploads"
    upload_max_mb: int = 50

    # REST API (api/main.py) -- comma-separated list of origins allowed to call
    # it; the ingestion_pipeline_ui frontend runs on the Vite dev server by default.
    api_cors_origins: str = "http://localhost:3000"

    # Authentication (api/auth.py). AUTH_SECRET signs session cookies: set a long random value
    # (python -c "import secrets; print(secrets.token_urlsafe(48))"). If empty, a random one is generated at
    # startup and everyone is signed out on each restart.
    auth_secret: str = ""
    auth_session_hours: int = 72
    auth_cookie_secure: bool = False  # set true when served over HTTPS
    # Self-service sign-up on the login page. Anyone who can sign up can read everything in the archive, so for anything
    # beyond local use set SIGNUP_INVITE_CODE (people must enter it) or ALLOW_SIGNUP=false.
    allow_signup: bool = True
    signup_invite_code: str = ""
    # Optional first-user seed: created at startup when no users exist yet.
    admin_email: str = ""
    admin_password: str = ""

    # Self-hosted Nango (deploy/nango, docs/NANGO_SETUP.md). The secret key comes from the Nango
    # dashboard (Environment Settings); the API uses it to list connections and fetch synced records.
    nango_base_url: str = "http://localhost:3003"
    nango_secret_key: str = ""
    # How connectors look for new data (free Nango has no sync engine, so we poll each connected account).
    connector_poll_minutes: int = 10
    connector_initial_days: int = 30  # first sync of a new connection only reaches back this far
    gmail_max_mails: int = 100  # most Gmail messages to ingest per connected account (newest first); 0 = no limit
    connector_max_items_per_scan: int = 100  # bigger backlogs are worked through over several scans

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
