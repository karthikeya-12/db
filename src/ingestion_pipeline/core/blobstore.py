"""
Raw-bytes archive. Every adapter writes source bytes through this before any
processing begins -- the archive is immutable and independent of whatever
happens downstream, so the original file is always recoverable for
debugging or reprocessing.

LocalFileSystemBlobStore is the local-dev backend. An S3-backed
implementation is a drop-in replacement behind the same interface --
adapters and the orchestrator never construct a backend directly, they
receive one (see config.py / get_blob_store()).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BlobStore(ABC):
    @abstractmethod
    def put(self, path: str, data: bytes) -> str:
        """Write `data` at `path` (if not already present) and return a ref string."""
        raise NotImplementedError

    @abstractmethod
    def get(self, ref: str) -> bytes:
        """Read back bytes previously written under `ref`."""
        raise NotImplementedError


class BlobAlreadyExistsError(Exception):
    """Raised when put() targets a path already archived with *different* content."""


class LocalFileSystemBlobStore(BlobStore):
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, path: str, data: bytes) -> str:
        target = self._root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Immutability: never overwrite an existing archive. Re-archiving the
            # same path with identical bytes is a no-op success (idempotent
            # retry), not an error -- only a genuine content mismatch is a conflict.
            if target.read_bytes() != data:
                raise BlobAlreadyExistsError(f"{path} already archived with different content")
            return f"local://{path}"
        target.write_bytes(data)
        return f"local://{path}"

    def get(self, ref: str) -> bytes:
        if not ref.startswith("local://"):
            raise ValueError(f"Not a local ref: {ref!r}")
        path = ref[len("local://"):]
        return (self._root / path).read_bytes()


def get_blob_store() -> BlobStore:
    from ingestion_pipeline.config import get_settings

    settings = get_settings()
    if settings.blob_store_backend == "local":
        return LocalFileSystemBlobStore(settings.blob_store_local_root)
    raise NotImplementedError(
        f"blob_store_backend={settings.blob_store_backend!r} has no implementation yet "
        "(an S3-backed BlobStore is planned alongside the S3 adapter)."
    )
