from __future__ import annotations

from snapgit.ocr_benchmark.paddle_benchmark import (
    build_paddle_init_kwargs,
    compute_error_rates,
    extract_lines_from_result,
    normalize_for_scoring,
)


def test_normalize_for_scoring_lowercases_and_compacts_spaces() -> None:
    assert normalize_for_scoring("  Ala   Ma \n KOTA\t") == "ala ma kota"


def test_compute_error_rates_returns_zero_for_identical_text() -> None:
    metrics = compute_error_rates("Ala ma kota", "ala   ma   kota")
    assert metrics["cer"] == 0.0
    assert metrics["wer"] == 0.0


def test_compute_error_rates_detects_missing_word() -> None:
    metrics = compute_error_rates("Ala ma kota", "Ala kota")
    assert metrics["cer"] > 0.0
    assert metrics["wer"] > 0.0


def test_extract_lines_from_legacy_paddleocr_shape() -> None:
    raw = [
        [
            [[[0, 0], [1, 0], [1, 1], [0, 1]], ("Hello", 0.95)],
            [[[0, 2], [1, 2], [1, 3], [0, 3]], ("World", 0.90)],
        ]
    ]
    lines = extract_lines_from_result(raw)

    assert [line["text"] for line in lines] == ["Hello", "World"]
    assert lines[0]["confidence"] == 0.95
    assert lines[1]["confidence"] == 0.90


def test_extract_lines_from_dict_shape() -> None:
    raw = {
        "rec_texts": ["Ala", "ma", "kota"],
        "rec_scores": [0.91, 0.89, 0.93],
        "dt_polys": [
            [[0, 0], [1, 0], [1, 1], [0, 1]],
            [[0, 2], [1, 2], [1, 3], [0, 3]],
            [[0, 4], [1, 4], [1, 5], [0, 5]],
        ],
    }
    lines = extract_lines_from_result(raw)

    assert [line["text"] for line in lines] == ["Ala", "ma", "kota"]
    assert lines[2]["confidence"] == 0.93


def test_build_paddle_init_kwargs_legacy_api() -> None:
    supported = {"lang", "use_angle_cls", "use_gpu"}
    kwargs = build_paddle_init_kwargs(
        supported_params=supported,
        lang="en",
        use_angle_cls=True,
        use_gpu=True,
    )

    assert kwargs == {"lang": "en", "use_angle_cls": True, "use_gpu": True}


def test_build_paddle_init_kwargs_modern_api_without_use_gpu() -> None:
    supported = {"lang", "use_textline_orientation", "device"}
    kwargs = build_paddle_init_kwargs(
        supported_params=supported,
        lang="en",
        use_angle_cls=True,
        use_gpu=True,
    )

    assert kwargs == {
        "lang": "en",
        "use_textline_orientation": True,
        "device": "gpu",
    }
