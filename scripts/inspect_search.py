"""Prints the raw shape of what Cognee's chunk search returns for a query (debugging aid for chat retrieval).

    uv run python scripts/inspect_search.py "How many vacation days do employees get?"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ingestion_pipeline.chat.retrieval import _unwrap, passage_from_payload
from ingestion_pipeline.core.cognee_indexer import run_on_cognee_loop
from ingestion_pipeline.core.cognee_setup import get_cognee


async def go(query: str):
    from cognee import SearchType

    return await get_cognee().search(query_text=query, query_type=SearchType.CHUNKS, top_k=3)


raw = run_on_cognee_loop(go(sys.argv[1] if len(sys.argv) > 1 else "vacation days"))
print("type:", type(raw), "len:", len(raw) if hasattr(raw, "__len__") else "?")
for item in (raw or [])[:2]:
    print("item type:", type(item))
    print("item repr:", repr(item)[:800])
payloads = _unwrap(raw)
print("readable payloads:", len(payloads))
for p in payloads[:2]:
    print(passage_from_payload(p))


async def datasets_overview():
    cognee = get_cognee()
    from cognee.modules.users.methods import get_default_user

    user = await get_default_user()
    rows = []
    for ds in await cognee.datasets.list_datasets(user=user) if "user" in cognee.datasets.list_datasets.__code__.co_varnames else await cognee.datasets.list_datasets():
        data = await cognee.datasets.list_data(ds.id)
        rows.append((ds.name, len(data)))
    return rows


print("\nCognee datasets (name, documents):")
try:
    for name, count in run_on_cognee_loop(datasets_overview()):
        print(f"  {count:4d}  {name}")
except Exception as exc:  # diagnostic only
    print("  could not list datasets:", exc)
