from ingestion_pipeline.core.knowledge_graph import subgraph_for_document


def node(node_id, node_type, **props):
    return (node_id, {"id": node_id, "type": node_type, **props})


# doc1 has chunks c1,c2; entity "Ada" is shared with doc2 (chunk c9), so the walk must not leak into doc2.
NODES = [
    node("doc1", "TextDocument", name="a.txt"),
    node("c1", "DocumentChunk", chunk_index=0, text="x" * 2000),
    node("c2", "DocumentChunk", chunk_index=1),
    node("ada", "Entity", name="Ada"),
    node("bob", "Entity", name="Bob"),
    node("person", "EntityType", name="Person"),
    node("sum1", "TextSummary", text="summary"),
    node("doc2", "TextDocument", name="b.txt"),
    node("c9", "DocumentChunk", chunk_index=0),
    node("zed", "Entity", name="Zed"),
]
EDGES = [
    ("c1", "doc1", "is_part_of", {}),
    ("c2", "doc1", "is_part_of", {}),
    ("c1", "ada", "contains", {}),
    ("c2", "bob", "contains", {}),
    ("ada", "person", "is_a", {}),
    ("bob", "person", "is_a", {}),
    ("ada", "bob", "works_with", {}),
    ("sum1", "c1", "made_from", {}),
    ("c9", "doc2", "is_part_of", {}),
    ("c9", "ada", "contains", {}),  # shared entity -> other document's chunk
    ("c9", "zed", "contains", {}),
]


def test_subgraph_contains_only_this_documents_neighbourhood():
    graph = subgraph_for_document(NODES, EDGES, "doc1")
    ids = {n["id"] for n in graph["nodes"]}
    assert ids == {"doc1", "c1", "c2", "ada", "bob", "person", "sum1"}  # no doc2 / c9 / zed
    labels = {(e["source"], e["target"], e["label"]) for e in graph["edges"]}
    assert ("ada", "bob", "works_with") in labels and ("c1", "doc1", "is_part_of") in labels
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])


def test_labels_and_long_text_are_tidied():
    graph = subgraph_for_document(NODES, EDGES, "doc1")
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["ada"]["label"] == "Ada" and by_id["c2"]["label"] == "Chunk 1"
    assert len(by_id["c1"]["properties"]["text"]) == 500 and "chunk_index" in by_id["c1"]["properties"]


def test_unknown_document_gives_an_empty_graph():
    assert subgraph_for_document(NODES, EDGES, "nope") == {"nodes": [], "edges": []}
