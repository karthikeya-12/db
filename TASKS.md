# Build checklist

Source of truth for progress. Ticked as each task is finished and verified.
Progress: 30 / 30 done

Decisions: Cognee owns chunk/embed/graph; Nango is SELF-HOSTED (set up together, see P3.0); LOGIN PAGE REQUIRED (user confirmed in chat; see P2.5/P2.6); Next.js + Tailwind + shadcn/ui; the reference screenshot is inspiration only, UI style is our own (minimal, neutral, one accent); connectors: Gmail, Google Calendar, Slack, Google Drive, Notion plus more (P3.5).

## Phase 0 - Stabilize
- [x] P0.1 Spike: verify Cognee, Nango, LangGraph APIs and versions
- [x] P0.2 Fix item status handling (duplicates stuck, completed_at, updated_at)
- [x] P0.3 Item identity and versioning (updated files re-ingest)
- [x] P0.4 Postgres job queue and single worker (replace watcher race)
- [x] P0.5 Dead-letter retry via new job; remove cursor-skip bugs
- [x] P0.6 Dependency cleanup and tabular edge cases (uv.lock needs `uv lock` on your machine)
- [x] P0.7 Tests for orchestrator, dedup, queue; sync schema.sql with models

## Phase 1 - Cognee
- [x] P1.1 Add Cognee and configuration (Neo4j, pgvector, OpenAI)
- [x] P1.2 CogneeIndexer: add + cognify per dataset, replace on update
- [x] P1.3 Retire custom chunk/embed/extract/graph-write; update graph read endpoint
- [x] P1.4 Ingest sample files and verify graph in Neo4j

## Phase 2 - Uploads and UI shell
- [x] P2.1 Upload API and UploadAdapter (any format)
- [x] P2.2 Next.js app scaffold (Tailwind, shadcn/ui, Inter, theme, layout)
- [x] P2.3 Data page: upload dropzone and documents table
- [x] P2.4 Activity page (runs, dead letters, retry)
- [x] P2.5 Auth backend: users table, password hashing, JWT, protected API
- [x] P2.6 Login page and route protection in the React app

## Phase 3 - Connectors (Nango)
- [x] P3.0 Self-hosted Nango: compose, env, first-run setup guide
- [x] P3.1 Nango backend: connect sessions, connections table, signature-verified webhook
- [x] P3.2 Provider fetchers (Gmail, Drive, Slack, Notion, Calendar) + NangoAdapter with cursor; scheduled polling enqueues scans
- [x] P3.3 Connectors UI (search, connected-only, categories, toggles)
- [x] P3.4 Connector detail: sync now, status, disconnect
- [x] P3.5 Additional connectors beyond the first five

## Phase 4 - Chatbot
- [x] P4.1 LangGraph agent with Cognee retrieval and citations
- [x] P4.2 Chat API: SSE streaming and Postgres conversation memory
- [x] P4.3 Chat UI with streaming and source citations
- [x] P4.4 Small evaluation set for answer quality

## Phase 5 - Hardening
- [x] P5.1 docker-compose for Postgres, Neo4j, API including self-hosted Nango
- [x] P5.2 Update README and architecture docs
- [x] P5.3 End-to-end verification and final review
