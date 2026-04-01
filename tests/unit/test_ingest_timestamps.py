from snapgit.ingest.service import choose_captured_at


def test_choose_captured_at_priority():
    ts = choose_captured_at(
        payload_capture_time="2026-04-01T10:00:00Z",
        embedded_timestamp="2026-03-01T10:00:00Z",
        source_file_mtime="2026-02-01T10:00:00Z",
        ingested_at="2026-01-01T10:00:00Z",
    )
    assert ts == "2026-04-01T10:00:00Z"


def test_choose_captured_at_falls_back_when_payload_missing():
    ts = choose_captured_at(
        payload_capture_time=None,
        embedded_timestamp="2026-03-01T10:00:00Z",
        source_file_mtime="2026-02-01T10:00:00Z",
        ingested_at="2026-01-01T10:00:00Z",
    )
    assert ts == "2026-03-01T10:00:00Z"


def test_choose_captured_at_skips_empty_strings_in_priority_order():
    ts = choose_captured_at(
        payload_capture_time="",
        embedded_timestamp="",
        source_file_mtime="2026-02-01T10:00:00Z",
        ingested_at="2026-01-01T10:00:00Z",
    )
    assert ts == "2026-02-01T10:00:00Z"


def test_choose_captured_at_returns_none_when_all_values_missing():
    ts = choose_captured_at(
        payload_capture_time=None,
        embedded_timestamp="",
        source_file_mtime=None,
        ingested_at="",
    )
    assert ts is None
