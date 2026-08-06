"""
The Source Adapter contract. Every data source implements exactly this and
nothing else -- the orchestrator (orchestrator.py) is the only place that
knows the pipeline's stage order. An adapter that contains dedup, chunking,
embedding, or persistence logic is a design bug: that logic belongs in the
shared core, not duplicated per source.

Onboarding a new source (email, CRM, ...) later means writing a new
Adapter subclass -- this file, and orchestrator.py, do not change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ingestion_pipeline.core.types import RawItem

# Opaque to the core pipeline -- each adapter defines its own shape
# (an S3 object key + version, a timestamp, an API pagination token, ...).
ItemRef = Any
Cursor = Any


class Adapter(ABC):
    """Base class every source adapter implements."""

    source_id: str

    @abstractmethod
    def list_changes(self, cursor: Cursor | None) -> list[ItemRef]:
        """Return refs for items new or changed since `cursor` (None on the very first run)."""
        raise NotImplementedError

    @abstractmethod
    def fetch(self, ref: ItemRef) -> RawItem:
        """Fetch the full raw item for one ref returned by list_changes."""
        raise NotImplementedError

    @abstractmethod
    def advance_cursor(self, ref: ItemRef) -> Cursor:
        """Return the checkpoint value to persist once `ref` has reached a terminal state."""
        raise NotImplementedError
