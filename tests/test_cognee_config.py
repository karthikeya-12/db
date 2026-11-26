from ingestion_pipeline.config import Settings
from ingestion_pipeline.core.cognee_setup import build_cognee_config


def make(**overrides):
    base = dict(
        embedding_api_key="sk-test",
        embedding_model="text-embedding-3-large",
        neo4j_uri="neo4j://127.0.0.1:7687",
        neo4j_user="neo4j",
        neo4j_password="pw",
        neo4j_database="ingest",
        entity_extraction_model="gpt-4o-mini",
        cognee_data_root="./data/cognee",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_maps_existing_env_values_onto_cognee():
    config = build_cognee_config(make())
    assert config.env["GRAPH_DATABASE_PROVIDER"] == "neo4j"
    assert config.env["GRAPH_DATABASE_URL"] == "neo4j://127.0.0.1:7687"
    assert config.env["GRAPH_DATABASE_NAME"] == "ingest"
    assert config.env["GRAPH_DATABASE_PASSWORD"] == "pw"
    assert config.llm == {"llm_provider": "openai", "llm_model": "openai/gpt-4o-mini", "llm_api_key": "sk-test"}
    # the bare model name from an older .env gains the provider prefix Cognee/LiteLLM expects
    assert config.embedding["embedding_model"] == "openai/text-embedding-3-large"
    assert config.embedding["embedding_dimensions"] == 3072
    assert config.system_root.endswith("cognee/system") and config.data_root.endswith("cognee/data")


def test_explicit_cognee_values_win_over_fallbacks():
    config = build_cognee_config(make(cognee_llm_model="anthropic/claude-x", cognee_llm_api_key="sk-other"))
    assert config.llm["llm_model"] == "anthropic/claude-x"
    assert config.llm["llm_api_key"] == "sk-other"
