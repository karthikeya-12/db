"""Saving uploaded files safely: name sanitising, format and size checks, atomic writes."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ingestion_pipeline.core.formats import SUPPORTED_EXTENSIONS, classify

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_NAME_LENGTH = 150
_CHUNK = 1024 * 1024


class UploadRejected(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SavedUpload:
    file_name: str
    item_id: str
    size_bytes: int


def sanitize_file_name(raw_name: str | None) -> str:
    """Keep only the base name, drop characters that are unsafe on Windows/Linux, cap the length."""
    name = (raw_name or "").replace("\\", "/").split("/")[-1].strip()
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    if not name:
        raise UploadRejected("file has no usable name")
    if len(name) > MAX_NAME_LENGTH:
        stem, suffix = Path(name).stem, Path(name).suffix
        name = stem[: MAX_NAME_LENGTH - len(suffix)] + suffix
    return name


def save_upload(folder: str | Path, raw_name: str | None, stream: BinaryIO, max_bytes: int, item_prefix: str = "upload") -> SavedUpload:
    """Validate and store one upload. The file only appears under its final name once complete,
    so a scan never sees a half-written file."""
    name = sanitize_file_name(raw_name)
    if classify(name) is None:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UploadRejected(f"unsupported format '{Path(name).suffix or name}'. Supported: {supported}", 415)

    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    handle, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".upload-", suffix=".part")
    size = 0
    try:
        with os.fdopen(handle, "wb") as out:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadRejected(f"file is larger than the {max_bytes // (1024 * 1024)} MB limit", 413)
                out.write(chunk)
        if size == 0:
            raise UploadRejected("file is empty")
        os.replace(tmp_path, target_dir / name)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    return SavedUpload(file_name=name, item_id=f"{item_prefix}:{name}", size_bytes=size)
