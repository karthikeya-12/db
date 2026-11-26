"""
Shows what the background machinery is doing: job queue, indexing queue, failures.

    uv run python scripts/diagnose_queue.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from ingestion_pipeline.db.session import session_scope

with session_scope() as db:
    print("Jobs by status:")
    for status, kind, n in db.execute(text("SELECT status, kind, count(*) FROM ingestion_jobs GROUP BY 1, 2 ORDER BY 1, 2")).all():
        print(f"  {status:8s} {kind:11s} {n}")
    print("\nMost recent jobs:")
    for row in db.execute(
        text("SELECT status, kind, left(source_id, 48), attempts, started_at, finished_at, left(coalesce(error, ''), 140) "
             "FROM ingestion_jobs ORDER BY created_at DESC LIMIT 8")
    ).all():
        print("  ", *row)

    offset = db.execute(text("SELECT last_event_id FROM event_consumer_offsets WHERE consumer_name = 'cognee_indexer'")).scalar() or 0
    newest = db.execute(text("SELECT coalesce(max(event_id), 0) FROM ingestion_events")).scalar()
    pending = db.execute(text("SELECT count(*) FROM ingestion_events WHERE event_id > :o AND topic = 'item.ingested'"), {"o": offset}).scalar()
    print(f"\nIndexing queue: consumer at event {offset} of {newest}; {pending} event(s) waiting")

    print("\nOpen indexing failures:")
    rows = db.execute(
        text("SELECT attempt_count, left(item_id, 60), left(error_message, 160) FROM dead_letter_items "
             "WHERE stage_failed = 'cognee-index' AND requeued_at IS NULL ORDER BY failed_at DESC LIMIT 8")
    ).all()
    for row in rows:
        print("  ", *row)
    if not rows:
        print("  (none)")
