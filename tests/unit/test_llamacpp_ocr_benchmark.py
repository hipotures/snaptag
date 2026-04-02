from __future__ import annotations

from snapgit.ocr_benchmark.llamacpp_ocr_benchmark import (
    compute_reference_overlap_metrics,
    extract_message_content_text,
)


def test_extract_message_content_text_from_string() -> None:
    text = extract_message_content_text("{\"full_text\":\"Ala\",\"content_tags\":[]}")
    assert text == "{\"full_text\":\"Ala\",\"content_tags\":[]}"


def test_extract_message_content_text_from_openai_parts() -> None:
    content = [
        {"type": "output_text", "text": "{\"full_text\":\"Ala\""},
        {"type": "output_text", "text": ",\"content_tags\":[\"ui\"]}"},
    ]
    text = extract_message_content_text(content)
    assert text == "{\"full_text\":\"Ala\"\n,\"content_tags\":[\"ui\"]}"


def test_compute_reference_overlap_metrics_basic() -> None:
    reference = {
        "a.png": {"status": "ok", "predicted_text": "Ala ma kota"},
        "b.png": {"status": "ok", "predicted_text": "Numer 221 DNS Secondary"},
    }
    candidate = {
        "a.png": {"status": "ok", "predicted_text": "Ala ma kota"},
        "b.png": {"status": "ok", "predicted_text": "Numer 221 DNS"},
    }

    metrics = compute_reference_overlap_metrics(reference, candidate)
    assert metrics["reference_shared_images"] == 2
    assert metrics["reference_token_recall"] is not None
    assert metrics["reference_token_precision"] is not None
    assert metrics["reference_keyword_recall"] is not None
    assert float(metrics["reference_token_recall"]) < 1.0
