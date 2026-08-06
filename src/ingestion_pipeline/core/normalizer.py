"""
Format-to-structured-content normalization, branched by content_type.

DOCUMENT (PDF/DOCX -> Markdown via Docling) is a separate, not-yet-built
task -- NotYetImplementedDocumentNormalizer below raises on purpose so the
orchestrator's DOCUMENT branch is a visible TODO rather than silently wrong.
See spikes/pdf_extraction/ for the tooling comparison that will decide what
replaces this class.

TABULAR (CSV/XLSX -> structured rows) is implemented today -- it's the
minimal amount needed to prove out the TABULAR branch that never touches
chunking or embedding.
"""
from __future__ import annotations

import csv
import io
from abc import ABC, abstractmethod


class DocumentNormalizer(ABC):
    """Converts a document-shaped file's raw bytes to Markdown text."""

    @abstractmethod
    def to_markdown(self, raw_bytes: bytes, file_name: str) -> str:
        raise NotImplementedError


class NotYetImplementedDocumentNormalizer(DocumentNormalizer):
    """Placeholder -- Docling integration is a separate, not-yet-built task."""

    def to_markdown(self, raw_bytes: bytes, file_name: str) -> str:
        raise NotImplementedError(
            "PDF/DOCX -> Markdown conversion (Docling) is not implemented yet -- "
            "see the extraction spike under spikes/pdf_extraction/."
        )


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
