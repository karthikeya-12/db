# PDF/DOCX Extraction Tooling Spike

Goal: decide which library the "Convert document-shaped formats to markdown"
task should be built on, by comparing at least two candidate approaches
against real sample documents. This is a spike, not production code -- the
deliverable is a written comparison and a recommendation.

## How to run

1. Drop 5-10 representative sample files into `samples/` -- mix text-native
   PDFs, scanned/image PDFs, multi-column layouts, documents with tables, and
   at least one DOCX file.
2. Install the spike-only dependencies: `pip install -e ".[spike]"` (run from the repo root)
3. Run: `python compare_extractors.py samples/`
4. The script prints progress and writes `comparison_report.md` in this folder.

## Scoring rubric (fill in manually after reading each output)

| Criterion | What to check |
|---|---|
| Heading structure preserved | Are `#`/`##` headings present and correctly nested? |
| Tables reconstructed legibly | Do tables render as readable Markdown tables, not flattened text? |
| No missing paragraphs | Does the output contain everything the source page does? |
| No garbled text | Any mojibake, OCR gibberish, or encoding artifacts? |

## Candidates wired in by default

- **pymupdf4llm** -- fast, layout-aware Markdown output; generally strong on text-native PDFs.
- **pypdf + heuristic headings** -- simpler baseline: raw text extraction, headings
  inferred from font-size/all-caps/line-length heuristics.

Docling was the tool named in the task brief. It wasn't wired in as a default
candidate here because its exact current packaging/dependencies should be
verified against its own docs before adding it to `pyproject.toml` (this
repo's own web search was unavailable at the time this spike harness was
built -- see `compare_extractors.py` for where to add it as a third
`ExtractionCandidate` once you've confirmed the install path).

## Deliverable

`comparison_report.md` in this folder is the filled-in deliverable --
results, scoring rubric, and recommendation from an actual run.

## About the sample set

No real-world documents were available in this session, so the 6 samples in
`samples/` were generated synthetically (`generate_samples.py`) to exercise
the same structural challenges: headings/paragraphs (`text_native.pdf`),
a two-column layout (`multi_column.pdf`), a data table (`table_heavy.pdf`),
a page with no text layer at all (`scanned_image.pdf`, forces OCR), and two
DOCX files (`plain.docx`, `table.docx`). See `comparison_report.md`'s
"Caveat: synthetic samples" section for what this does and doesn't validate
-- re-running this spike against a handful of real documents is worth doing
before fully committing to a conversion approach.
