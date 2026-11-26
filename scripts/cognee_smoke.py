"""
Smoke test for the Cognee integration against YOUR real local services (Postgres, Neo4j, OpenAI).

    uv run python scripts/cognee_smoke.py            # run the checks
    uv run python scripts/cognee_smoke.py --cleanup  # also remove the test documents from Cognee afterwards

What it checks, in order, stopping at the first failure with a hint:
  1. Neo4j is reachable with the credentials in .env AND has the APOC plugin (Cognee's Neo4j adapter needs it).
  2. Postgres is reachable and sql/schema.sql applies.
  3. Two small Markdown files go through the pipeline: fetch -> archive -> normalize -> publish.
  4. The Cognee consumer indexes them (chunk, embed, LLM entity extraction, write to Neo4j). Uses your OpenAI key.
  5. The graph for one document can be read back from Neo4j.
  6. A natural-language question is answered from the graph.
  7. Changing a file produces a new version and is applied to Cognee as an update.

It writes only files named smoke_*.md in a temp folder, and Cognee/Neo4j/Postgres data for them
(a few cents of OpenAI usage). It does not touch data/incoming.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import textwrap
import time
from pathlib import Path


def ok(message: str) -> None:
    print(f"  PASS  {message}")


def fail(message: str, hint: str = "") -> None:
    print(f"  FAIL  {message}")
    if hint:
        print(f"        hint: {hint}")
    sys.exit(1)


def section(title: str) -> None:
    print(f"\n[{title}]")


DOC_ADA = """\
# Ada Lovelace

Ada Lovelace worked with Charles Babbage on the Analytical Engine in London.
Ada Lovelace wrote the first published algorithm intended for a machine.
Charles Babbage designed the Analytical Engine.
"""

DOC_ACME = """\
# Acme Robotics

Acme Robotics is headquartered in Berlin and builds warehouse robots.
Maria Chen is the chief executive officer of Acme Robotics.
Acme Robotics acquired Nordic Vision, a startup based in Oslo.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="create the Postgres database from DATABASE_URL if it does not exist",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="remove the test documents from Cognee at the end",
    )
    args = parser.parse_args()

    from ingestion_pipeline.config import get_settings

    settings = get_settings()

    section("1. Neo4j + APOC")
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        fail(
            f"cannot connect to Neo4j at {settings.neo4j_uri}: {exc}",
            "is Neo4j running, and are NEO4J_URI/USER/PASSWORD in .env right?",
        )
    ok(f"connected to {settings.neo4j_uri}")
    database = settings.neo4j_database or None
    if database:
        with driver.session(database="system") as system_session:
            found = [
                r["name"]
                for r in system_session.run("SHOW DATABASES YIELD name RETURN name")
            ]
        if database not in found:
            fail(
                f"Neo4j database '{database}' does not exist (found: {sorted(found)})",
                f"run in Neo4j Browser: CREATE DATABASE {database}",
            )
        ok(f"target database '{database}' exists")
    with driver.session(database=database) as session:
        apoc = None
        errors = []
        try:
            apoc = session.run("RETURN apoc.version() AS v").single()["v"]
        except Exception as exc:  # noqa: BLE001
            errors.append(f"apoc.version(): {exc}")
        if apoc is None:
            try:
                count = session.run(
                    "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc.' RETURN count(*) AS c"
                ).single()["c"]
                if count:
                    apoc = f"{count} procedures visible"
            except Exception as exc:  # noqa: BLE001
                errors.append(f"SHOW PROCEDURES: {exc}")
        if apoc is None:
            fail(
                "APOC not usable. Neo4j said: " + " | ".join(errors),
                "Install APOC Core for your exact Neo4j version, restart the database, and make sure "
                "dbms.security.procedures.unrestricted=apoc.* (and allowlist) is set in neo4j.conf",
            )
        ok(f"APOC plugin present ({apoc})")
        edition = session.run(
            "CALL dbms.components() YIELD edition RETURN edition LIMIT 1"
        ).single()["edition"]
        ok(f"edition: {edition} (shared graph is used, so Community is fine)")
    driver.close()

    section("2. Postgres + schema")
    from ingestion_pipeline.db.session import ensure_schema

    def try_schema():
        try:
            ensure_schema()
            return None
        except Exception as exc:  # noqa: BLE001
            return exc

    error = try_schema()
    if error is not None and "does not exist" in str(error) and args.create_db:
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        url = make_url(settings.database_url)
        with psycopg.connect(
            host=url.host,
            port=url.port,
            user=url.username,
            password=url.password,
            dbname="postgres",
            autocommit=True,
        ) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database))
            )
        ok(f"created database {url.database}")
        error = try_schema()
    if error is not None:
        fail(
            f"database error: {error}",
            "if the database is missing, re-run with --create-db (the DB user needs CREATEDB), "
            'or run: docker exec -it <postgres-container> psql -U <user> -c "CREATE DATABASE ingestion_pipeline"',
        )
    ok("schema applied")

    section("3. Ingest two Markdown files")
    from ingestion_pipeline.adapters.local_folder_adapter import LocalFolderAdapter
    from ingestion_pipeline.core.blobstore import get_blob_store
    from ingestion_pipeline.core.orchestrator import PipelineOrchestrator

    folder = Path(tempfile.mkdtemp(prefix="cognee_smoke_"))
    (folder / "smoke_ada.md").write_text(DOC_ADA, encoding="utf-8")
    (folder / "smoke_acme.md").write_text(DOC_ACME, encoding="utf-8")
    orchestrator = PipelineOrchestrator(
        adapter=LocalFolderAdapter(folder), blob_store=get_blob_store()
    )
    summary = orchestrator.run()
    if summary["items_failed"]:
        fail(
            f"ingestion failed: {summary}",
            "check the log above; first Docling use downloads models and needs internet",
        )
    ok(
        f"ingested: processed={summary['items_processed']} skipped_duplicate={summary['items_skipped_duplicate']}"
    )

    section("4. Cognee indexing (calls OpenAI)")
    # Earlier failed runs may have dead-lettered the smoke documents; queue those for another try.
    from sqlalchemy import select

    from ingestion_pipeline.consumers import cognee_consumer
    from ingestion_pipeline.core.jobs import requeue_dead_letter
    from ingestion_pipeline.db.models import DeadLetterItem
    from ingestion_pipeline.db.session import session_scope

    with session_scope() as db:
        rows = db.scalars(
            select(DeadLetterItem).where(
                DeadLetterItem.stage_failed == "cognee-index",
                DeadLetterItem.requeued_at.is_(None),
                DeadLetterItem.item_id.like("%smoke_%"),
            )
        ).all()
        for row in rows:
            requeue_dead_letter(db, row.id)
        if rows:
            print(f"  requeued {len(rows)} earlier failed smoke document(s)")

    started = time.time()
    totals = {"indexed": 0, "failed": 0}
    for _ in range(20):
        result = cognee_consumer.run_once()
        totals["indexed"] += result["indexed"]
        totals["failed"] += result["failed"]
        if (
            result["events_seen"] == 0
            or result["indexed"] + result["skipped"] + result["failed"] == 0
        ):
            break
    if totals["indexed"] < 2:
        fail(
            f"only {totals['indexed']} document(s) indexed ({totals})",
            "see dead_letter_items (stage cognee-index) for the error text",
        )
    ok(f"indexed {totals['indexed']} document(s) in {time.time() - started:.0f}s")

    section("5. Read the graph back")
    from ingestion_pipeline.core.knowledge_graph import read_document_subgraph

    graph = read_document_subgraph("local:smoke_ada.md")
    kinds = {}
    for node in graph["nodes"]:
        kinds[node["type"]] = kinds.get(node["type"], 0) + 1
    if not graph["nodes"]:
        fail(
            "no graph found for local:smoke_ada.md",
            "cognify may have produced nothing; check the Cognee log",
        )
    ok(f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges: {kinds}")
    names = sorted(n["label"] for n in graph["nodes"] if n["type"] == "Entity")
    print(f"        entities: {names}")

    section("6. Ask a question")
    from ingestion_pipeline.core.cognee_indexer import dataset_for, run_on_cognee_loop
    from ingestion_pipeline.core.cognee_setup import get_cognee

    cognee = get_cognee()
    from cognee import SearchType

    async def ask():
        return await cognee.search(
            query_text="Who did Ada Lovelace work with, and on what?",
            query_type=SearchType.GRAPH_COMPLETION,
            datasets=[dataset_for("local_folder")],
        )

    answer = run_on_cognee_loop(ask())
    print(f"        answer: {answer}")
    if not answer or "babbage" not in str(answer).lower():
        fail(
            "the answer does not mention Charles Babbage",
            "indexing may be incomplete; re-run, or inspect the graph in Neo4j Browser",
        )
    ok("answer is grounded in the document")

    section("7. Update a document")
    (folder / "smoke_ada.md").write_text(
        DOC_ADA + "\nAda Lovelace also corresponded with Mary Somerville.\n",
        encoding="utf-8",
    )
    summary = orchestrator.run()
    if summary["items_processed"] != 1:
        fail(f"expected 1 updated item, got {summary}")
    ok("new version ingested (version 2)")
    result = cognee_consumer.run_once()
    if result["indexed"] != 1:
        fail(f"update was not applied to Cognee: {result}")
    ok("applied to Cognee as an update")

    if args.cleanup:
        section("cleanup")
        from ingestion_pipeline.core.cognee_indexer import CogneeIndexer

        indexer = CogneeIndexer()
        for item in ("local:smoke_ada.md", "local:smoke_acme.md"):
            print(
                f"  removed {item}: {indexer.remove_document(item_id=item, source_id='local_folder')}"
            )

    print("\nAll checks passed.")


def __ignore__():
    from sqlalchemy import text

    from ingestion_pipeline.db.session import session_scope

    # queries = [
    #     "DELETE FROM dead_letter_items WHERE item_id LIKE 'nango:%'",
    #     "DELETE FROM ingestion_items WHERE source_id LIKE 'nango:%' AND status = 'FAILED'",
    #     "DELETE FROM connector_checkpoints WHERE source_id LIKE 'nango:%'",
    # ]
    queries = [
        "SELECT * FROM dead_letter_items WHERE item_id LIKE 'nango:%'",
        "SELECT * FROM ingestion_items WHERE source_id LIKE 'nango:%' AND status = 'FAILED'",
        "SELECT * FROM connector_checkpoints WHERE source_id LIKE 'nango:%'",
    ]
    with session_scope() as db:
        for i in queries:
            res = db.execute(text(i))
            print(res.__dict__)


if __name__ == "__main__":
    main()
    # __ignore__()
