"""
Neo4j write layer for extracted entities/relationships.

Write pattern ported from everest_repo's kg_zone3 chunk loader
(worker/neo4j/chunk_loader.py::load_via_driver): declare a uniqueness
constraint once, batch all rows through a single UNWIND+MERGE per
Cypher statement (one round-trip per batch instead of one per row), and
return the driver's own write counters (nodes_created, relationships_created,
properties_set) rather than counting client-side -- exactly that file's
approach, just simplified since this project has no chunk/temporal-node
graph shape, only (:Entity)-[:RELATES_TO]->(:Entity).

Deduping is by (name, type) only -- no mapping against an external
concept/entity registry (see entity_extractor.py's docstring for why).

Only ever called from consumers/entity_relationship_consumer.py -- the core
ingestion pipeline (orchestrator.py) never imports this module or knows
Neo4j exists.
"""
from __future__ import annotations

ENTITY_LABEL = "Entity"


class GraphStore:
    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase  # optional dependency -- pip install -e ".[graph]"

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._constraint_ensured = False

    def close(self) -> None:
        self._driver.close()

    def _ensure_constraint(self) -> None:
        """Declare the (name, type) uniqueness constraint once per process, not per write --
        matches chunk_loader.py's `CREATE CONSTRAINT ... IF NOT EXISTS` pattern."""
        if self._constraint_ensured:
            return
        with self._driver.session() as session:
            session.run(
                f"CREATE CONSTRAINT entity_name_type IF NOT EXISTS "
                f"FOR (n:{ENTITY_LABEL}) REQUIRE (n.name, n.type) IS UNIQUE"
            )
        self._constraint_ensured = True

    def write_entities_and_relationships(
        self, item_id: str, source_id: str, entities: list[dict], relationships: list[dict]
    ) -> dict:
        """
        One write transaction per item -- either the whole item's entities/relationships
        land, or none do, so a mid-write failure never leaves a half-written item.

        Returns {"nodes_created": n, "relationships_created": n, "properties_set": n},
        read directly from the driver's own transaction counters (not counted
        client-side) -- same observability shape as chunk_loader.py's `acc` dict.
        """
        self._ensure_constraint()
        with self._driver.session() as session:
            return session.execute_write(self._write_tx, item_id, source_id, entities, relationships)

    @staticmethod
    def _write_tx(tx, item_id: str, source_id: str, entities: list[dict], relationships: list[dict]) -> dict:
        counters = {"nodes_created": 0, "relationships_created": 0, "properties_set": 0}

        # The extractor's relationship objects only carry subject/object NAMES, not
        # their types -- build a name->type lookup from this item's own entities list
        # so a relationship's endpoints MERGE onto the exact same node the entities
        # batch below just created/matched, rather than a second node keyed on a
        # fallback type (which would silently fragment the graph into two nodes per name).
        entity_type_by_name = {e["name"]: e.get("type", "OTHER") for e in entities}

        if entities:
            entity_rows = [
                {
                    "name": e["name"],
                    "type": e.get("type", "OTHER"),
                    "evidence": e.get("evidence"),
                }
                for e in entities
            ]
            result = tx.run(
                f"""
                UNWIND $rows AS row
                MERGE (n:{ENTITY_LABEL} {{name: row.name, type: row.type}})
                ON CREATE SET n.first_seen_item_id = $item_id, n.source_id = $source_id, n.evidence = row.evidence
                SET n.last_seen_item_id = $item_id
                """,
                rows=entity_rows,
                item_id=item_id,
                source_id=source_id,
            )
            cc = result.consume().counters
            counters["nodes_created"] += cc.nodes_created
            counters["properties_set"] += cc.properties_set

        if relationships:
            rel_rows = [
                {
                    "subject": r["subject"],
                    "subject_type": entity_type_by_name.get(r["subject"], "OTHER"),
                    "object": r["object"],
                    "object_type": entity_type_by_name.get(r["object"], "OTHER"),
                    "predicate": r["predicate"],
                    "evidence": r.get("evidence"),
                }
                for r in relationships
            ]
            result = tx.run(
                f"""
                UNWIND $rows AS row
                MERGE (subject:{ENTITY_LABEL} {{name: row.subject, type: row.subject_type}})
                MERGE (object:{ENTITY_LABEL} {{name: row.object, type: row.object_type}})
                MERGE (subject)-[r:RELATES_TO {{predicate: row.predicate}}]->(object)
                ON CREATE SET r.item_id = $item_id, r.evidence = row.evidence
                """,
                rows=rel_rows,
                item_id=item_id,
            )
            cc = result.consume().counters
            counters["nodes_created"] += cc.nodes_created
            counters["relationships_created"] += cc.relationships_created
            counters["properties_set"] += cc.properties_set

        return counters


def get_graph_store() -> GraphStore:
    from ingestion_pipeline.config import get_settings

    settings = get_settings()
    return GraphStore(uri=settings.neo4j_uri, user=settings.neo4j_user, password=settings.neo4j_password)
