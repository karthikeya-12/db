"""
Neo4j write layer for extracted entities/relationships -- a two-layer graph
modeled directly on everest_repo's instance/evidence layer
(kg_zone3/kg-zone-3/worker/neo4j/temporal_loader.py + chunk_loader.py):

  (:Chunk)                       -- one per persisted text chunk (mirrors
                                     Postgres chunk_embeddings), linked to its
                                     siblings in document order
  (:Concept:<TYPE>)              -- one per distinct (name, type), dynamic
                                     secondary label same as everest's
                                     `SET n:\\`<EntityClassLabel>\\`` pattern
  (:Statement)                   -- one per extracted relationship (everest's
                                     TemporalNode, renamed -- we don't carry
                                     their valid-time/provenance fields), with
                                     RELATES_SUBJECT/RELATES_OBJECT up-edges to
                                     Concepts and a HAS_EVIDENCE down-edge to
                                     the Chunk it came from

NOT ported: everest's Structural Foundation / lattice layer (kg_sf_load) --
a curated, versioned business taxonomy loaded as `(:Concept)` nodes from
hand-authored manifest files. This project has no equivalent registry, so
Concept nodes here are deduped by (name, type) only, with no canonicalization
against an external index or cross-document identity resolution. That's a
deliberate, documented simplification (see entity_extractor.py's docstring),
not an oversight.

Also simplified vs. everest: a bare entity mention (no relationship) is
grounded to its source chunk via a direct Concept-[:MENTIONED_IN]->Chunk edge,
rather than wrapping every single mention in its own Statement/TemporalNode
the way everest's ABOUT role does -- we don't carry the valid-time data that
would justify a full statement wrapper for a bare mention.

CHILD_OF (section/heading hierarchy) is NOT implemented -- this project's
chunkers don't currently extract heading-level structure to hang it on
(see core/chunker.py). Only PREV_SIBLING/NEXT_SIBLING (chunk_index order
within one item) are written, matching everest's chunk_loader.py for that
part.

Write pattern (batched UNWIND+MERGE, driver write-counters instead of
client-side counting) is the same approach ported earlier from
chunk_loader.py::load_via_driver -- just applied to three node types now
instead of one.

Writes are only ever called from consumers/entity_relationship_consumer.py --
the core ingestion pipeline (orchestrator.py) never imports this module or
knows Neo4j exists. read_item_subgraph() is the one read-path exception:
called from api/main.py (GET /api/graph/items/{item_id}) so the frontend can
render a document's graph without touching Neo4j directly.
"""
from __future__ import annotations

import hashlib

CONCEPT_LABEL = "Concept"
CHUNK_LABEL = "Chunk"
STATEMENT_LABEL = "Statement"


def _statement_id(item_id: str, chunk_id: str, subject: str, predicate: str, obj: str) -> str:
    """Stable, content-derived id -- same approach as chunker.py's _chunk_id, so
    re-running extraction over unchanged text reproduces the same Statement node
    (MERGE dedupes) instead of creating a duplicate."""
    raw = f"{item_id}||{chunk_id}||{subject}||{predicate}||{obj}"
    return "STMT_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class GraphStore:
    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase  # optional dependency -- pip install -e ".[graph]"

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._constraints_ensured = False

    def close(self) -> None:
        self._driver.close()

    def _ensure_constraints(self) -> None:
        """Declare uniqueness constraints once per process, not per write -- matches
        chunk_loader.py's `CREATE CONSTRAINT ... IF NOT EXISTS` pattern, extended to
        all three node types."""
        if self._constraints_ensured:
            return
        with self._driver.session() as session:
            session.run(
                f"CREATE CONSTRAINT concept_name_type IF NOT EXISTS "
                f"FOR (n:{CONCEPT_LABEL}) REQUIRE (n.name, n.type) IS UNIQUE"
            )
            session.run(
                f"CREATE CONSTRAINT chunk_id IF NOT EXISTS "
                f"FOR (c:{CHUNK_LABEL}) REQUIRE c.chunk_id IS UNIQUE"
            )
            session.run(
                f"CREATE CONSTRAINT statement_id IF NOT EXISTS "
                f"FOR (s:{STATEMENT_LABEL}) REQUIRE s.statement_id IS UNIQUE"
            )
        self._constraints_ensured = True

    def write_chunk(
        self,
        chunk_id: str,
        item_id: str,
        source_id: str,
        chunk_index: int,
        body_text: str | None,
        prev_chunk_id: str | None,
    ) -> dict:
        """
        Write/merge one Chunk node and, if prev_chunk_id is given, a PREV_SIBLING/
        NEXT_SIBLING edge pair to the previous chunk of the same item. Called once
        per chunk, in chunk_index order, by the consumer -- so by the time chunk N
        is written, chunk N-1 already exists and the sibling edge always resolves.
        """
        self._ensure_constraints()
        with self._driver.session() as session:
            return session.execute_write(
                self._write_chunk_tx, chunk_id, item_id, source_id, chunk_index, body_text, prev_chunk_id
            )

    @staticmethod
    def _write_chunk_tx(tx, chunk_id, item_id, source_id, chunk_index, body_text, prev_chunk_id) -> dict:
        counters = {"nodes_created": 0, "relationships_created": 0, "properties_set": 0}
        result = tx.run(
            f"""
            MERGE (c:{CHUNK_LABEL} {{chunk_id: $chunk_id}})
            SET c.item_id = $item_id, c.source_id = $source_id, c.chunk_index = $chunk_index, c.body_text = $body_text
            """,
            chunk_id=chunk_id,
            item_id=item_id,
            source_id=source_id,
            chunk_index=chunk_index,
            body_text=body_text,
        )
        cc = result.consume().counters
        counters["nodes_created"] += cc.nodes_created
        counters["properties_set"] += cc.properties_set

        if prev_chunk_id is not None:
            result = tx.run(
                f"""
                MATCH (prev:{CHUNK_LABEL} {{chunk_id: $prev_chunk_id}})
                MATCH (curr:{CHUNK_LABEL} {{chunk_id: $chunk_id}})
                MERGE (prev)-[:NEXT_SIBLING]->(curr)
                MERGE (curr)-[:PREV_SIBLING]->(prev)
                """,
                prev_chunk_id=prev_chunk_id,
                chunk_id=chunk_id,
            )
            cc = result.consume().counters
            counters["relationships_created"] += cc.relationships_created

        return counters

    def write_entities_and_relationships(
        self,
        item_id: str,
        source_id: str,
        chunk_id: str,
        entities: list[dict],
        relationships: list[dict],
    ) -> dict:
        """
        One write transaction per chunk -- either the whole chunk's entities/
        relationships land, or none do. Assumes write_chunk() has already been
        called for chunk_id in this same run (the consumer always writes the Chunk
        node first), since MENTIONED_IN/HAS_EVIDENCE edges MATCH it rather than
        MERGE it here.

        Returns {"nodes_created": n, "relationships_created": n, "properties_set": n},
        read directly from the driver's own transaction counters -- same
        observability shape as chunk_loader.py's `acc` dict.
        """
        self._ensure_constraints()
        with self._driver.session() as session:
            return session.execute_write(
                self._write_entities_tx, item_id, source_id, chunk_id, entities, relationships
            )

    @staticmethod
    def _write_entities_tx(tx, item_id: str, source_id: str, chunk_id: str, entities: list[dict], relationships: list[dict]) -> dict:
        counters = {"nodes_created": 0, "relationships_created": 0, "properties_set": 0}

        # A relationship's subject/object name isn't always present in this same
        # chunk's `entities` list -- the LLM's two output lists aren't always
        # perfectly consistent with each other. Previously, a name missing from
        # `entities` fell through to a bare-bones MERGE in the relationships
        # block below with NO ON CREATE SET at all, producing a Concept node with
        # no first_seen_item_id/source_id and no dynamic type label -- a visibly
        # incomplete node next to every properly-created one. Fixed by folding
        # any such name into the entities list itself (type defaults to OTHER,
        # no per-entity evidence) before the write loop below, so EVERY Concept
        # node -- whether it came from `entities` or only appeared as a
        # relationship endpoint -- goes through the exact same full-property,
        # dynamic-labeled, MENTIONED_IN-linked write path. No separate fallback
        # path exists anymore.
        entity_type_by_name = {e["name"]: e.get("type", "OTHER") for e in entities}
        seen_names = set(entity_type_by_name)
        all_entities = list(entities)
        for r in relationships:
            for name in (r["subject"], r["object"]):
                if name not in seen_names:
                    seen_names.add(name)
                    all_entities.append({"name": name, "type": "OTHER", "evidence": None})
                    entity_type_by_name[name] = "OTHER"

        # Cypher can't parameterize a label, so entities are grouped by type and one
        # UNWIND+MERGE issued per group (ENTITY_TYPES is a small fixed set --
        # at most 6 extra round-trips per chunk, not one per entity). This block
        # runs BEFORE the relationships block below, so every relationship
        # endpoint -- whether a real extracted entity or a name that only showed
        # up as a relationship subject/object -- is guaranteed to already exist,
        # fully populated, by the time that block runs.
        by_type: dict[str, list[dict]] = {}
        for e in all_entities:
            by_type.setdefault(e.get("type", "OTHER"), []).append(e)

        for entity_type, rows in by_type.items():
            entity_rows = [{"name": e["name"], "evidence": e.get("evidence")} for e in rows]
            result = tx.run(
                f"""
                UNWIND $rows AS row
                MERGE (n:{CONCEPT_LABEL} {{name: row.name, type: $entity_type}})
                ON CREATE SET n.first_seen_item_id = $item_id, n.source_id = $source_id
                SET n.last_seen_item_id = $item_id
                SET n:`{entity_type}`
                WITH n, row
                MATCH (c:{CHUNK_LABEL} {{chunk_id: $chunk_id}})
                MERGE (n)-[m:MENTIONED_IN]->(c)
                ON CREATE SET m.evidence = row.evidence
                """,
                rows=entity_rows,
                entity_type=entity_type,
                item_id=item_id,
                source_id=source_id,
                chunk_id=chunk_id,
            )
            cc = result.consume().counters
            counters["nodes_created"] += cc.nodes_created
            counters["relationships_created"] += cc.relationships_created
            counters["properties_set"] += cc.properties_set

        if relationships:
            rel_rows = [
                {
                    "statement_id": _statement_id(item_id, chunk_id, r["subject"], r["predicate"], r["object"]),
                    "subject": r["subject"],
                    "subject_type": entity_type_by_name.get(r["subject"], "OTHER"),
                    "object": r["object"],
                    "object_type": entity_type_by_name.get(r["object"], "OTHER"),
                    "predicate": r["predicate"],
                    "evidence": r.get("evidence"),
                }
                for r in relationships
            ]
            # subject/object are MATCHed, not MERGEd, here -- the loop above
            # already guarantees both exist, fully populated and dynamic-labeled,
            # before this runs. A relationship whose subject or object still
            # somehow doesn't resolve (shouldn't happen given the above) is
            # simply skipped for that row rather than silently creating a new
            # bare node.
            result = tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (subject:{CONCEPT_LABEL} {{name: row.subject, type: row.subject_type}})
                MATCH (object:{CONCEPT_LABEL} {{name: row.object, type: row.object_type}})
                MERGE (stmt:{STATEMENT_LABEL} {{statement_id: row.statement_id}})
                ON CREATE SET stmt.predicate = row.predicate, stmt.evidence = row.evidence,
                              stmt.item_id = $item_id, stmt.source_id = $source_id, stmt.chunk_id = $chunk_id
                MERGE (stmt)-[:RELATES_SUBJECT]->(subject)
                MERGE (stmt)-[:RELATES_OBJECT]->(object)
                WITH stmt
                MATCH (c:{CHUNK_LABEL} {{chunk_id: $chunk_id}})
                MERGE (stmt)-[:HAS_EVIDENCE]->(c)
                """,
                rows=rel_rows,
                item_id=item_id,
                source_id=source_id,
                chunk_id=chunk_id,
            )
            cc = result.consume().counters
            counters["nodes_created"] += cc.nodes_created
            counters["relationships_created"] += cc.relationships_created
            counters["properties_set"] += cc.properties_set

        return counters

    def read_item_subgraph(self, item_id: str) -> dict:
        """
        Read every Chunk/Concept/Statement node belonging to one item, plus the
        MENTIONED_IN/HAS_EVIDENCE/RELATES_SUBJECT/RELATES_OBJECT edges between
        them, shaped as {"nodes": [...], "edges": [...]} -- generic node-link
        JSON, ready for a frontend graph-rendering library (e.g. react-force-graph)
        with no further reshaping. Read-only: called from api/main.py, never from
        the write path.
        """
        with self._driver.session() as session:
            return session.execute_read(self._read_item_subgraph_tx, item_id)

    @staticmethod
    def _read_item_subgraph_tx(tx, item_id: str) -> dict:
        result = tx.run(
            f"""
            MATCH (c:{CHUNK_LABEL} {{item_id: $item_id}})
            OPTIONAL MATCH (concept:{CONCEPT_LABEL})-[m:MENTIONED_IN]->(c)
            OPTIONAL MATCH (stmt:{STATEMENT_LABEL} {{item_id: $item_id}})-[:HAS_EVIDENCE]->(c)
            OPTIONAL MATCH (stmt)-[:RELATES_SUBJECT]->(subj:{CONCEPT_LABEL})
            OPTIONAL MATCH (stmt)-[:RELATES_OBJECT]->(obj:{CONCEPT_LABEL})
            RETURN c, concept, m, stmt, subj, obj
            """,
            item_id=item_id,
        )

        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def add_node(n, kind: str) -> str | None:
            if n is None:
                return None
            node_id = f"{kind}:{n.element_id}"
            if node_id not in nodes:
                nodes[node_id] = {"id": node_id, "kind": kind, "labels": list(n.labels), **dict(n)}
            return node_id

        for record in result:
            c_id = add_node(record["c"], "Chunk")
            concept_id = add_node(record["concept"], "Concept")
            stmt_id = add_node(record["stmt"], "Statement")
            subj_id = add_node(record["subj"], "Concept")
            obj_id = add_node(record["obj"], "Concept")

            if concept_id and c_id:
                edges.append({"source": concept_id, "target": c_id, "type": "MENTIONED_IN"})
            if stmt_id and c_id:
                edges.append({"source": stmt_id, "target": c_id, "type": "HAS_EVIDENCE"})
            if stmt_id and subj_id:
                edges.append({"source": stmt_id, "target": subj_id, "type": "RELATES_SUBJECT"})
            if stmt_id and obj_id:
                edges.append({"source": stmt_id, "target": obj_id, "type": "RELATES_OBJECT"})

        seen: set[tuple[str, str, str]] = set()
        deduped_edges = []
        for e in edges:
            key = (e["source"], e["target"], e["type"])
            if key not in seen:
                seen.add(key)
                deduped_edges.append(e)

        return {"nodes": list(nodes.values()), "edges": deduped_edges}


def get_graph_store() -> GraphStore:
    from ingestion_pipeline.config import get_settings

    settings = get_settings()
    return GraphStore(uri=settings.neo4j_uri, user=settings.neo4j_user, password=settings.neo4j_password)
