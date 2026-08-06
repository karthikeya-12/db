"""Structured JSON logging -- one line per stage transition per item."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("item_id", "source_id", "stage", "outcome"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def log_stage(logger: logging.Logger, *, item_id: str, source_id: str, stage: str, outcome: str, **extra) -> None:
    """One structured line per stage transition per item."""
    logger.info(
        f"stage={stage} outcome={outcome}",
        extra={"item_id": item_id, "source_id": source_id, "stage": stage, "outcome": outcome, **extra},
    )
