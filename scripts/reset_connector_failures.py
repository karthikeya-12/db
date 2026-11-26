"""
Shows (and, with --apply, clears) failed connector items so the next sync fetches them again.

    uv run python scripts/reset_connector_failures.py            # look only
    uv run python scripts/reset_connector_failures.py --apply    # delete failed connector items + their cursors
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
    print("Failed connector items by error:")
    rows = db.execute(
        text(
            "SELECT stage_failed, left(error_message, 110) AS error, count(*) AS n FROM dead_letter_items "
            "WHERE item_id LIKE 'nango:%' AND requeued_at IS NULL GROUP BY 1, 2 ORDER BY n DESC"
        )
    ).all()
    for stage, error, n in rows or []:
        print(f"  {n:4d}  [{stage}] {error}")
    if not rows:
        print("  (none)")
    for label, sql in {
        "failed items": "SELECT count(*) FROM ingestion_items WHERE source_id LIKE 'nango:%' AND status = 'FAILED'",
        "ok items": "SELECT count(*) FROM ingestion_items WHERE source_id LIKE 'nango:%' AND status = 'DONE'",
        "cursors": "SELECT count(*) FROM connector_checkpoints WHERE source_id LIKE 'nango:%'",
    }.items():
        print(f"{label}: {db.execute(text(sql)).scalar()}")

    if args.apply:
        for sql in (
            "DELETE FROM dead_letter_items WHERE item_id LIKE 'nango:%' AND item_id IN "
            "(SELECT item_id FROM ingestion_items WHERE status = 'FAILED')",
            "DELETE FROM ingestion_items WHERE source_id LIKE 'nango:%' AND status = 'FAILED'",
            "DELETE FROM connector_checkpoints WHERE source_id LIKE 'nango:%'",
        ):
            print(f"deleted {db.execute(text(sql)).rowcount} <- {sql[:60]}")
        print("Now press Sync now on the connection.")
