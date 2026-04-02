from __future__ import annotations

from snapgit.ocr_benchmark.ollama_ocr_benchmark import (
    build_ollama_options_payload,
    build_ollama_reasoning_payload,
    build_ocr_prompt,
    extract_json_object_from_text,
    is_ollama_api_base_url,
    parse_ocr_response,
    strip_v1_suffix,
)


def test_strip_v1_suffix_handles_openai_compat_url() -> None:
    assert strip_v1_suffix("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert strip_v1_suffix("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"


def test_is_ollama_api_base_url_accepts_localhost_11434() -> None:
    assert is_ollama_api_base_url("http://127.0.0.1:11434/v1")
    assert is_ollama_api_base_url("http://localhost:11434")
    assert not is_ollama_api_base_url("http://127.0.0.1:8000")


def test_build_ocr_prompt_contains_anti_hallucination_rules() -> None:
    prompt = build_ocr_prompt(language_hint="pl,en")
    assert "Do not translate" in prompt
    assert "Do not correct spelling" in prompt
    assert "JSON object and nothing else" in prompt
    assert '"full_text"' in prompt
    assert '"content_tags"' in prompt
    assert "Preserve technical tokens exactly" in prompt
    assert "DNS Secondary" in prompt


def test_extract_json_object_from_text_supports_markdown_fence() -> None:
    text = """Sure.\n```json\n{\"lines\":[\"Ala ma kota\"]}\n```\n"""
    payload = extract_json_object_from_text(text)
    assert payload == {"lines": ["Ala ma kota"]}


def test_parse_ocr_response_joins_lines() -> None:
    payload = {"full_text": "Ala ma kota\nNumer 221", "content_tags": ["ui", "panel"]}
    parsed = parse_ocr_response(payload)
    assert parsed["lines"] == ["Ala ma kota", "Numer 221"]
    assert parsed["text"] == "Ala ma kota\nNumer 221"
    assert parsed["content_tags"] == ["ui", "panel"]


def test_build_ollama_reasoning_payload_for_levels() -> None:
    assert build_ollama_reasoning_payload("inherit") == {}
    assert build_ollama_reasoning_payload("false") == {
        "think": False,
        "reasoning_effort": "none",
        "reasoning": {"effort": "none"},
    }
    assert build_ollama_reasoning_payload("true") == {"think": True}
    assert build_ollama_reasoning_payload("low") == {
        "think": True,
        "reasoning_effort": "low",
        "reasoning": {"effort": "low"},
    }


def test_build_ollama_options_payload_prefers_num_predict_override() -> None:
    options = build_ollama_options_payload(
        temperature=0.0,
        max_output_tokens=4096,
        ollama_num_predict=2048,
        ollama_num_ctx=8192,
    )
    assert options == {"temperature": 0.0, "num_predict": 2048, "num_ctx": 8192}
