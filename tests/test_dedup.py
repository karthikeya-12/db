from ingestion_pipeline.core.dedup import compute_sha256


def test_compute_sha256_stable():
    data = b"hello world"
    assert compute_sha256(data) == compute_sha256(data)


def test_compute_sha256_differs_for_different_content():
    assert compute_sha256(b"a") != compute_sha256(b"b")
