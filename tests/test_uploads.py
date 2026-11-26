import io

import pytest

from ingestion_pipeline.core.formats import classify
from ingestion_pipeline.core.types import ContentType
from ingestion_pipeline.core.uploads import UploadRejected, sanitize_file_name, save_upload


def test_classify_families():
    assert classify("a.PDF") == ContentType.DOCUMENT
    assert classify("notes.txt") == ContentType.DOCUMENT
    assert classify("data.csv") == ContentType.TABULAR
    assert classify("scan.PNG") == ContentType.DOCUMENT
    assert classify("old.doc") is None
    assert classify("archive.zip") is None
    assert classify("noext") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\me\\a b.docx", "a b.docx"),
        ('we<ird>:name?.md', "we_ird__name_.md"),
        ("  .hidden.txt ", "hidden.txt"),
    ],
)
def test_sanitize(raw, expected):
    assert sanitize_file_name(raw) == expected


def test_sanitize_rejects_empty():
    with pytest.raises(UploadRejected):
        sanitize_file_name("  ../ ")


def test_sanitize_caps_length_keeping_extension():
    name = sanitize_file_name("x" * 400 + ".pdf")
    assert len(name) <= 150 and name.endswith(".pdf")


def test_save_ok_and_overwrite(tmp_path):
    saved = save_upload(tmp_path, "a.md", io.BytesIO(b"# hi"), 1024)
    assert saved.item_id == "upload:a.md" and (tmp_path / "a.md").read_bytes() == b"# hi"
    save_upload(tmp_path, "a.md", io.BytesIO(b"# changed"), 1024)
    assert (tmp_path / "a.md").read_bytes() == b"# changed"
    assert [p.name for p in tmp_path.iterdir()] == ["a.md"]  # no temp files left behind


def test_save_rejections_leave_nothing_behind(tmp_path):
    with pytest.raises(UploadRejected) as unsupported:
        save_upload(tmp_path, "x.zip", io.BytesIO(b"zz"), 1024)
    assert unsupported.value.status_code == 415
    with pytest.raises(UploadRejected) as too_big:
        save_upload(tmp_path, "big.txt", io.BytesIO(b"x" * 2048), 1024)
    assert too_big.value.status_code == 413
    with pytest.raises(UploadRejected):
        save_upload(tmp_path, "empty.txt", io.BytesIO(b""), 1024)
    assert list(tmp_path.iterdir()) == []
