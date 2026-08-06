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
from ingestion_pipeline.core.types import ContentType, RawItem

_EXT_TO_CONTENT_TYPE = {
    ".csv": ContentType.TABULAR,
    ".xlsx": ContentType.TABULAR,
    ".txt": ContentType.DOCUMENT,
    ".md": ContentType.DOCUMENT,
}


class LocalFolderAdapter(Adapter):
    source_id = "local_folder"

    def __init__(self, folder: str | Path) -> None:
        self._folder = Path(folder)
        self._folder.mkdir(parents=True, exist_ok=True)

    def list_changes(self, cursor: str | None) -> list[Path]:
        since = float(cursor) if cursor else 0.0
        return sorted(p for p in self._folder.iterdir() if p.is_file() and p.stat().st_mtime > since)

    def fetch(self, ref: Path) -> RawItem:
        suffix = ref.suffix.lower()
        content_type = _EXT_TO_CONTENT_TYPE.get(suffix)
        if content_type is None:
            raise ValueError(f"Unsupported extension for local demo adapter: {suffix!r}")
        return RawItem(
            source_id=self.source_id,
            item_id=f"local:{ref.name}",
            content_type=content_type,
            raw_bytes=ref.read_bytes(),
            source_metadata={"path": str(ref), "mtime": ref.stat().st_mtime},
            file_name=ref.name,
        )

    def advance_cursor(self, ref: Path) -> str:
        return str(max(ref.stat().st_mtime, time.time() - 1))
