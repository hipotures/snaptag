from __future__ import annotations


def build_fts_document(ocr_text: str, title: str | None = None) -> str:
    return " ".join(part for part in (title, ocr_text) if part)
