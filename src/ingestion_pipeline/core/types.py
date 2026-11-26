"""Shared value types passed between every stage of the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ContentType(str, Enum):
    DOCUMENT = "DOCUMENT"
    TABULAR = "TABULAR"
    TRANSCRIPT = "TRANSCRIPT"
    MESSAGE = "MESSAGE"
    STRUCTURED_RECORD = "STRUCTURED_RECORD"


@dataclass
class RawItem:
    """What every Adapter.fetch() must return, regardless of source."""
    source_id: str
    item_id: str
    content_type: ContentType
    raw_bytes: bytes | None = None
    raw_payload: Any | None = None
    source_metadata: dict = field(default_factory=dict)
    file_name: str | None = None
