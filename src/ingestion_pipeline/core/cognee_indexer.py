"""
Hands normalized document text to Cognee, which chunks it, embeds it, extracts entities and
relationships with an LLM, and writes the knowledge graph to Neo4j.

Identity: every item gets a PINNED Cognee data_id (uuid5 of its item_id), so the same item always
maps to the same Cognee document across versions. A first version is `add()`ed; a later version
is `update()`d (Cognee diffs it and replaces only the touched chunks/graph nodes); a deleted item
is `forget()`ed. Whether a document already exists is asked of Cognee itself rather than guessed
from our own state, so a retry after a half-finished earlier attempt does the right thing.

Datasets: one Cognee dataset per source_id ("local_folder", "uploads", "nango_<connection>"), so a
connector can be wiped or re-indexed without touching the others.

Threading: Cognee's async engines (Neo4j driver, SQLAlchemy) bind to the event loop that first
used them, and callers here are synchronous worker threads. Every call is therefore run on one
dedicated long-lived event-loop thread instead of `asyncio.run()` per call.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_DATA_ID_NAMESPACE = uuid.UUID("6f1d9c52-3c1e-4a57-9a8e-0d6c1f6b7a10")


def data_id_for(item_id: str) -> uuid.UUID:
    """Stable Cognee document id for an item."""
    return uuid.uuid5(_DATA_ID_NAMESPACE, item_id)


def dataset_for(source_id: str) -> str:
    """Cognee dataset name for a source: lowercase letters, digits and underscores only."""
    return re.sub(r"[^a-z0-9_]+", "_", source_id.lower()).strip("_") or "default"


class _LoopRunner:
    """One background event loop for all Cognee calls."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="cognee-event-loop", daemon=True).start()
                self._loop = loop
            return self._loop

    def run(self, coroutine, timeout: float | None = None):
        return asyncio.run_coroutine_threadsafe(coroutine, self._ensure()).result(timeout)


_runner = _LoopRunner()


def run_on_cognee_loop(coroutine, timeout: float | None = None):
    """Run any Cognee coroutine on the shared event-loop thread (see module docstring)."""
    return _runner.run(coroutine, timeout)


class CogneeIndexer:
    def __init__(self, cognee_module: Any = None, data_item_cls: Any = None) -> None:
        # A fake injected for tests has no database to create; only a real Cognee needs setup().
        self._needs_setup = cognee_module is None
        self._cognee = cognee_module
        self._data_item_cls = data_item_cls

    # ---------------------------------------------------------------- public (sync)

    def index_document(
        self, *, item_id: str, source_id: str, text: str, title: str | None = None, metadata: dict | None = None
    ) -> str:
        """Add or update one document, then cognify its dataset. Returns "added" or "updated"."""
        return _runner.run(self._index_document(item_id, source_id, text, title, metadata or {}))

    def remove_document(self, *, item_id: str, source_id: str) -> bool:
        """Remove one document (and its graph/vector memory). Returns False if it was not indexed."""
        return _runner.run(self._remove_document(item_id, source_id))

    # --------------------------------------------------------------------- internals

    def _module(self):
        if self._cognee is None:
            from ingestion_pipeline.core.cognee_setup import get_cognee

            self._cognee = get_cognee()
        return self._cognee

    def _make_data_item(self, **kwargs):
        if self._data_item_cls is None:
            from cognee.tasks.ingestion.data_item import DataItem

            self._data_item_cls = DataItem
        return self._data_item_cls(**kwargs)

    async def _ensure_setup(self) -> None:
        """Create Cognee's relational tables once per process (it raises DatabaseNotCreatedError otherwise)."""
        if self._needs_setup:
            from cognee.modules.engine.operations.setup import setup

            await setup()
            self._needs_setup = False

    async def _dataset_id(self, dataset_name: str):
        await self._ensure_setup()
        for dataset in await self._module().datasets.list_datasets():
            if dataset.name == dataset_name:
                return dataset.id
        return None

    async def _exists(self, dataset_id, data_id: uuid.UUID) -> bool:
        if dataset_id is None:
            return False
        rows = await self._module().datasets.list_data(dataset_id)
        return any(str(row.id) == str(data_id) for row in rows)

    async def _index_document(self, item_id, source_id, text, title, metadata) -> str:
        cognee = self._module()
        await self._ensure_setup()
        dataset_name = dataset_for(source_id)
        data_id = data_id_for(item_id)
        dataset_id = await self._dataset_id(dataset_name)

        if await self._exists(dataset_id, data_id):
            await cognee.update(data_id=data_id, data=text, dataset_id=dataset_id)
            action = "updated"
        else:
            item = self._make_data_item(
                data=text,
                label=title or item_id,
                external_metadata={"item_id": item_id, "source_id": source_id, **metadata},
                data_id=data_id,
                literal_text=True,  # the text is document content, never a URL/path to fetch
            )
            await cognee.add(item, dataset_name=dataset_name)
            action = "added"

        await cognee.cognify(datasets=[dataset_name])
        logger.info("cognee %s item_id=%s dataset=%s", action, item_id, dataset_name)
        return action

    async def _remove_document(self, item_id, source_id) -> bool:
        dataset_id = await self._dataset_id(dataset_for(source_id))
        data_id = data_id_for(item_id)
        if not await self._exists(dataset_id, data_id):
            return False
        await self._module().forget(data_id=data_id, dataset_id=dataset_id)
        return True
