# Archive: document ingestion and chat

Bring documents in (uploads or connected apps), have them chunked, embedded and linked into a knowledge graph
automatically, then ask questions and get answers that cite their sources.

```
 Upload / Connectors (Nango OAuth + polling)
        │
        ▼
 Adapter ─▶ Orchestrator ─▶ dedup by content hash ─▶ archive ─▶ normalize (Docling / text) ─▶ Postgres + outbox event
                                                                                                   │
                                                                                                   ▼
                                                              Cognee consumer: chunk · embed (LanceDB) · entities → Neo4j
                                                                                                   │
                                                                                                   ▼
                              Chat: LangGraph (condense question → Cognee retrieval → OpenAI answer with [n] citations)
 Next.js UI  ◀──────────────────────────  FastAPI (cookie login, /api/*)  ◀─────────────────────────┘
```

## What it does

- **Library**: drag-and-drop upload of any format (PDF, Office, text, tables, ...), document list with status.
- **Connectors**: Gmail, Google Calendar, Google Drive, Slack, Notion, Outlook, OneDrive, Dropbox, GitHub, Jira, Confluence.
  OAuth goes through a self-hosted Nango; new data is found by polling every `CONNECTOR_POLL_MINUTES` and ingested automatically.
  See `docs/CONNECTORS.md` and `docs/NANGO_SETUP.md`.
- **Consult**: streaming chat over everything ingested; answers cite numbered sources, conversations are saved per user.
- **Ledger**: pipeline runs and failed items, with one-click retry.
- **Login**: accounts are created by an operator (no public sign-up).

Changed or re-uploaded documents are re-ingested as a new version; identical content is skipped.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/); Node 22+
- PostgreSQL 16 with pgvector
- **Neo4j Enterprise (or Aura) with the APOC plugin.** Cognee needs APOC. Use a dedicated database (`NEO4J_DATABASE=ingest`);
  creating one needs Enterprise: `CREATE DATABASE ingest`.
- An OpenAI API key (embeddings, entity extraction and answers)
- Docker, if you want Nango or the full-stack compose file

## Quick start (local development)

```bash
cp .env.sample .env          # fill in the values below
uv lock && uv sync           # after pulling dependency changes
uv run python scripts/cognee_smoke.py --create-db     # checks Postgres, Neo4j + APOC, OpenAI and a real ingest
uv run uvicorn ingestion_pipeline.api.main:app --port 8000

cd frontend && npm install && cp .env.local.example .env.local && npm run dev   # http://localhost:3000
```

Minimum `.env`:

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | Postgres (the schema in `sql/schema.sql` is applied automatically at startup) |
| `EMBEDDING_API_KEY` | OpenAI key, used for embeddings, entity extraction and chat |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | Knowledge graph |
| `AUTH_SECRET` | Signs login cookies. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | First user, created at startup when there are no users. Remove after first login |
| `NANGO_SECRET_KEY`, `NANGO_BASE_URL` | Connectors (see below) |

More users: `uv run python -m ingestion_pipeline.cli create-user someone@example.com`.
Every setting is documented in `.env.sample`.

## Connectors (self-hosted Nango)

```bash
uv run python scripts/setup_nango_env.py
docker compose -f deploy/nango/docker-compose.yml --env-file deploy/nango/.env up -d
uv run python scripts/nango_check.py
```

Then follow `docs/NANGO_SETUP.md` (dashboard, secret key) and `docs/CONNECTORS.md` (registering an OAuth app per provider).
The free self-hosted Nango provides OAuth and token refresh only, with no sync engine or webhooks, which is why the app polls.
Back up `NANGO_ENCRYPTION_KEY`: without it stored connections cannot be decrypted.

## Full stack in Docker

```bash
docker compose --profile stack up -d --build      # Postgres, Neo4j (APOC + `ingest` db), API, web UI on :3000
```

Needs `NEO4J_PASSWORD` and `NEO4J_ACCEPT_LICENSE=yes` in `.env` (Neo4j Enterprise licence). Nango stays a separate compose file.
This stack has not been run end to end by the authors; treat the first start as a smoke test.

## Tests and quality checks

```bash
uv run pytest                          # backend (embedded Postgres, mocked provider APIs, scripted chat agent)
cd frontend && npx tsc --noEmit && npm run build
uv run python scripts/run_eval.py      # answer quality against your real stack (a few cents of OpenAI usage)
```

`scripts/run_eval.py` ingests three small documents from `evals/corpus`, asks the questions in `evals/cases.json`, and checks
for the right facts, the right citation, and an honest "not found" for questions the archive cannot answer.

## Layout

```
src/ingestion_pipeline/
  api/            FastAPI app: main (items, uploads, runs), auth, connectors, chat
  adapters/       local folder, upload, Nango-backed connector adapter, registry
  connectors/     Nango client, catalog, polling scheduler, one fetcher per provider (providers/)
  core/           orchestrator, dedup, versions, job queue, normalizer, events (outbox), auth, Cognee setup + indexer
  consumers/      Cognee consumer: item.ingested events → chunk, embed, graph
  chat/           retrieval (Cognee), LangGraph agent, evaluation scoring
  db/             SQLAlchemy models (sql/schema.sql mirrors them)
frontend/         Next.js 16 + Tailwind 4 UI
deploy/nango/     self-hosted Nango compose
scripts/          cognee_smoke, nango_check, setup_nango_env, run_eval
evals/            chat evaluation corpus and cases
```

## Good to know

- Cognee owns chunking, embeddings and the entity graph. Our own `chunk_embeddings` table is no longer written to; it can be dropped.
- All connector tokens live in Nango; this app stores only connection metadata and per-connection sync cursors.
- Cognee's storage (vectors, metadata) is under `COGNEE_DATA_ROOT`; keep it on a persistent volume.
- The login throttle is in memory and per process; run one API instance, or put a rate limiter in front.
- Real provider fetchers (Gmail, Drive, Slack, Notion, Outlook, ...) are tested against mocked APIs. Expect to tune them on first contact with live accounts.
