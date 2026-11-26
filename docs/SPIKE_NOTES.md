# Spike notes (P0.1) - verified against the published packages, 2026-11-25

Web search was unavailable, so these were read from the package sources/type
definitions themselves (pip download / npm pack), not from docs sites.

## Cognee 1.6.10 (Python >=3.10,<3.15)
- API: `add(data, dataset_name=..., ...)` then `cognify(datasets=[...])`;
  `search(query_text, query_type=SearchType.X, datasets=[...], top_k=15, only_context=False, include_references=False)`;
  `update(data_id, data, dataset_id, ...)` for changed documents; `forget(data_id=, dataset_id=)` or `forget(dataset=)` to remove.
  `add()` includes a "refuse changed existing documents" check, so updated content must go through `update()`, not a second `add()`.
- SearchType includes CHUNKS, SUMMARIES, GRAPH_COMPLETION, RAG_COMPLETION, HYBRID_COMPLETION (default), CYPHER, etc.
- Config via env: `GRAPH_DATABASE_PROVIDER` (default ladybug; set `neo4j`), `VECTOR_DB_PROVIDER` (default lancedb; pgvector exists),
  relational `DB_PROVIDER` (default sqlite).
- Extras: `neo4j` extra requires `neo4j>=5.28,<6` -> our pin `neo4j>=6.2` must change to `>=5.28,<6`.
  `postgres` extra requires psycopg2 + asyncpg (conflicts with this repo's "no psycopg2" rule), so we use
  LanceDB (vectors) + SQLite (Cognee's own metadata) and keep all of OUR tables on psycopg v3.
- Ships its own docling extra and LLM layer (litellm/openai).

## Nango (node 0.72.6, REST)
- `POST /connect/sessions` -> `{data: {token, connect_link, expires_at}}`; body has `end_user` / `tags`, `allowed_integrations`.
- Frontend (`@nangohq/frontend`): `new Nango()` then `openConnectUI({sessionToken, onEvent})`; events: ready, close, connect, error, settings_changed.
- Webhooks: `type:'auth'` (connectionId, providerConfigKey, provider, operation) and `type:'sync'`
  (connectionId, providerConfigKey, syncName, model, modifiedAfter, success, responseResults).
- Records: `listRecords({providerConfigKey, connectionId, model, modifiedAfter, limit, cursor})` -> `{records, next_cursor}`;
  each record carries `_nango_metadata.last_action` (added/updated/deleted).
- No official Python SDK assumed: backend calls the REST API with httpx and the secret key.
- VERIFIED (SDK 0.70.9): webhook signature is header `X-Nango-Hmac-Sha256` = hex HMAC-SHA256 of the raw body with the environment secret key.
- VERIFIED (docs.nango.dev, free self-hosting overview): the FREE self-hosted Nango includes ONLY API authorization.
  Syncs, actions and webhooks are NOT included. Consequences: (a) we use Nango for OAuth + token storage/refresh only;
  (b) our backend fetches provider data itself with the access token from `GET /connections/{id}?provider_config_key=`;
  (c) new data is found by SCHEDULED POLLING (cursor per connection), not Nango webhooks; (d) connections are discovered by
  listing `GET /connections`, since the auth webhook will not fire. The webhook endpoint is kept (signature-verified) in case the
  deployment is ever upgraded.
- REST used: GET /connections, GET /connections/{id}?provider_config_key=, DELETE /connections/{id}?provider_config_key=,
  POST /connect/sessions, GET /integrations, GET /providers.

## LangGraph / LangChain
- langgraph 1.2.24, langgraph-checkpoint-postgres 3.2.1, langchain-openai 1.7.4, langchain-core 1.7.7.

## Decisions that follow
1. Pin `neo4j>=5.28,<6`.
2. Cognee: Neo4j graph + LanceDB vectors + SQLite metadata; our app state stays in Postgres (psycopg v3).
3. Store Cognee's `data_id` per item version so updates call `update()` and deletes call `forget()`.
4. Nango access via a small httpx client in `connectors/nango_client.py`.
