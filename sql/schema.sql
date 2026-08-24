-- ============================================================================
-- Ingestion Pipeline — schema DDL
-- Mirrors src/ingestion_pipeline/db/models.py. If you change a model, update
-- this file to match -- this is what docker-compose applies on first boot.
-- Every statement is safely re-runnable.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ingestion_items (
    item_id         TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    content_type    TEXT NOT NULL
        CHECK (content_type IN ('DOCUMENT', 'TABULAR', 'TRANSCRIPT', 'MESSAGE', 'STRUCTURED_RECORD')),
    sha256_hash     TEXT,
    status          TEXT NOT NULL DEFAULT 'NEW'
        CHECK (status IN ('NEW', 'PROCESSING', 'DONE', 'FAILED', 'DUPLICATE')),
    storage_ref     JSONB,
    source_metadata JSONB,
    error_reason    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingestion_items_source_status ON ingestion_items (source_id, status);
CREATE INDEX IF NOT EXISTS idx_ingestion_items_hash ON ingestion_items (sha256_hash);

CREATE TABLE IF NOT EXISTS dedup_registry (
    sha256_hash   TEXT PRIMARY KEY,
    item_id       TEXT NOT NULL REFERENCES ingestion_items(item_id),
    source_id     TEXT NOT NULL,
    file_name     TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dedup_registry_source ON dedup_registry (source_id);

CREATE TABLE IF NOT EXISTS connector_checkpoints (
    source_id    TEXT PRIMARY KEY,
    cursor_value TEXT,
    cursor_meta  JSONB,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_events (
    event_id     BIGSERIAL PRIMARY KEY,
    topic        TEXT NOT NULL,
    item_id      TEXT REFERENCES ingestion_items(item_id),
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_events_topic_created ON ingestion_events (topic, created_at);

CREATE TABLE IF NOT EXISTS event_consumer_offsets (
    consumer_name TEXT NOT NULL,
    topic         TEXT NOT NULL,
    last_event_id BIGINT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer_name, topic)
);

-- embedding uses HALFVEC, not VECTOR: pgvector's HNSW/IVFFlat indexes cap out
-- at 2000 dimensions for the plain `vector` type, and text-embedding-3-large
-- produces 3072-dim vectors. `halfvec` (half-precision floats, pgvector >=
-- 0.7.0) supports HNSW indexing up to 4000 dimensions, so it fits. Slight
-- precision tradeoff vs full-precision vector, negligible for cosine-similarity
-- search, and it uses half the storage.
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id    TEXT PRIMARY KEY,
    item_id     TEXT NOT NULL REFERENCES ingestion_items(item_id),
    chunk_index INT NOT NULL,
    model       TEXT NOT NULL,
    body_text   TEXT,
    embedding   HALFVEC(3072),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (item_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_item ON chunk_embeddings (item_id);

-- Vector similarity index -- halfvec_cosine_ops (not vector_cosine_ops) since
-- the column is halfvec. Requires pgvector >= 0.7.0; the pgvector/pgvector:pg16
-- image already used by this project ships a recent enough version.
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vec
    ON chunk_embeddings
    USING hnsw (embedding halfvec_cosine_ops);

CREATE TABLE IF NOT EXISTS tabular_rows (
    row_id     BIGSERIAL PRIMARY KEY,
    item_id    TEXT NOT NULL REFERENCES ingestion_items(item_id),
    row_index  INT NOT NULL,
    row_data   JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (item_id, row_index)
);

CREATE INDEX IF NOT EXISTS idx_tabular_rows_item ON tabular_rows (item_id);

-- One row per orchestrator.run() call -- makes run history queryable per
-- source_id instead of only visible as a single end-of-run log line.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id                   TEXT PRIMARY KEY,
    source_id                TEXT NOT NULL,
    items_identified         INT NOT NULL DEFAULT 0,
    items_processed          INT NOT NULL DEFAULT 0,
    items_failed             INT NOT NULL DEFAULT 0,
    items_skipped_duplicate  INT NOT NULL DEFAULT 0,
    items_dead_lettered      INT NOT NULL DEFAULT 0,
    started_at               TIMESTAMPTZ NOT NULL,
    duration_seconds         NUMERIC NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_source_started ON pipeline_runs (source_id, started_at);

CREATE TABLE IF NOT EXISTS dead_letter_items (
    id             TEXT PRIMARY KEY,
    item_id        TEXT NOT NULL REFERENCES ingestion_items(item_id),
    source_id      TEXT NOT NULL,
    stage_failed   TEXT NOT NULL,
    error_message  TEXT NOT NULL,
    attempt_count  INT NOT NULL DEFAULT 0,
    failed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    requeued_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_items_item ON dead_letter_items (item_id);
