from __future__ import annotations

from typing import Optional


def choose_captured_at(
    payload_capture_time: Optional[str],
    embedded_timestamp: Optional[str],
    source_file_mtime: Optional[str],
    ingested_at: Optional[str],
) -> Optional[str]:
    for candidate in (
        payload_capture_time,
        embedded_timestamp,
        source_file_mtime,
        ingested_at,
    ):
        if candidate not in (None, ""):
            return candidate
    return None
