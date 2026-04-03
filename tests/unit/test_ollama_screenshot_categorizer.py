from __future__ import annotations

import pytest

from snapgit.ocr_benchmark.ollama_screenshot_categorizer import (
    _redact_request_payload,
    build_arg_parser,
    build_category_prompt,
    extract_app_hint_from_filename,
    parse_category_response,
)


def test_extract_app_hint_from_filename_for_phone_screenshot() -> None:
    assert extract_app_hint_from_filename("Screenshot_20250822_122853_TikTok.jpg") == "TikTok"
    assert (
        extract_app_hint_from_filename(
            "Screenshot_20251125_161602_Background Video Recorder.jpg"
        )
        == "Background Video Recorder"
    )
    assert (
        extract_app_hint_from_filename("Screenshot_20251115_143557_Instagram~2.jpg")
        == "Instagram"
    )
    assert extract_app_hint_from_filename("Screenshot From 2025-01-02 15-07-41.png") == ""


def test_build_category_prompt_contains_schema_and_hint() -> None:
    prompt = build_category_prompt(language_hint="pl,en", app_hint_from_filename="TikTok")
    assert '"summary_pl"' in prompt
    assert '"summary_en"' in prompt
    assert '"ocr_excerpt"' in prompt
    assert '"categories"' in prompt
    assert "5 to 7 unique category pairs" in prompt
    assert "Filename app hint" in prompt
    assert "TikTok" in prompt


def test_parse_category_response_requires_minimum_categories() -> None:
    payload = {
        "summary_pl": "ekran z social media",
        "summary_en": "social media screen",
        "ocr_excerpt": "obserwuj",
        "categories": [
            {"pl": "media społecznościowe", "en": "social media"},
            {"pl": "wideo", "en": "video"},
            {"pl": "komentarze", "en": "comments"},
            {"pl": "profil", "en": "profile"},
        ],
    }
    with pytest.raises(ValueError, match="at least 5"):
        parse_category_response(payload)


def test_parse_category_response_normalizes_and_deduplicates() -> None:
    payload = {
        "summary_pl": "",
        "summary_en": "",
        "ocr_excerpt": "For you",
        "categories": [
            {"pl": "Media Społecznościowe", "en": "Social Media"},
            {"pl": "Wideo", "en": "Video"},
            {"pl": "Komentarze", "en": "Comments"},
            {"pl": "Profil", "en": "Profile"},
            {"pl": "powiadomienia", "en": "Notifications"},
            {"pl": "wideo", "en": "video"},
        ],
    }

    parsed = parse_category_response(payload)
    assert len(parsed["categories"]) == 5
    assert parsed["summary_pl"] == "media społecznościowe, wideo, komentarze"
    assert parsed["summary_en"] == "social media, video, comments"


def test_build_arg_parser_accepts_resume_flags() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["--model", "qwen3.5:9b", "--no-resume", "--no-progress"])
    assert args.resume is False
    assert args.progress is False


def test_redact_request_payload_replaces_images_with_metadata() -> None:
    payload = {
        "model": "qwen3.5:9b",
        "messages": [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "y", "images": ["abc123", "zzz"]},
        ],
    }
    redacted = _redact_request_payload(payload)

    assert redacted["messages"][1]["images"] == [
        {"index": 0, "redacted": True, "encoding": "base64", "char_length": 6},
        {"index": 1, "redacted": True, "encoding": "base64", "char_length": 3},
    ]
