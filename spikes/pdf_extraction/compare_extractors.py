"""
Runs each registered extraction candidate against every sample file and
writes a comparison report. See README.md in this folder for the scoring
rubric and how to add another candidate (e.g. Docling).

Usage:
    python compare_extractors.py samples/
"""
from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path


class ExtractionCandidate(ABC):
    name: str

    @abstractmethod
    def extract(self, file_path: Path) -> str:
        """Return Markdown (or Markdown-ish) text for one file. Raise on unsupported input."""
        raise NotImplementedError


class PyMuPDF4LLMCandidate(ExtractionCandidate):
    name = "pymupdf4llm"

    def extract(self, file_path: Path) -> str:
        import pymupdf4llm  # optional dependency -- pip install -e ".[spike]"

        return pymupdf4llm.to_markdown(str(file_path))


class SimplePyPDFCandidate(ExtractionCandidate):
    name = "pypdf+heuristic-headings"

    def extract(self, file_path: Path) -> str:
        from pypdf import PdfReader  # optional dependency

        reader = PdfReader(str(file_path))
        lines: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    lines.append("")
                    continue
                if self._looks_like_heading(stripped):
                    lines.append(f"## {stripped}")
                else:
                    lines.append(stripped)
        return "\n".join(lines)

    @staticmethod
    def _looks_like_heading(line: str) -> bool:
        return len(line) < 80 and (line.isupper() or line.endswith(":"))


# Add Docling (or any other candidate) here following the same pattern once
# its install path is confirmed -- see README.md.
CANDIDATES: list[ExtractionCandidate] = [PyMuPDF4LLMCandidate(), SimplePyPDFCandidate()]


def main(samples_dir: str) -> None:
    samples = sorted(p for p in Path(samples_dir).glob("*") if p.suffix.lower() in (".pdf", ".docx"))
    if not samples:
        print(f"No PDF/DOCX sample files found in {samples_dir!r} -- drop 5-10 real documents there first.")
        return

    report_lines = ["# Extraction Comparison Report", ""]
    for sample in samples:
        report_lines.append(f"## {sample.name}")
        for candidate in CANDIDATES:
            start = time.monotonic()
            try:
                output = candidate.extract(sample)
                elapsed = time.monotonic() - start
                preview = output[:500].replace("\n", " ")
                report_lines.append(f"- **{candidate.name}** -- {elapsed:.2f}s, {len(output)} chars. Preview: `{preview}...`")
            except Exception as exc:
                report_lines.append(f"- **{candidate.name}** -- FAILED: {exc}")
        report_lines.append("")

    report_path = Path(__file__).parent / "comparison_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python compare_extractors.py <samples_dir>")
        sys.exit(1)
    main(sys.argv[1])
