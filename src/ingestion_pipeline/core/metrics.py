"""
Per-run metrics aggregated by source_id. Kept in-memory for a single run and
emitted as one structured summary log line at the end -- wiring this to a
persistent metrics backend (Prometheus, CloudWatch, etc.) is future work;
this shape is designed not to need changes when that happens.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class RunMetrics:
    source_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    items_identified: int = 0
    items_processed: int = 0
    items_failed: int = 0
    items_skipped_duplicate: int = 0
    items_dead_lettered: int = 0

    def finish_and_log(self) -> dict:
        duration_seconds = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        summary = {
            "source_id": self.source_id,
            "items_identified": self.items_identified,
            "items_processed": self.items_processed,
            "items_failed": self.items_failed,
            "items_skipped_duplicate": self.items_skipped_duplicate,
            "items_dead_lettered": self.items_dead_lettered,
            "duration_seconds": round(duration_seconds, 2),
        }
        logger.info("run_summary", extra={"stage": "run_summary", "outcome": "done", **summary})
        return summary
