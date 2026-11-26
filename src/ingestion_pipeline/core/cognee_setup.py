"""
Single place that configures Cognee from this project's Settings and hands out the `cognee` module.

Why a wrapper instead of `import cognee` everywhere:

  * Cognee reads configuration from the process environment AND loads this project's `.env` into
    it at import time (override=True). Its variable names overlap ours (EMBEDDING_MODEL,
    EMBEDDING_API_KEY, ...), and our `.env` has `EMBEDDING_MODEL=text-embedding-3-large` without the
    provider prefix Cognee expects. So the values that matter are pushed through Cognee's own
    config API after import, where they win.
  * Graph/relational/vector selection has to be in the environment BEFORE import, because Cognee
    derives its per-provider defaults when its config objects are first built.

Storage layout: Neo4j holds the knowledge graph; Cognee keeps vectors (LanceDB) and its own
bookkeeping (SQLite) under settings.cognee_data_root. Our application tables stay in Postgres.

ENABLE_BACKEND_ACCESS_CONTROL defaults to "false" here. Cognee's default is per-dataset database
isolation, which needs Neo4j Enterprise/Aura (CREATE DATABASE) and fails on Community edition.
Access control for this app is handled by our own login layer, so all datasets share one graph;
datasets are still separate logical units (one per connector, one for uploads).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from ingestion_pipeline.config import Settings, get_settings


@dataclass(frozen=True)
class CogneeConfig:
    env: dict[str, str]  # must be in os.environ before `import cognee`
    llm: dict  # cognee.config.set_llm_config(...)
    embedding: dict  # cognee.config.set_embedding_config(...)
    system_root: str
    data_root: str
    notes: list[str] = field(default_factory=list)


def _provider_model(model: str) -> str:
    """LiteLLM wants "<provider>/<model>"; accept a bare OpenAI model name from older .env files."""
    return model if "/" in model else f"openai/{model}"


def build_cognee_config(settings: Settings) -> CogneeConfig:
    """Pure function (no imports, no I/O) so the mapping is unit-testable."""
    root = Path(settings.cognee_data_root).expanduser().resolve()
    api_key = settings.cognee_llm_api_key or settings.embedding_api_key
    llm_model = _provider_model(settings.cognee_llm_model or settings.entity_extraction_model)

    return CogneeConfig(
        env={
            "GRAPH_DATABASE_PROVIDER": "neo4j",
            "GRAPH_DATABASE_URL": settings.neo4j_uri,
            "GRAPH_DATABASE_USERNAME": settings.neo4j_user,
            "GRAPH_DATABASE_PASSWORD": settings.neo4j_password,
            "GRAPH_DATABASE_NAME": settings.neo4j_database,
            "VECTOR_DB_PROVIDER": "lancedb",
            "DB_PROVIDER": "sqlite",
            "TELEMETRY_DISABLED": "1",
        },
        llm={"llm_provider": "openai", "llm_model": llm_model, "llm_api_key": api_key},
        embedding={
            "embedding_provider": "openai",
            "embedding_model": _provider_model(settings.embedding_model),
            "embedding_dimensions": settings.embedding_dimensions,
            "embedding_api_key": settings.embedding_api_key,
            "embedding_batch_size": settings.embedding_batch_size,
        },
        system_root=str(root / "system"),
        data_root=str(root / "data"),
    )


_lock = Lock()
_cognee = None


def get_cognee():
    """Return the configured `cognee` module (configures it on first use, once per process)."""
    global _cognee
    with _lock:
        if _cognee is not None:
            return _cognee

        config = build_cognee_config(get_settings())
        os.environ.update(config.env)
        os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")

        try:
            import cognee
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError(
                "cognee is not installed -- run `uv sync` (it is a core dependency of this project)."
            ) from exc

        Path(config.system_root).mkdir(parents=True, exist_ok=True)
        Path(config.data_root).mkdir(parents=True, exist_ok=True)
        cognee.config.system_root_directory(config.system_root)
        cognee.config.data_root_directory(config.data_root)
        cognee.config.set_llm_config(config.llm)
        cognee.config.set_embedding_config(config.embedding)

        _cognee = cognee
        return _cognee
