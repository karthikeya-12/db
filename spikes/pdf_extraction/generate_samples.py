"""Generate a representative synthetic sample set for the extraction spike.

No real-world documents were available in this session, so these are
synthetic stand-ins built to exercise the same structural challenges real
documents would: headings, paragraphs, multi-column layout, tables, and
scanned/image-only text (no text layer).
"""
import io
from pathlib import Path

from docx import Document
from docx.shared import Pt
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    Frame,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet

OUT = Path(__file__).parent / "samples"
OUT.mkdir(exist_ok=True)

styles = getSampleStyleSheet()


def make_text_native_pdf():
    path = OUT / "text_native.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    story = [
        Paragraph("INGESTION PIPELINE OVERVIEW", styles["Title"]),
        Spacer(1, 12),
        Paragraph("1. INTRODUCTION", styles["Heading1"]),
        Paragraph(
            "This document describes a single-source ingestion pipeline that fetches, "
            "deduplicates, normalizes, chunks, embeds, and persists content from an "
            "uploaded-documents data source. " * 3,
            styles["BodyText"],
        ),
        Spacer(1, 12),
        Paragraph("2. ARCHITECTURE", styles["Heading1"]),
        Paragraph(
            "The orchestrator drives every adapter through a fixed stage sequence: "
            "checkpoint, list_changes, fetch, archive, dedup-check, normalize, chunk, "
            "embed, persist, publish-event, advance-cursor. " * 3,
            styles["BodyText"],
        ),
        Spacer(1, 12),
        Paragraph("2.1 Source Adapters", styles["Heading2"]),
        Paragraph(
            "A new data source is onboarded by writing only an Adapter implementation; "
            "the orchestrator itself never changes. " * 3,
            styles["BodyText"],
        ),
        Spacer(1, 300),
        Paragraph("Page 1", styles["Normal"]),
    ]
    doc.build(story)
    print(f"wrote {path}")


def make_multi_column_pdf():
    path = OUT / "multi_column.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    frame1 = Frame(0.5 * inch, 0.5 * inch, 3.5 * inch, 9.5 * inch, id="col1")
    frame2 = Frame(4.25 * inch, 0.5 * inch, 3.5 * inch, 9.5 * inch, id="col2")
    doc.addPageTemplates([PageTemplate(id="TwoCol", frames=[frame1, frame2])])
    left_text = (
        "LEFT COLUMN HEADING<br/><br/>" +
        "This is left-column content describing the dedup registry and how content "
        "fingerprints are registered only after a successful persist. " * 6
    )
    right_text = (
        "RIGHT COLUMN HEADING<br/><br/>" +
        "This is right-column content describing the chunking strategies used for "
        "documents, transcripts, and messages. " * 6
    )
    story = [Paragraph(left_text, styles["BodyText"]), Paragraph(right_text, styles["BodyText"])]
    doc.build(story)
    print(f"wrote {path}")


def make_table_heavy_pdf():
    path = OUT / "table_heavy.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    data = [["Task", "Status", "Progress"]]
    tasks = [
        ("Core orchestrator", "In progress", "60%"),
        ("Postgres schema", "In progress", "80%"),
        ("Local dev environment", "Completed", "100%"),
        ("Blob storage abstraction", "In progress", "60%"),
        ("Chunking framework", "In progress", "60%"),
        ("Embedding service", "To Do", "0%"),
        ("Event publishing", "In progress", "50%"),
        ("Secrets management", "To Do", "0%"),
        ("Retry & dead-letter queue", "In progress", "30%"),
        ("Observability", "To Do", "0%"),
    ]
    data.extend(tasks)
    table = Table(data, colWidths=[3 * inch, 1.5 * inch, 1.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story = [Paragraph("TASK STATUS TABLE", styles["Title"]), Spacer(1, 12), table]
    doc.build(story)
    print(f"wrote {path}")


def make_scanned_image_pdf():
    """No text layer -- renders text as a raster image only, like a scanned page."""
    path = OUT / "scanned_image.pdf"
    img = Image.new("RGB", (1700, 2200), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
        font_small = font
    draw.text((100, 100), "SCANNED DOCUMENT SAMPLE", font=font, fill="black")
    lines = [
        "This page has no embedded text layer -- it is a raster image only,",
        "simulating a scanned or photographed document. Extraction tools",
        "without OCR should fail or return empty text for this sample.",
        "",
        "Section: Dead Letter Queue",
        "Items that exhaust their retry budget are written to the",
        "dead_letter_items table instead of being dropped silently.",
    ]
    y = 220
    for line in lines:
        draw.text((100, y), line, font=font_small, fill="black")
        y += 50

    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PDF")
    path.write_bytes(img_bytes.getvalue())
    print(f"wrote {path}")


def make_plain_docx():
    path = OUT / "plain.docx"
    doc = Document()
    doc.add_heading("Ingestion Pipeline - Meeting Notes", level=1)
    doc.add_heading("Attendees", level=2)
    doc.add_paragraph("Mani, Amal, Rajeev")
    doc.add_heading("Discussion", level=2)
    doc.add_paragraph(
        "Discussed the fixed pipeline stage order and confirmed the orchestrator "
        "should be the only component aware of it. Adapters should contain zero "
        "pipeline logic beyond fetching and metadata extraction."
    )
    doc.add_paragraph(
        "Also discussed the cache-before-embed pattern for the embedder, and "
        "agreed dedup hashes should only be registered after a successful persist."
    )
    doc.add_heading("Action Items", level=2)
    for item in ["Wire retry policy into fetch/embed calls", "Add pipeline_runs table", "Run extraction spike"]:
        doc.add_paragraph(item, style="List Bullet")
    doc.save(path)
    print(f"wrote {path}")


def make_table_docx():
    path = OUT / "table.docx"
    doc = Document()
    doc.add_heading("Connector Checkpoint Reference", level=1)
    doc.add_paragraph("One row per source adapter, tracking the cursor/watermark for incremental sync.")
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Column", "Type", "Notes"
    rows = [
        ("source_id", "TEXT PK", "e.g. 'uploaded_docs'"),
        ("cursor_value", "TEXT", "opaque, adapter-defined format"),
        ("cursor_meta", "JSONB", "optional extra state"),
        ("updated_at", "TIMESTAMPTZ", "auto-touched on update"),
    ]
    for col, typ, note in rows:
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = col, typ, note
    doc.save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    make_text_native_pdf()
    make_multi_column_pdf()
    make_table_heavy_pdf()
    make_scanned_image_pdf()
    make_plain_docx()
    make_table_docx()
