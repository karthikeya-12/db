from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import json

from openpyxl import Workbook

from ingestion_pipeline.core.normalizer import TabularParser


def xlsx_bytes(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_empty_xlsx_sheet_gives_no_rows():
    assert TabularParser().parse(xlsx_bytes([]), "empty.xlsx") == []


def test_xlsx_dates_and_decimals_are_json_serializable_and_blank_rows_skipped():
    data = xlsx_bytes(
        [
            ["name", "when", "amount"],
            ["a", datetime(2026, 1, 2, 3, 4), Decimal("1.5")],
            [None, None, None],
            ["b", date(2026, 5, 6), float("nan")],
        ]
    )
    rows = TabularParser().parse(data, "t.xlsx")
    assert len(rows) == 2
    assert rows[0]["when"] == "2026-01-02T03:04:00"
    assert rows[1]["amount"] is None
    json.dumps(rows, allow_nan=False)  # what Postgres JSONB requires


def test_csv_with_extra_cells_and_windows_encoding():
    csv_bytes = "name,city\nAna,Zürich,extra\n".encode("cp1252")
    rows = TabularParser().parse(csv_bytes, "x.csv")
    assert rows == [{"name": "Ana", "city": "Zürich", "col_2": "extra"}]
    json.dumps(rows, allow_nan=False)
