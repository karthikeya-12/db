from ingestion_pipeline.core.dedup import compute_sha256


def test_compute_sha256_stable():
    data = b"hello world"
    assert compute_sha256(data) == compute_sha256(data)


def test_compute_sha256_differs_for_different_content():
    assert compute_sha256(b"a") != compute_sha256(b"b")


def test_archive_path_segments_are_valid_on_windows():
    from ingestion_pipeline.core.orchestrator import safe_path_segment

    assert safe_path_segment("nango:google-mail:abc-123") == "nango_google-mail_abc-123"
    assert safe_path_segment('a/b\\c|d?e*"f<g>h') == "a_b_c_d_e__f_g_h"
    assert safe_path_segment("name with newline\n.") == "name with newline_"
    assert safe_path_segment("...") == "_"
