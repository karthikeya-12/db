"""
Which file formats the pipeline can ingest, and how each one is read.

Three families:
  * TEXT   -- plain-text-like files, decoded directly (no conversion needed).
  * DOCLING -- rich documents and images that Docling converts to Markdown (OCR for images).
  * TABULAR -- CSV/XLSX, parsed into rows in Postgres (not sent through Cognee).

Anything else is refused up front with a clear message, rather than failing later in the pipeline.
"""
from __future__ import annotations

from pathlib import Path

from ingestion_pipeline.core.types import ContentType

TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".rst", ".log", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
     ".xml", ".tex", ".srt", ".vtt"}
)
DOCLING_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".html", ".htm", ".xhtml", ".adoc", ".asciidoc",
     ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
)
TABULAR_EXTENSIONS = frozenset({".csv", ".xlsx"})

SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCLING_EXTENSIONS | TABULAR_EXTENSIONS


def classify(file_name: str) -> ContentType | None:
    """The content type for a file name, or None if the format is not supported."""
    suffix = Path(file_name).suffix.lower()
    if suffix in TABULAR_EXTENSIONS:
        return ContentType.TABULAR
    if suffix in TEXT_EXTENSIONS or suffix in DOCLING_EXTENSIONS:
        return ContentType.DOCUMENT
    return None


def is_text_file(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in TEXT_EXTENSIONS


def decode_text(raw_bytes: bytes) -> str:
    """Decode text of unknown encoding: UTF-8 (with BOM), then UTF-16 if it has a BOM, then cp1252."""
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw_bytes.decode("utf-16", errors="replace")
    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252", errors="replace")
