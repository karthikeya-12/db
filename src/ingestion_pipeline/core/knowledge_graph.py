"""
Read side of the knowledge graph Cognee builds in Neo4j: the neighbourhood of ONE document.

Cognee stores every node under the label `__Node__` with `id`, `type` and (usually) `name`
properties, and names each relationship after the field that created it:

    (DocumentChunk) -[is_part_of]-> (<Something>Document)      id == our pinned data_id
    (DocumentChunk) -[contains]->   (Entity)
    (Entity)        -[is_a]->       (EntityType)
    (Entity)        -[<verb>]->     (Entity)                   relationships extracted by the LLM
    (TextSummary)   -[made_from]->  (DocumentChunk)

The document node's id is the pinned Cognee data_id for the item (cognee_indexer.data_id_for), so
no extra lookup table is needed. Entities are SHARED between documents, so the walk is bounded:
document -> its chunks -> what those chunks directly touch -> the entity types of those entities.
It never follows an entity into another document's chunks.

subgraph_for_document() is a pure function over (nodes, edges) as returned by Cognee's
graph engine `get_graph_data()`, so it is testable without Neo4j.
"""
from __future__ import annotations

from typing import Iterable

from ingestion_pipeline.core.cognee_indexer import data_id_for, run_on_cognee_loop

_HIDDEN_PROPERTIES = {"id", "type", "name", "created_at", "updated_at", "metadata", "belongs_to_set", "feedback_weight"}
_MAX_PROPERTY_CHARS = 500


def _label(node_id: str, props: dict) -> str:
    name = props.get("name")
    if name:
        return str(name)
    if props.get("type") == "DocumentChunk":
        return f"Chunk {props.get('chunk_index', '?')}"
    return f"{props.get('type', 'Node')}_{node_id[:8]}"


def _clean_properties(props: dict) -> dict:
    cleaned = {}
    for key, value in props.items():
        if key in _HIDDEN_PROPERTIES or value is None:
            continue
        cleaned[key] = value[:_MAX_PROPERTY_CHARS] if isinstance(value, str) else value
    return cleaned


def subgraph_for_document(nodes: Iterable, edges: Iterable, document_id) -> dict:
    """nodes: [(id, props)]; edges: [(source_id, target_id, relationship_name, props)]."""
    props_by_id = {str(node_id): props for node_id, props in nodes}
    doc = str(document_id)
    if doc not in props_by_id:
        return {"nodes": [], "edges": []}

    triples = [(str(edge[0]), str(edge[1]), str(edge[2])) for edge in edges]

    def kind(node_id: str) -> str:
        return str(props_by_id.get(node_id, {}).get("type", ""))

    def is_other_document_or_chunk(node_id: str) -> bool:
        node_kind = kind(node_id)
        return node_kind == "DocumentChunk" or node_kind.endswith("Document")

    chunks = {s for s, t, r in triples if t == doc and r == "is_part_of"}
    keep = {doc} | chunks

    touched = set()  # what this document's chunks directly connect to (entities, summaries, ...)
    for s, t, _ in triples:
        for a, b in ((s, t), (t, s)):
            if a in chunks and b not in keep and not is_other_document_or_chunk(b):
                touched.add(b)
    keep |= touched

    for s, t, _ in triples:  # the entity types of the entities we kept
        for a, b in ((s, t), (t, s)):
            if a in touched and kind(a) == "Entity" and kind(b) == "EntityType":
                keep.add(b)

    out_nodes = [
        {"id": n, "label": _label(n, props_by_id[n]), "type": kind(n), "properties": _clean_properties(props_by_id[n])}
        for n in sorted(keep)
        if n in props_by_id
    ]
    seen = set()
    out_edges = []
    for s, t, r in triples:
        if s in keep and t in keep and (s, t, r) not in seen:
            seen.add((s, t, r))
            out_edges.append({"source": s, "target": t, "label": r})
    return {"nodes": out_nodes, "edges": out_edges}


def read_document_subgraph(item_id: str) -> dict:
    """Fetch the graph from Neo4j (through Cognee) and return the neighbourhood of one item."""
    from ingestion_pipeline.core.cognee_setup import get_cognee

    get_cognee()  # applies our configuration before the graph engine is created

    async def fetch():
        from cognee.infrastructure.databases.graph import get_graph_engine

        engine = await get_graph_engine()
        return await engine.get_graph_data()

    nodes, edges = run_on_cognee_loop(fetch())
    return subgraph_for_document(nodes, edges, data_id_for(item_id))
