"""Cognee indexer + consumer, with a fake `cognee` module (real Cognee needs OpenAI + Neo4j)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("pgserver")

from sqlalchemy import select  # noqa: E402

# reuse the module-scoped Postgres + fakes from the pipeline integration tests
from tests.test_pipeline_integration import (  # noqa: E402,F401
    FakeNormalizer,
    ListAdapter,
    _clean_tables,
    _database,
    make_orchestrator,
    words,
)

from ingestion_pipeline.core.cognee_indexer import CogneeIndexer, data_id_for, dataset_for  # noqa: E402


class FakeCognee:
    """Records calls; keeps per-dataset documents in memory like Cognee's dataset/data tables."""

    def __init__(self, fail_prefix: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.datasets_store: dict[str, dict] = {}  # name -> {"id": uuid, "data": {data_id: text}}
        self.fail_prefix = fail_prefix  # documents whose text starts with this fail to index
        self.datasets = SimpleNamespace(list_datasets=self._list_datasets, list_data=self._list_data)

    async def _list_datasets(self):
        return [SimpleNamespace(name=n, id=d["id"]) for n, d in self.datasets_store.items()]

    async def _list_data(self, dataset_id):
        for d in self.datasets_store.values():
            if d["id"] == dataset_id:
                return [SimpleNamespace(id=i) for i in d["data"]]
        return []

    async def add(self, item, dataset_name):
        if self.fail_prefix and item.data.startswith(self.fail_prefix):
            raise RuntimeError("llm unavailable")
        store = self.datasets_store.setdefault(dataset_name, {"id": uuid.uuid4(), "data": {}})
        store["data"][item.data_id] = item.data
        self.calls.append(("add", dataset_name, item.data_id, item.literal_text))

    async def update(self, data_id, data, dataset_id):
        for d in self.datasets_store.values():
            if d["id"] == dataset_id:
                d["data"][data_id] = data
        self.calls.append(("update", data_id, dataset_id))

    async def cognify(self, datasets):
        self.calls.append(("cognify", tuple(datasets)))

    async def forget(self, data_id, dataset_id):
        for d in self.datasets_store.values():
            if d["id"] == dataset_id:
                d["data"].pop(data_id, None)
        self.calls.append(("forget", data_id))


class FakeDataItem:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def indexer_with(fake):
    return CogneeIndexer(cognee_module=fake, data_item_cls=FakeDataItem)


def test_ids_and_dataset_names_are_stable_and_safe():
    assert data_id_for("local:a.txt") == data_id_for("local:a.txt") != data_id_for("local:b.txt")
    assert dataset_for("nango:Google Drive/abc-1") == "nango_google_drive_abc_1"


def test_first_version_is_added_then_later_versions_are_updated_then_forgotten():
    fake = FakeCognee()
    indexer = indexer_with(fake)
    assert indexer.index_document(item_id="local:a.txt", source_id="local_folder", text="v1") == "added"
    assert indexer.index_document(item_id="local:a.txt", source_id="local_folder", text="v2") == "updated"
    kinds = [call[0] for call in fake.calls]
    assert kinds == ["add", "cognify", "update", "cognify"]
    assert fake.calls[0][3] is True  # literal_text: document text is never treated as a URL/path
    assert indexer.remove_document(item_id="local:a.txt", source_id="local_folder") is True
    assert indexer.remove_document(item_id="local:a.txt", source_id="local_folder") is False


def test_normalized_text_is_archived_and_the_consumer_indexes_it(tmp_path):
    from ingestion_pipeline.consumers import cognee_consumer
    from ingestion_pipeline.core.blobstore import LocalFileSystemBlobStore

    adapter = ListAdapter({"a.txt": words("w", 30)})
    make_orchestrator(adapter, tmp_path).run()

    fake = FakeCognee()
    summary = cognee_consumer.run_once(indexer=indexer_with(fake), blob_store=LocalFileSystemBlobStore(tmp_path / "blobs"))
    assert summary["indexed"] == 1
    assert fake.datasets_store["test_src"]["data"][data_id_for("test_src:a.txt")] == words("w", 30).decode()
    assert cognee_consumer.run_once(indexer=indexer_with(fake), blob_store=LocalFileSystemBlobStore(tmp_path / "blobs"))["events_seen"] == 0


def test_a_failing_document_retries_then_is_dead_lettered_without_blocking_the_queue(tmp_path):
    from ingestion_pipeline.consumers import cognee_consumer
    from ingestion_pipeline.core import jobs
    from ingestion_pipeline.core.blobstore import LocalFileSystemBlobStore
    from ingestion_pipeline.db.models import DeadLetterItem
    from ingestion_pipeline.db.session import session_scope

    adapter = ListAdapter({"bad.txt": words("a", 20), "good.txt": words("b", 20)})
    make_orchestrator(adapter, tmp_path).run()
    blobs = LocalFileSystemBlobStore(tmp_path / "blobs")

    fake = FakeCognee(fail_prefix="a0")  # bad.txt's text starts with a0; it keeps failing
    first = cognee_consumer.run_once(indexer=indexer_with(fake), blob_store=blobs)
    assert first["indexed"] == 0  # stopped at the failing event (good.txt not attempted yet)
    cognee_consumer.run_once(indexer=indexer_with(fake), blob_store=blobs)
    third = cognee_consumer.run_once(indexer=indexer_with(fake), blob_store=blobs)  # 3rd attempt: out of attempts
    assert third["failed"] == 1 and third["indexed"] == 1  # bad.txt dead-lettered; good.txt NOT blocked behind it

    with session_scope() as session:
        row = session.execute(select(DeadLetterItem).where(DeadLetterItem.stage_failed == "cognee-index")).scalar_one()
        assert row.item_id == "test_src:bad.txt" and row.attempt_count == 3
        assert jobs.requeue_dead_letter(session, row.id) is None  # republishes the event, queues no ingest job

    fake.fail_prefix = None  # the outage is over
    fifth = cognee_consumer.run_once(indexer=indexer_with(fake), blob_store=blobs)
    assert fifth["indexed"] == 1 and data_id_for("test_src:bad.txt") in fake.datasets_store["test_src"]["data"]


def test_tabular_items_are_not_sent_to_cognee(tmp_path):
    from ingestion_pipeline.consumers import cognee_consumer
    from ingestion_pipeline.core import events
    from ingestion_pipeline.core.blobstore import LocalFileSystemBlobStore
    from ingestion_pipeline.db.models import IngestionItem
    from ingestion_pipeline.db.session import session_scope

    with session_scope() as session:
        session.add(IngestionItem(item_id="t:1", source_id="t", content_type="TABULAR"))
        session.flush()
        events.publish(session, events.TOPIC_ITEM_INGESTED, "t:1", {"item_id": "t:1", "source_id": "t", "content_type": "TABULAR"})
    fake = FakeCognee()
    summary = cognee_consumer.run_once(indexer=indexer_with(fake), blob_store=LocalFileSystemBlobStore(tmp_path))
    assert summary["skipped"] == 1 and fake.calls == []
