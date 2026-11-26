"""
Answer-quality check for the chat, against YOUR real stack (Postgres, Neo4j, OpenAI).

    uv run python scripts/run_eval.py             # ingest evals/corpus (skipped if already indexed), ask every case
    uv run python scripts/run_eval.py --cleanup   # also remove the evaluation documents afterwards
    uv run python scripts/run_eval.py --only vacation-days,q3-revenue

Each case in evals/cases.json is scored on: expected facts present, the right document cited, and - for questions the
archive cannot answer - an honest "I couldn't find it". Exit code 1 if any case fails. A few cents of OpenAI usage.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ITEMS = ["local:eval_northwind_handbook.md", "local:eval_q3_review.md", "local:eval_vendor_contract.md"]


def ingest_corpus() -> None:
    from ingestion_pipeline.adapters.local_folder_adapter import LocalFolderAdapter
    from ingestion_pipeline.consumers import cognee_consumer
    from ingestion_pipeline.core.blobstore import get_blob_store
    from ingestion_pipeline.core.orchestrator import PipelineOrchestrator
    from ingestion_pipeline.db.session import ensure_schema

    ensure_schema()
    folder = Path(tempfile.mkdtemp(prefix="chat_eval_"))
    for path in (ROOT / "evals" / "corpus").glob("*.md"):
        shutil.copy(path, folder / path.name)
    summary = PipelineOrchestrator(adapter=LocalFolderAdapter(folder), blob_store=get_blob_store()).run()
    print(f"ingest: {summary}")
    for _ in range(20):
        result = cognee_consumer.run_once()
        if result["events_seen"] == 0 or result["indexed"] + result["skipped"] + result["failed"] == 0:
            break
        print(f"index: {result}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--only", default="")
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from ingestion_pipeline.chat.agent import build_agent
    from ingestion_pipeline.chat.evaluation import score_case, summarize

    if not args.skip_ingest:
        ingest_corpus()

    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    agent = build_agent()
    print("warming up Cognee (first call is slow)...")
    agent._retriever.warm_up()
    results = []
    for case in cases:
        done = agent.ask(case["question"], case.get("history"))
        result = score_case(case, done["answer"], done["sources"])
        results.append(result)
        print(f"\n{'PASS' if result.passed else 'FAIL'}  {case['id']}\n  Q: {case['question']}\n  A: {done['answer'][:400]}")
        for problem in result.problems:
            print(f"  ! {problem}")

    summary = summarize(results)
    print(f"\n{summary['passed']}/{summary['total']} passed" + (f"; failed: {', '.join(summary['failed'])}" if summary["failed"] else ""))

    if args.cleanup:
        from ingestion_pipeline.core.cognee_indexer import CogneeIndexer

        indexer = CogneeIndexer()
        for item in CORPUS_ITEMS:
            print(f"removed {item}: {indexer.remove_document(item_id=item, source_id='local_folder')}")
    sys.exit(1 if summary["failed"] else 0)


if __name__ == "__main__":
    main()
