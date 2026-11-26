-- ============================================================================
-- Ingestion Pipeline — schema DDL
-- Mirrors src/ingestion_pipeline/db/models.py. If you change a model, update
-- this file to match -- this is what docker-compose applies on first boot.
-- Every statement is safely re-runnable.
-- ============================================================================

-- pgvector is no longer needed: chunk embeddings and the vector index live in Cognee (LanceDB).
-- An existing database keeps its old (now unused) chunk_embeddings table untouched.

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


-- ===========================================================================
-- Phase 0 additions: item versioning + job queue (idempotent)
-- ===========================================================================

-- Which version of an item is current; bumped each time changed content is ingested.
ALTER TABLE ingestion_items ADD COLUMN IF NOT EXISTS current_version INT NOT NULL DEFAULT 0;

-- One row per ingested version of an item. (item_id, sha256_hash) unique means
-- "this item's latest version has exactly this content" == duplicate, regardless of what
-- other items contain. Replaces the global dedup_registry lookup (that table is
-- kept for existing databases but is no longer read or written).
CREATE TABLE IF NOT EXISTS ingestion_item_versions (
    item_id      TEXT NOT NULL REFERENCES ingestion_items(item_id),
    version      INT  NOT NULL,
    sha256_hash  TEXT NOT NULL,
    storage_ref  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, version)
);

-- Earlier revisions of this file declared UNIQUE (item_id, sha256_hash); an A -> B -> A change needs that gone.
ALTER TABLE ingestion_item_versions DROP CONSTRAINT IF EXISTS ingestion_item_versions_item_id_sha256_hash_key;

-- Work queue drained by a single worker (core/jobs.py). Claimed with
-- FOR UPDATE SKIP LOCKED, and never two RUNNING jobs for the same source.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id       TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('scan', 'retry_item')),
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')),
    attempts     INT NOT NULL DEFAULT 0,
    error        TEXT,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs (status, run_after);
-- At most one queued scan per source: a burst of triggers collapses into one job.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_jobs_one_queued_scan
    ON ingestion_jobs (source_id) WHERE kind = 'scan' AND status = 'QUEUED';

-- Application users (login). Accounts are created by an operator (CLI or ADMIN_EMAIL/ADMIN_PASSWORD seed);
-- there is no public sign-up.
CREATE TABLE IF NOT EXISTS app_users (
    user_id        TEXT PRIMARY KEY,
    email          TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_users_email ON app_users (lower(email));

-- Connections made through Nango (one row per connected account). Mirrors Nango's list; also holds our own
-- settings (enabled) and polling state (last_synced_at, last_error).
CREATE TABLE IF NOT EXISTS connector_connections (
    id                   TEXT PRIMARY KEY,
    nango_connection_id  TEXT NOT NULL,
    integration_key      TEXT NOT NULL,
    provider             TEXT NOT NULL,
    display_name         TEXT,
    status               TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'ERROR', 'DISCONNECTED')),
    enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    last_synced_at       TIMESTAMPTZ,
    last_error           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (nango_connection_id, integration_key)
);

-- Chat: conversations belong to a user; messages keep the cited sources of each assistant answer.
CREATE TABLE IF NOT EXISTS chat_conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES app_users (user_id) ON DELETE CASCADE,
    title       TEXT NOT NULL DEFAULT 'New conversation',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chat_conversations_user ON chat_conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id               BIGSERIAL PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES chat_conversations (id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content          TEXT NOT NULL,
    sources          JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation ON chat_messages (conversation_id, id);
