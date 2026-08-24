"""
Format-to-structured-content normalization, branched by content_type.

DOCUMENT (PDF/DOCX -> Markdown) is handled by DoclingDocumentNormalizer,
following the extraction spike's recommendation (see
spikes/pdf_extraction/comparison_report.md). Requires the optional
`docling` extra: pip install -e ".[docling]". NOTE: Docling's exact current
API/packaging could not be verified against its live docs in this session
(web search was unavailable throughout) -- the call below is written
against the documented DocumentConverter().convert(path).document
.export_to_markdown() interface from training-era knowledge. If Docling's
API has moved on, this is the one place to fix it; NotYetImplementedDocumentNormalizer
is kept below as a fallback stub for environments without the docling extra
installed.

TABULAR (CSV/XLSX -> structured rows) is implemented today -- it's the
minimal amount needed to prove out the TABULAR branch that never touches
chunking or embedding.
"""
from __future__ import annotations

import csv
import io
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

_PAGE_NUMBER_RE = re.compile(r"^\s*(page\s+)?\d+(\s*/\s*\d+)?\s*$", re.IGNORECASE)
_REPEATED_BLANK_RE = re.compile(r"\n{3,}")


class DocumentNormalizer(ABC):
    """Converts a document-shaped file's raw bytes to Markdown text."""

    @abstractmethod
    def to_markdown(self, raw_bytes: bytes, file_name: str) -> str:
        raise NotImplementedError


class NotYetImplementedDocumentNormalizer(DocumentNormalizer):
    """Fallback stub -- used only if the `docling` extra isn't installed."""

    def to_markdown(self, raw_bytes: bytes, file_name: str) -> str:
        raise NotImplementedError(
            "PDF/DOCX -> Markdown conversion requires the 'docling' extra -- "
            "run: pip install -e \".[docling]\". See spikes/pdf_extraction/ for "
            "the extraction comparison that led to choosing Docling."
        )


def clean_markdown(text: str) -> str:
    """
    Cleaning pass applied after Docling conversion, before chunking:
    mojibake repair + stripping standalone page-number/footer lines and
    collapsing runs of blank lines left behind by page breaks. Conceptually
    adapted from the reference pipeline's content_cleaner.py approach
    (repair encoding artifacts, strip page furniture) -- rewritten here
    since this project doesn't import everest_repo code directly.
    """
    try:
        import ftfy

        text = ftfy.fix_text(text)
    except ImportError:
        pass  # ftfy ships with the docling extra; skip mojibake repair if it's not installed

    cleaned_lines = [line for line in text.split("\n") if not _PAGE_NUMBER_RE.match(line)]
    text = "\n".join(cleaned_lines)
    text = _REPEATED_BLANK_RE.sub("\n\n", text)
    return text.strip()


class DoclingDocumentNormalizer(DocumentNormalizer):
    """
    PDF/DOCX -> Markdown via Docling, followed by clean_markdown(). Docling's
    converter takes a file path, not raw bytes, so raw_bytes is written to a
    temp file first.

    Uses delete=False + a manual close/unlink in finally, NOT
    `with NamedTemporaryFile(delete=True) as tmp:` -- that pattern keeps the
    file handle open for the duration of the with-block, and Windows (unlike
    Linux/Mac) locks a file exclusively while any handle on it is open, so
    Docling's own attempt to open that same path fails with
    PermissionError: [Errno 13]. Closing the handle before handing the path
    to Docling, then unlinking afterward, works on every OS.
    """

    def to_markdown(self, raw_bytes: bytes, file_name: str) -> str:
        from docling.document_converter import DocumentConverter

        suffix = Path(file_name).suffix or ".pdf"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(raw_bytes)
            tmp.close()  # release the OS lock before Docling opens the same path
            result = DocumentConverter().convert(tmp.name)
            markdown_text = result.document.export_to_markdown()
        finally:
            Path(tmp.name).unlink(missing_ok=True)

        return clean_markdown(markdown_text)


def get_document_normalizer() -> DocumentNormalizer:
    """Returns DoclingDocumentNormalizer if the docling extra is installed, else the stub."""
    try:
        import docling  # noqa: F401

        return DoclingDocumentNormalizer()
    except ImportError:
        return NotYetImplementedDocumentNormalizer()


class TabularParser:
    """Parses CSV/XLSX raw bytes into a list of row dicts. Never chunked, never embedded."""

    def parse(self, raw_bytes: bytes, file_name: str) -> list[dict]:
        if file_name.lower().endswith(".csv"):
            return self._parse_csv(raw_bytes)
        if file_name.lower().endswith(".xlsx"):
            return self._parse_xlsx(raw_bytes)
        raise ValueError(f"Unsupported tabular format: {file_name!r}")

    def _parse_csv(self, raw_bytes: bytes) -> list[dict]:
        text = raw_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]

    def _parse_xlsx(self, raw_bytes: bytes) -> list[dict]:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        header = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(next(rows_iter))]
        return [dict(zip(header, row)) for row in rows_iter]
