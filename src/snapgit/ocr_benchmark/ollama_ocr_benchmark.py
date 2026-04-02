from __future__ import annotations

import argparse
import base64
import csv
import glob
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


def normalize_for_scoring(text: str) -> str:
    return " ".join(text.lower().split())


def _levenshtein_distance(left: Sequence[Any], right: Sequence[Any]) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            cost = 0 if left_value == right_value else 1
            current.append(
                min(
                    current[right_index - 1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def compute_error_rates(reference: str, prediction: str) -> dict[str, float]:
    ref_norm = normalize_for_scoring(reference)
    pred_norm = normalize_for_scoring(prediction)

    char_ref = list(ref_norm)
    char_pred = list(pred_norm)
    word_ref = ref_norm.split() if ref_norm else []
    word_pred = pred_norm.split() if pred_norm else []

    char_distance = _levenshtein_distance(char_ref, char_pred)
    word_distance = _levenshtein_distance(word_ref, word_pred)

    cer = 0.0 if not char_ref and not char_pred else char_distance / max(len(char_ref), 1)
    wer = 0.0 if not word_ref and not word_pred else word_distance / max(len(word_ref), 1)

    return {
        "char_distance": float(char_distance),
        "word_distance": float(word_distance),
        "cer": cer,
        "wer": wer,
    }


def strip_v1_suffix(api_base_url: str) -> str:
    text = api_base_url.rstrip("/")
    if text.endswith("/v1"):
        return text[:-3]
    return text


def is_ollama_api_base_url(api_base_url: str) -> bool:
    parsed = urllib.parse.urlparse(api_base_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return False
    if parsed.port != 11434:
        return False
    return True


def ollama_post_json(base_url: str, path: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        strip_v1_suffix(base_url) + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_get_json(base_url: str, path: str, timeout_seconds: float) -> dict[str, Any]:
    with urllib.request.urlopen(strip_v1_suffix(base_url) + path, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_stop_model(model_name: str) -> None:
    subprocess.run(["ollama", "stop", model_name], capture_output=True, text=True, check=False)


def ollama_ps_names(base_url: str, timeout_seconds: float) -> list[str]:
    payload = ollama_get_json(base_url, "/api/ps", timeout_seconds)
    return [str(item.get("name", "")) for item in payload.get("models", []) if item.get("name")]


def prepare_ollama_model(
    api_base_url: str,
    model_name: str,
    benchmark_models: Sequence[str],
    keep_alive: str,
    poll_seconds: float,
    poll_attempts: int,
    timeout_seconds: float,
) -> None:
    for candidate in benchmark_models:
        ollama_stop_model(candidate)
    ollama_post_json(
        api_base_url,
        "/api/generate",
        {
            "model": model_name,
            "prompt": "ok",
            "stream": False,
            "keep_alive": keep_alive,
        },
        timeout_seconds,
    )
    for _attempt in range(poll_attempts):
        time.sleep(poll_seconds)
        loaded_models = ollama_ps_names(api_base_url, timeout_seconds)
        if model_name in loaded_models:
            return
    raise RuntimeError(f'failed to confirm Ollama model load for "{model_name}"')


def unload_ollama_model(
    api_base_url: str,
    model_name: str,
    poll_seconds: float,
    poll_attempts: int,
    timeout_seconds: float,
) -> None:
    ollama_stop_model(model_name)
    for _attempt in range(poll_attempts):
        time.sleep(poll_seconds)
        loaded_models = ollama_ps_names(api_base_url, timeout_seconds)
        if model_name not in loaded_models:
            return
    raise RuntimeError(f'failed to confirm Ollama model unload for "{model_name}"')


def build_ocr_prompt(language_hint: str) -> str:
    return (
        "You are an OCR transcription engine.\n"
        "Extract all visible text from the image exactly as displayed.\n"
        "Return exactly one JSON object and nothing else.\n"
        "Schema:\n"
        "{\n"
        '  "full_text": string,\n'
        '  "content_tags": [string]\n'
        "}\n"
        "Rules:\n"
        "- Keep original language, casing, punctuation, and numbers.\n"
        "- Keep reading order: top-to-bottom, left-to-right.\n"
        "- full_text must contain all detected text joined with newline separators.\n"
        "- content_tags must contain short topical tags for text content, e.g. ui, settings, article, invoice, listing.\n"
        "- Preserve technical tokens exactly: hostnames, domains, URLs, IPv4/IPv6, model names, dates, and numbers.\n"
        "- Do not normalize or rewrite UI labels such as DNS Secondary, IPv4, IPv6, Gateway, Status, Model.\n"
        "- Do not translate.\n"
        "- Do not summarize.\n"
        "- Do not correct spelling.\n"
        "- Do not invent missing text.\n"
        "- If a fragment is unreadable, skip only that fragment.\n"
        f"- Language hint: {language_hint}.\n"
    )


def build_ollama_reasoning_payload(ollama_think: str) -> dict[str, Any]:
    if ollama_think == "inherit":
        return {}
    if ollama_think == "false":
        return {
            "think": False,
            "reasoning_effort": "none",
            "reasoning": {"effort": "none"},
        }
    if ollama_think == "true":
        return {"think": True}
    if ollama_think in {"low", "medium", "high"}:
        return {
            "think": True,
            "reasoning_effort": ollama_think,
            "reasoning": {"effort": ollama_think},
        }
    raise ValueError(f"unsupported ollama_think value: {ollama_think}")


def build_ollama_options_payload(
    *,
    temperature: float,
    max_output_tokens: int | None,
    ollama_num_predict: int | None,
    ollama_num_ctx: int | None,
) -> dict[str, Any]:
    options: dict[str, Any] = {"temperature": temperature}
    if ollama_num_predict is not None:
        options["num_predict"] = ollama_num_predict
    elif max_output_tokens is not None:
        options["num_predict"] = max_output_tokens
    if ollama_num_ctx is not None:
        options["num_ctx"] = ollama_num_ctx
    return options


def extract_json_object_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty response body")

    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fence_match:
        payload = json.loads(fence_match.group(1))
        if isinstance(payload, dict):
            return payload

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        raise ValueError("no JSON object found in response")
    payload = json.loads(stripped[first_brace : last_brace + 1])
    if not isinstance(payload, dict):
        raise ValueError("parsed payload is not an object")
    return payload


def parse_ocr_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    full_text_value = payload.get("full_text", "")
    if full_text_value is None:
        full_text_value = ""
    full_text = str(full_text_value).strip()

    if not full_text:
        lines_raw = payload.get("lines", [])
        if not isinstance(lines_raw, list):
            raise ValueError("response field 'full_text' is empty and 'lines' is not a list")
        lines = [str(item).strip() for item in lines_raw if str(item).strip()]
        full_text = "\n".join(lines)
    else:
        lines = [line.strip() for line in full_text.splitlines() if line.strip()]

    tags_raw = payload.get("content_tags", [])
    if tags_raw is None:
        tags_raw = []
    if not isinstance(tags_raw, list):
        raise ValueError("response field 'content_tags' is not a list")
    content_tags: list[str] = []
    for tag in tags_raw:
        cleaned = str(tag).strip().lower()
        if cleaned and cleaned not in content_tags:
            content_tags.append(cleaned)

    return {"lines": lines, "text": full_text, "content_tags": content_tags}


def _image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _load_ground_truth(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"Ground-truth file does not exist: {path}")

    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Ground-truth JSON must be an object: {\"filename.png\": \"text\"}")
        return {str(key): str(value) for key, value in payload.items()}

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("Ground-truth CSV must include headers")
            filename_key = "filename" if "filename" in reader.fieldnames else reader.fieldnames[0]
            text_key = "text" if "text" in reader.fieldnames else reader.fieldnames[-1]
            return {
                str(row[filename_key]): str(row[text_key])
                for row in reader
                if row.get(filename_key) is not None and row.get(text_key) is not None
            }

    raise ValueError("Supported ground-truth formats: .json, .csv")


def _lookup_ground_truth(mapping: Mapping[str, str], image_path: Path) -> str | None:
    if image_path.name in mapping:
        return mapping[image_path.name]
    if image_path.stem in mapping:
        return mapping[image_path.stem]
    return None


def _ensure_output_dir(path: Path | None, model: str) -> Path:
    if path is not None:
        output_dir = path
    else:
        now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        safe_model = re.sub(r"[^a-zA-Z0-9_.-]+", "_", model)
        output_dir = Path("data/ocr_bench/ollama") / safe_model / now
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw").mkdir(parents=True, exist_ok=True)
    return output_dir


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _mean_or_none(values: Iterable[float]) -> float | None:
    values_list = list(values)
    if not values_list:
        return None
    return mean(values_list)


def _run_ollama_ocr(
    *,
    api_base_url: str,
    model: str,
    image_path: Path,
    language_hint: str,
    temperature: float,
    timeout_seconds: float,
    max_output_tokens: int | None,
    ollama_num_predict: int | None,
    ollama_num_ctx: int | None,
    ollama_keep_alive: str,
    ollama_think: str,
) -> tuple[str, dict[str, Any]]:
    prompt = build_ocr_prompt(language_hint=language_hint)
    image_b64 = _image_to_base64(image_path)
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "keep_alive": ollama_keep_alive,
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            },
        ],
        "format": {
            "type": "object",
            "properties": {
                "full_text": {"type": "string"},
                "content_tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["full_text", "content_tags"],
            "additionalProperties": False,
        },
        "options": build_ollama_options_payload(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            ollama_num_predict=ollama_num_predict,
            ollama_num_ctx=ollama_num_ctx,
        ),
    }
    payload.update(build_ollama_reasoning_payload(ollama_think))

    try:
        response_payload = ollama_post_json(api_base_url, "/api/chat", payload, timeout_seconds)
    except Exception:
        payload_no_schema = dict(payload)
        payload_no_schema.pop("format", None)
        response_payload = ollama_post_json(api_base_url, "/api/chat", payload_no_schema, timeout_seconds)

    message = response_payload.get("message", {})
    if not isinstance(message, dict):
        raise ValueError("missing response message")
    content = str(message.get("content", "")).strip()
    return content, response_payload


def run_benchmark(
    *,
    input_glob: str,
    output_dir: Path | None,
    api_base_url: str,
    model: str,
    language_hint: str,
    temperature: float,
    timeout_seconds: float,
    max_output_tokens: int | None,
    ollama_num_predict: int | None,
    ollama_num_ctx: int | None,
    ollama_keep_alive: str,
    ollama_think: str,
    ollama_load_poll_seconds: float,
    ollama_load_poll_attempts: int,
    manage_model_loading: bool,
    ground_truth_path: Path | None,
    limit: int | None,
) -> dict[str, Any]:
    image_paths = sorted(Path(path) for path in glob.glob(input_glob))
    if limit is not None:
        image_paths = image_paths[:limit]
    if not image_paths:
        raise RuntimeError(f"No input images found for pattern: {input_glob}")

    gt_map = _load_ground_truth(ground_truth_path)
    destination = _ensure_output_dir(output_dir, model)

    if manage_model_loading and is_ollama_api_base_url(api_base_url):
        prepare_ollama_model(
            api_base_url=api_base_url,
            model_name=model,
            benchmark_models=[model],
            keep_alive=ollama_keep_alive,
            poll_seconds=ollama_load_poll_seconds,
            poll_attempts=ollama_load_poll_attempts,
            timeout_seconds=timeout_seconds,
        )

    per_image_rows: list[dict[str, Any]] = []
    per_line_rows: list[dict[str, Any]] = []
    cer_values: list[float] = []
    wer_values: list[float] = []
    failures: list[dict[str, str]] = []
    elapsed_values: list[float] = []

    try:
        for image_path in image_paths:
            image_name = image_path.name
            start_time = time.perf_counter()
            try:
                raw_text, backend_payload = _run_ollama_ocr(
                    api_base_url=api_base_url,
                    model=model,
                    image_path=image_path,
                    language_hint=language_hint,
                    temperature=temperature,
                    timeout_seconds=timeout_seconds,
                    max_output_tokens=max_output_tokens,
                    ollama_num_predict=ollama_num_predict,
                    ollama_num_ctx=ollama_num_ctx,
                    ollama_keep_alive=ollama_keep_alive,
                    ollama_think=ollama_think,
                )
                parsed_payload = extract_json_object_from_text(raw_text)
                parsed = parse_ocr_response(parsed_payload)
                lines = parsed["lines"]
                predicted_text = parsed["text"]
                content_tags = parsed["content_tags"]
                elapsed = time.perf_counter() - start_time
                elapsed_values.append(elapsed)
            except Exception as exc:
                failures.append({"image": image_name, "error": str(exc)})
                per_image_rows.append(
                    {
                        "image_name": image_name,
                        "image_path": str(image_path),
                        "status": "error",
                        "line_count": 0,
                        "char_count": 0,
                        "mean_confidence": "",
                        "min_confidence": "",
                        "max_confidence": "",
                        "predicted_text": "",
                        "ground_truth_text": "",
                        "cer": "",
                        "wer": "",
                        "content_tags_json": "",
                        "elapsed_seconds": "",
                        "error": str(exc),
                    }
                )
                continue

            _write_json(destination / "raw" / f"{image_path.stem}.response.json", backend_payload)
            (destination / "raw" / f"{image_path.stem}.raw.txt").write_text(raw_text, encoding="utf-8")
            _write_json(destination / "raw" / f"{image_path.stem}.parsed.json", parsed_payload)

            for line_index, line_text in enumerate(lines):
                per_line_rows.append(
                    {
                        "image_name": image_name,
                        "line_index": line_index,
                        "text": line_text,
                        "confidence": "",
                        "bbox_json": "",
                    }
                )

            row: dict[str, Any] = {
                "image_name": image_name,
                "image_path": str(image_path),
                "status": "ok",
                "line_count": len(lines),
                "char_count": len(predicted_text),
                "mean_confidence": "",
                "min_confidence": "",
                "max_confidence": "",
                "predicted_text": predicted_text,
                "ground_truth_text": "",
                "cer": "",
                "wer": "",
                "content_tags_json": json.dumps(content_tags, ensure_ascii=False),
                "elapsed_seconds": elapsed,
                "error": "",
            }

            ground_truth_text = _lookup_ground_truth(gt_map, image_path)
            if ground_truth_text is not None:
                metrics = compute_error_rates(ground_truth_text, predicted_text)
                row["ground_truth_text"] = ground_truth_text
                row["cer"] = metrics["cer"]
                row["wer"] = metrics["wer"]
                cer_values.append(metrics["cer"])
                wer_values.append(metrics["wer"])

            per_image_rows.append(row)
    finally:
        if manage_model_loading and is_ollama_api_base_url(api_base_url):
            unload_ollama_model(
                api_base_url=api_base_url,
                model_name=model,
                poll_seconds=ollama_load_poll_seconds,
                poll_attempts=ollama_load_poll_attempts,
                timeout_seconds=timeout_seconds,
            )

    processed_ok = [row for row in per_image_rows if row["status"] == "ok"]
    summary = {
        "engine": "ollama_ocr",
        "utc_timestamp": datetime.now(timezone.utc).isoformat(),
        "input_glob": input_glob,
        "images_total": len(image_paths),
        "images_ok": len(processed_ok),
        "images_failed": len(failures),
        "lines_total": int(sum(int(row["line_count"]) for row in processed_ok)),
        "chars_total": int(sum(int(row["char_count"]) for row in processed_ok)),
        "confidence_mean": None,
        "confidence_min": None,
        "confidence_max": None,
        "ground_truth_images": len(cer_values),
        "mean_cer": _mean_or_none(cer_values),
        "mean_wer": _mean_or_none(wer_values),
        "elapsed_seconds_mean": _mean_or_none(elapsed_values),
        "failures": failures,
        "output_dir": str(destination.resolve()),
        "model": model,
        "api_base_url": api_base_url,
        "language_hint": language_hint,
        "ollama_think": ollama_think,
    }

    _write_csv(
        destination / "per_image.csv",
        per_image_rows,
        [
            "image_name",
            "image_path",
            "status",
            "line_count",
            "char_count",
            "mean_confidence",
            "min_confidence",
            "max_confidence",
            "predicted_text",
            "ground_truth_text",
            "cer",
            "wer",
            "content_tags_json",
            "elapsed_seconds",
            "error",
        ],
    )
    _write_csv(
        destination / "per_line.csv",
        per_line_rows,
        ["image_name", "line_index", "text", "confidence", "bbox_json"],
    )
    _write_json(destination / "summary.json", summary)

    markdown_summary = [
        "# Ollama OCR Benchmark Summary",
        "",
        f"- Model: `{model}`",
        f"- Input glob: `{input_glob}`",
        f"- Images total: `{summary['images_total']}`",
        f"- Images ok: `{summary['images_ok']}`",
        f"- Images failed: `{summary['images_failed']}`",
        f"- Lines total: `{summary['lines_total']}`",
        f"- Mean elapsed (s): `{summary['elapsed_seconds_mean']}`",
        f"- Ground-truth images: `{summary['ground_truth_images']}`",
        f"- Mean CER: `{summary['mean_cer']}`",
        f"- Mean WER: `{summary['mean_wer']}`",
    ]
    (destination / "summary.md").write_text("\n".join(markdown_summary) + "\n", encoding="utf-8")

    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OCR benchmark for an Ollama vision model and save quality artifacts."
    )
    parser.add_argument(
        "--input-glob",
        default="/tmp/scr/*.png",
        help="Glob pattern for input images.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Default: data/ocr_bench/ollama/<model>/<timestamp>.",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://127.0.0.1:11434",
        help="Ollama API base URL.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Ollama model name, e.g. qwen2.5vl:7b.",
    )
    parser.add_argument(
        "--language-hint",
        default="pl,en",
        help="Language hint embedded in the OCR prompt.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Per-request timeout.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="Optional max generated tokens (Ollama num_predict).",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=None,
        help="Explicit Ollama options.num_predict. Overrides --max-output-tokens when set.",
    )
    parser.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=None,
        help="Optional Ollama num_ctx override.",
    )
    parser.add_argument(
        "--ollama-think",
        choices=("inherit", "false", "true", "low", "medium", "high"),
        default="false",
        help="Thinking mode for Ollama models. false disables thinking; low/medium/high set reasoning effort. Default: false.",
    )
    parser.add_argument(
        "--ollama-keep-alive",
        default="30m",
        help="Ollama keep_alive duration used during benchmark.",
    )
    parser.add_argument(
        "--manage-model-loading",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Load target model before run and unload after run. Default: enabled.",
    )
    parser.add_argument(
        "--ollama-load-poll-seconds",
        type=float,
        default=0.5,
        help="Polling interval used during model load/unload checks.",
    )
    parser.add_argument(
        "--ollama-load-poll-attempts",
        type=int,
        default=120,
        help="Polling attempts used during model load/unload checks.",
    )
    parser.add_argument(
        "--ground-truth",
        default="",
        help="Optional path to ground-truth file (.json or .csv).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit of processed images after sorting.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir) if args.output_dir else None
    ground_truth_path = Path(args.ground_truth) if args.ground_truth else None
    summary = run_benchmark(
        input_glob=args.input_glob,
        output_dir=output_dir,
        api_base_url=args.api_base_url,
        model=args.model,
        language_hint=args.language_hint,
        temperature=args.temperature,
        timeout_seconds=args.timeout_seconds,
        max_output_tokens=args.max_output_tokens,
        ollama_num_predict=args.ollama_num_predict,
        ollama_num_ctx=args.ollama_num_ctx,
        ollama_keep_alive=args.ollama_keep_alive,
        ollama_think=args.ollama_think,
        ollama_load_poll_seconds=args.ollama_load_poll_seconds,
        ollama_load_poll_attempts=args.ollama_load_poll_attempts,
        manage_model_loading=args.manage_model_loading,
        ground_truth_path=ground_truth_path,
        limit=args.limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
