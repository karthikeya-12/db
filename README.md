# Ingestion Pipeline

Single-source (phase 1: user file uploads) ingestion pipeline: fetch -> archive
-> dedup -> normalize -> chunk -> embed -> persist -> publish, built so a
second source can be added later by writing a new Adapter only — no changes
to the orchestrator or any other shared component.

## Stack

- Python 3.12, SQLAlchemy 2.0 with the **psycopg (v3)** driver — no psycopg2 anywhere in this codebase
- PostgreSQL + pgvector for state, dedup, checkpoints, chunk embeddings, structured rows
- Local filesystem BlobStore for dev (an S3-backed BlobStore is a drop-in swap behind the same interface, built alongside the real S3 adapter)

## What's built in this slice (today)

1. Source Adapter contract + orchestrator — `core/adapter.py`, `core/orchestrator.py`
2. Postgres schema — `sql/schema.sql`, mirrored in `db/models.py`
3. Local dev environment — `docker-compose.yml`
4. BlobStore abstraction — `core/blobstore.py`
5. Chunking strategy framework (word-window / sentence-boundary / thread-level) — `core/chunker.py`
6. Embedder with cache-before-embed — `core/embedder.py`
7. Event publishing (outbox pattern) — `core/events.py`
8. Secrets access module — `config.py::SecretsProvider`
9. Retry policy + dead-letter queue — `core/retry.py`
10. Structured logging + per-run metrics — `core/logging_config.py`, `core/metrics.py`
11. PDF/DOCX extraction tooling spike — `spikes/pdf_extraction/`

12. Docling-based Markdown conversion for DOCUMENT content — `core/normalizer.py::DoclingDocumentNormalizer`, plus a `clean_markdown()` cleaning pass (mojibake repair + page-furniture stripping). Requires the optional `docling` extra (`pip install -e ".[docling]"`); falls back to a clear-error stub if not installed. NOTE: Docling's exact current API/packaging was not verified against live docs in this session (web search was unavailable throughout) — built against training-era knowledge of its interface; flagged in the code as the one place to fix if the API has moved.
13. Retry policy wired in — `core/retry.py::call_with_retry` now wraps the two external calls (`adapter.fetch`, `embedder.embed_missing`) in `core/orchestrator.py`, auto-translating transient errors (timeouts/connection/OS errors) to `RetryableError`; anything else fails immediately as non-retryable.
14. Event payload completed — `item.ingested` events now include `storage_ref` and `ingested_at`, matching the task spec's minimum payload.
15. Metrics persistence — `pipeline_runs` table (`sql/schema.sql`, `db/models.py::PipelineRun`) stores one row per `orchestrator.run()` call, so run history is queryable per source_id instead of only visible in the end-of-run log line.
16. PDF/DOCX extraction spike run — `spikes/pdf_extraction/comparison_report.md` has real results (from a synthetic sample set — see the report's caveat section) and a recommendation.
17. Entity/relationship extraction + Neo4j — `consumers/entity_relationship_consumer.py` polls the `item.ingested` event outbox (independent of the core orchestrator), extracts entities/relationships via an LLM (`core/entity_extractor.py`), and writes them to Neo4j (`core/graph_store.py`). Requires the optional `graph` extra (`pip install -e ".[graph]"`) and your own Neo4j instance's connection details in `.env`. Run with `python -m ingestion_pipeline.consumers.entity_relationship_consumer`.
    - Deliberately simplified vs. everest_repo's kg_zone2/kg_zone3 approach in one specific way: entities are deduped by (name, type) only — there's no mapping step against an external concept/entity registry, since this pipeline has no equivalent of Everest's proprietary vocabulary. See `core/entity_extractor.py`'s docstring for that scope decision.
    - Everything else is reused directly rather than re-derived: `core/entity_extractor.py::validate_extraction()` ports everest_repo's `signal_extraction.py::validate_extraction` typed-findings approach (rule_id/severity/detail) including its verbatim-evidence check — every evidence span an LLM returns is checked against the actual source text, and anything that fails (missing fields, bad type, non-verbatim evidence) is dropped before it ever reaches Neo4j. `core/graph_store.py` ports everest_repo's `kg_zone3/worker/neo4j/chunk_loader.py::load_via_driver` write pattern: a uniqueness constraint declared once, batched `UNWIND`+`MERGE` writes (one round-trip per item instead of one per entity/relationship), and returned driver write-counters (`nodes_created`/`relationships_created`/`properties_set`) instead of counting client-side.

**Deliberately not built yet (next up):**
- The real S3 adapter — `adapters/local_folder_adapter.py` is a local-filesystem stand-in used only to prove the orchestrator runs end-to-end without needing AWS credentials yet.
- Automatic OAuth token-refresh integration into `SecretsProvider.get_secret()` — the manual seam (`register_refresher`/`refresh()`) exists, but wiring it to trigger automatically on token expiry was deferred since no OAuth-based adapter exists yet to actually exercise it against (see `config.py`'s `SecretsProvider` docstring).

## Setup

Postgres + pgvector for this project comes from a **shared container** (`shared-postgres`), not from this project's own `docker-compose.yml`. The idea: one long-lived pgvector-enabled Postgres server, reused across every local project, not just this one. This project only owns its own database/role inside that shared server.

### 1. Start the shared Postgres container (one-time, outside this project)

```bash
docker pull pgvector/pgvector:pg16
docker volume create shared_pgvector_data
docker run -d --name shared-postgres --restart unless-stopped   -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres   -p 5433:5432   -v shared_pgvector_data:/var/lib/postgresql/data   pgvector/pgvector:pg16
```

If `shared-postgres` already exists from another project, skip straight to step 2 — reuse it.

### 2. Create this project's role, database, extension, and schema (via pgAdmin)

Connect pgAdmin to the shared container first:

| Field | Value |
|---|---|
| Host | localhost |
| Port | 5433 |
| Maintenance database | postgres |
| Username | postgres |
| Password | postgres |

Then, against that server:

1. Login/Group Role -> create `ingestion` with password `ingestion`, "Can login?" = Yes.
2. Database -> create `ingestion_pipeline`, owner = `ingestion`.
3. Open a Query Tool on the new `ingestion_pipeline` database and run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
4. Run `sql/schema.sql` (this repo) against `ingestion_pipeline` — Query Tool -> open file -> Execute. It's idempotent, safe to re-run.

### 3. Point this project at it and install deps

```bash
cp .env.sample .env        # fill in EMBEDDING_API_KEY if you have one
                            # DATABASE_URL default already targets postgresql+psycopg://ingestion:ingestion@localhost:5433/ingestion_pipeline
pip install -e ".[dev]"
```

### Fallback: fully self-contained local Postgres (no shared container)

If you'd rather not use the shared container, this project's `docker-compose.yml` still has its own Postgres service, opt-in via a profile so it never starts by accident:

```bash
docker compose --profile standalone up -d   # starts Postgres + pgvector on host port 5434
docker compose --profile standalone down     # stop, keep data
docker compose --profile standalone down -v   # stop AND wipe the data volume
```

Point `DATABASE_URL` at port **5434** instead of 5433 if you use this path — schema.sql is auto-applied on that container's first boot only.

## Run the demo pipeline locally (no AWS needed)

```bash
mkdir -p data/incoming
cp some_file.csv data/incoming/      # .csv, .xlsx, .txt, .md all work with the demo adapter
python -m ingestion_pipeline.cli
```

Re-running is safe — already-processed files are skipped by the dedup check,
not reprocessed.

## Run tests

```bash
pytest
```

Tests cover pure logic only (chunker, dedup hashing) — no database required.

## Project layout

```
src/ingestion_pipeline/
  config.py                  # Settings + SecretsProvider (every credential lookup goes through this)
  db/
    session.py                 # SQLAlchemy engine/session -- psycopg v3, not psycopg2
    models.py                  # ORM models -- source of truth for schema shape
  core/
    types.py                    # RawItem, Chunk, ContentType
    adapter.py                   # The contract every source adapter implements
    orchestrator.py               # The ONLY place that knows the pipeline's stage order
    blobstore.py                   # Raw-bytes archive abstraction
    dedup.py                        # SHA-256 fingerprint + dedup_registry check
    checkpoint.py                    # connector_checkpoints get/set
    chunker.py                        # Word-window / sentence-boundary / thread-level strategies
    embedder.py                        # Cache-before-embed
    events.py                           # Outbox publish / poll / acknowledge
    retry.py                             # Retry policy + dead-letter queue
    normalizer.py                         # DOCUMENT->Markdown (not yet implemented) + TABULAR row parser
    logging_config.py                     # Structured JSON logging
    metrics.py                             # Per-run metrics
  adapters/
    local_folder_adapter.py                # Demo adapter -- proves the orchestrator without AWS
  cli.py                                    # Local run entrypoint
sql/schema.sql                               # Idempotent DDL, applied by docker-compose on first boot
spikes/pdf_extraction/                       # Task 11 -- extraction tooling comparison harness
tests/                                       # Unit tests for pure-logic pieces
```

## Reused vs. built fresh

- **Reused (ported to SQLAlchemy, not psycopg2):** the dedup fingerprint pattern (`core/dedup.py`), the chunk split-point priority logic — paragraph break > sentence end > hard cut — generalized from character-based to word-based splitting (`core/chunker.py::WordWindowChunker`), and the cache-before-embed pattern (`core/embedder.py`).
- **Built fresh — no prior equivalent:** the Adapter/orchestrator contract, the outbox event system, the secrets module, retry/dead-letter handling, and structured metrics.

No files outside this `ingestion_pipeline` folder were modified to build this.
