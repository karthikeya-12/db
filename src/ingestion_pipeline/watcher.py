"""
DEPRECATED -- automatic processing now runs inside the API process itself.

Run `python -m ingestion_pipeline.api.main` instead: it now starts the REST
API AND the folder watcher together in one process, so this is no longer a
separate thing you need to start. This file is kept only so a stale
`python -m ingestion_pipeline.watcher` invocation fails with a clear message
instead of silently doing something out of date. Safe to delete -- the
watcher logic now lives in api/main.py.
"""
from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "watcher.py has been merged into api/main.py -- run "
        "`python -m ingestion_pipeline.api.main` instead. It now starts "
        "both the REST API and the automatic folder watcher in one process."
    )


if __name__ == "__main__":
    main()
