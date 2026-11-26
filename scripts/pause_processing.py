"""
Stops all pending background work without deleting any data. STOP THE API FIRST (Ctrl+C in its terminal).

    uv run python scripts/pause_processing.py            # shows what it would do
    uv run python scripts/pause_processing.py --apply    # cancels queued/running jobs and skips the indexing backlog

What stays: everything already filed, indexed and searchable.
What changes: documents still waiting to be indexed stay filed in the archive but are not made searchable
(the indexing position moves to the newest event), and no scan jobs are left in the queue.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text

from ingestion_pipeline.db.session import session_scope

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()

with session_scope() as db:
    jobs = db.execute(text("SELECT count(*) FROM ingestion_jobs WHERE status IN ('QUEUED', 'RUNNING')")).scalar()
    offset = db.execute(text("SELECT last_event_id FROM event_consumer_offsets WHERE consumer_name = 'cognee_indexer'")).scalar() or 0
    newest = db.execute(text("SELECT coalesce(max(event_id), 0) FROM ingestion_events")).scalar()
    waiting = db.execute(text("SELECT count(*) FROM ingestion_events WHERE event_id > :o AND topic = 'item.ingested'"), {"o": offset}).scalar()
    print(f"{jobs} queued/running job(s); {waiting} document(s) waiting to be indexed")
    if not args.apply:
        print("Nothing changed. Re-run with --apply to cancel the jobs and skip the indexing backlog.")
        sys.exit(0)
    cancelled = db.execute(
        text("UPDATE ingestion_jobs SET status = 'FAILED', error = 'cancelled by pause_processing', finished_at = now() WHERE status IN ('QUEUED', 'RUNNING')")
    ).rowcount
    db.execute(
        text("INSERT INTO event_consumer_offsets (consumer_name, topic, last_event_id) VALUES ('cognee_indexer', 'item.ingested', :n) "
             "ON CONFLICT (consumer_name, topic) DO UPDATE SET last_event_id = :n"),
        {"n": newest},
    )
    print(f"cancelled {cancelled} job(s); indexing position moved to event {newest}")
