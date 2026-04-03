from __future__ import annotations

import json
from pathlib import Path

from snapgit.ocr_benchmark.category_frequency_summary import (
    _extract_categories,
    summarize_category_frequency,
)


def test_extract_categories_deduplicates_and_normalizes() -> None:
    row = {
        "categories": [
            {"pl": " Gra ", "en": " Game "},
            {"pl": "gra", "en": "game"},
            {"pl": "Sklep", "en": "Shop"},
        ]
    }
    pairs = _extract_categories(row)
    assert pairs == [("gra", "game"), ("sklep", "shop")]


def test_extract_categories_supports_categories_json_fallback() -> None:
    row = {
        "categories_json": json.dumps(
            [
                {"pl": "Bankowość", "en": "Banking"},
                {"pl": "Płatność", "en": "Payment"},
            ],
            ensure_ascii=False,
        )
    }
    pairs = _extract_categories(row)
    assert pairs == [("bankowość", "banking"), ("płatność", "payment")]


def test_summarize_category_frequency_sorts_rarest_first(tmp_path: Path) -> None:
    jsonl = tmp_path / "results.jsonl"
    rows = [
        {
            "status": "ok",
            "categories": [
                {"pl": "gra", "en": "game"},
                {"pl": "social media", "en": "social media"},
            ],
        },
        {
            "status": "ok",
            "categories": [
                {"pl": "bankowość", "en": "banking"},
                {"pl": "social media", "en": "social media"},
            ],
        },
        {
            "status": "error",
            "categories": [
                {"pl": "gra", "en": "game"},
            ],
        },
    ]
    jsonl.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")

    summary = summarize_category_frequency(input_jsonl=jsonl, include_status="ok")
    assert summary["rows_total"] == 3
    assert summary["rows_included"] == 2
    assert summary["pairs"] == [
        {"pl": "bankowość", "en": "banking", "count": 1},
        {"pl": "gra", "en": "game", "count": 1},
        {"pl": "social media", "en": "social media", "count": 2},
    ]
