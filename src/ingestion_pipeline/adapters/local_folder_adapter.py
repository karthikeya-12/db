"""
A minimal demo Adapter used to exercise the orchestrator end-to-end locally,
without needing S3/AWS credentials. Drop files into `data/incoming/` and run
the pipeline -- each file becomes one RawItem.

This is NOT the S3 upload adapter (a separate, not-yet-built task); it
exists only to prove the orchestrator's "onboard a new source by writing
only an Adapter" contract without any cloud dependency.
"""
from __future__ import annotations

import time
from pathlib import Path

from ingestion_pipeline.core.adapter import Adapter
from ingestion_pipeline.core.formats import classify
from ingestion_pipeline.core.types import RawItem

class LocalFolderAdapter(Adapter):
    source_id = "local_folder"
    item_prefix = "local"

    def __init__(self, folder: str | Path) -> None:
        self._folder = Path(folder)
        self._folder.mkdir(parents=True, exist_ok=True)

    def list_changes(self, cursor: str | None) -> list[Path]:
        # The cursor is deliberately ignored. An mtime watermark silently skips files whose
        # modified time is older than the last run -- on Windows a copied file keeps its original
        # mtime -- so every supported file is listed and the orchestrator's per-item content-hash
        # check skips the unchanged ones cheaply.
        return sorted(
            p for p in self._folder.iterdir() if p.is_file() and classify(p.name) is not None
        )

    def fetch(self, ref: Path) -> RawItem:
        content_type = classify(ref.name)
        if content_type is None:
            raise ValueError(f"Unsupported file format: {ref.suffix!r}")
        return RawItem(
            source_id=self.source_id,
            item_id=f"{self.item_prefix}:{ref.name}",
            content_type=content_type,
            raw_bytes=ref.read_bytes(),
            source_metadata={"path": str(ref), "mtime": ref.stat().st_mtime},
            file_name=ref.name,
        )

    def advance_cursor(self, ref: Path) -> str:
        return str(time.time())  # informational only -- list_changes ignores the cursor
