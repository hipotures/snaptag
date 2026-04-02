from __future__ import annotations

import argparse
import base64
import collections
import csv
import glob
import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from snapgit.ocr_benchmark.ollama_ocr_benchmark import (
    build_ocr_prompt,
    compute_error_rates,
    extract_json_object_from_text,
    parse_ocr_response,
)


def normalize_openai_api_base_url(api_base_url: str) -> str:
    base = api_base_url.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def _image_path_to_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    elif suffix == ".webp":
        mime = "image/webp"
    else:
        mime = "application/octet-stream"
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def extract_message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
        return "\n".join(chunks).strip()
    return ""


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


def _load_per_image_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {row["image_name"]: row for row in csv.DictReader(handle)}


_TOKEN_PATTERN = re.compile(r"[0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]+", re.UNICODE)
_STOPWORDS = {
    "oraz",
    "który",
    "która",
    "które",
    "jest",
    "się",
    "żeby",
    "that",
    "this",
    "with",
    "from",
    "have",
    "your",
    "jego",
    "jej",
    "dla",
    "the",
    "and",
    "czy",
    "or",
    "ale",
    "nie",
    "tak",
    "to",
    "na",
    "w",
    "z",
    "do",
    "po",
    "za",
    "od",
    "we",
    "o",
    "u",
    "a",
    "i",
}


def _normalize_for_tokens(text: str) -> str:
    return " ".join(text.lower().split())


def _tokens(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(_normalize_for_tokens(text))


def _keywords(tokens: list[str]) -> list[str]:
    return [token for token in tokens if len(token) >= 5 and token not in _STOPWORDS]


def compute_reference_overlap_metrics(
    reference_rows: Mapping[str, Mapping[str, str]],
    candidate_rows: Mapping[str, Mapping[str, str]],
) -> dict[str, float | int | None]:
    ref_tokens_all: list[str] = []
    cand_tokens_all: list[str] = []
    ref_keywords_all: list[str] = []
    cand_keywords_all: list[str] = []
    shared_images = 0

    for image_name in sorted(set(reference_rows) & set(candidate_rows)):
        ref_row = reference_rows[image_name]
        cand_row = candidate_rows[image_name]
        if ref_row.get("status") != "ok" or cand_row.get("status") != "ok":
            continue
        shared_images += 1
        ref_tokens = _tokens(str(ref_row.get("predicted_text", "")))
        cand_tokens = _tokens(str(cand_row.get("predicted_text", "")))
        ref_tokens_all.extend(ref_tokens)
        cand_tokens_all.extend(cand_tokens)
        ref_keywords_all.extend(_keywords(ref_tokens))
        cand_keywords_all.extend(_keywords(cand_tokens))

    if not ref_tokens_all or not cand_tokens_all:
        return {
            "reference_shared_images": shared_images,
            "reference_token_recall": None,
            "reference_token_precision": None,
            "reference_keyword_recall": None,
            "reference_tokens": len(ref_tokens_all),
            "candidate_tokens": len(cand_tokens_all),
        }

    ref_counter = collections.Counter(ref_tokens_all)
    cand_counter = collections.Counter(cand_tokens_all)
    overlap = sum(min(ref_counter[token], cand_counter[token]) for token in ref_counter)
    token_recall = overlap / sum(ref_counter.values())
    token_precision = overlap / sum(cand_counter.values())

    ref_kw_counter = collections.Counter(ref_keywords_all)
    cand_kw_counter = collections.Counter(cand_keywords_all)
    kw_overlap = sum(min(ref_kw_counter[token], cand_kw_counter[token]) for token in ref_kw_counter)
    keyword_recall = (
        kw_overlap / sum(ref_kw_counter.values()) if ref_kw_counter else None
    )

    return {
        "reference_shared_images": shared_images,
        "reference_token_recall": token_recall,
        "reference_token_precision": token_precision,
        "reference_keyword_recall": keyword_recall,
        "reference_tokens": sum(ref_counter.values()),
        "candidate_tokens": sum(cand_counter.values()),
    }


def _ensure_output_dir(path: Path | None, model: str) -> Path:
    if path is not None:
        output_dir = path
    else:
        now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        safe_model = re.sub(r"[^a-zA-Z0-9_.-]+", "_", model)
        output_dir = Path("data/ocr_bench/vllm") / safe_model / now
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


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _run_vllm_ocr(
    *,
    api_base_url: str,
    model: str,
    image_path: Path,
    language_hint: str,
    temperature: float,
    timeout_seconds: float,
    max_output_tokens: int | None,
) -> tuple[str, dict[str, Any]]:
    url = normalize_openai_api_base_url(api_base_url) + "/v1/chat/completions"
    prompt = build_ocr_prompt(language_hint=language_hint)
    data_url = _image_path_to_data_url(image_path)

    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ocr_response",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "full_text": {"type": "string"},
                        "content_tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["full_text", "content_tags"],
                    "additionalProperties": False,
                },
            },
        },
    }
    if max_output_tokens is not None:
        payload["max_tokens"] = max_output_tokens

    try:
        response_payload = _post_json(url, payload, timeout_seconds)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        error_text = error_body.lower()
        # Some vLLM builds do not support response_format=json_schema.
        if exc.code == 400 and (
            "response_format" in error_text or "json_schema" in error_text
        ):
            payload_no_schema = dict(payload)
            payload_no_schema.pop("response_format", None)
            response_payload = _post_json(url, payload_no_schema, timeout_seconds)
        else:
            raise RuntimeError(f"vLLM HTTP {exc.code}: {error_body}") from exc
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing 'choices' in response")
    message = choices[0].get("message", {})
    if not isinstance(message, Mapping):
        raise ValueError("missing 'message' in first choice")
    content = extract_message_content_text(message.get("content", ""))
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
    ground_truth_path: Path | None,
    reference_per_image_csv: Path | None,
    limit: int | None,
) -> dict[str, Any]:
    image_paths = sorted(Path(path) for path in glob.glob(input_glob))
    if limit is not None:
        image_paths = image_paths[:limit]
    if not image_paths:
        raise RuntimeError(f"No input images found for pattern: {input_glob}")

    gt_map = _load_ground_truth(ground_truth_path)
    destination = _ensure_output_dir(output_dir, model)

    per_image_rows: list[dict[str, Any]] = []
    per_line_rows: list[dict[str, Any]] = []
    cer_values: list[float] = []
    wer_values: list[float] = []
    failures: list[dict[str, str]] = []
    elapsed_values: list[float] = []

    for image_path in image_paths:
        image_name = image_path.name
        start_time = perf_counter()
        try:
            raw_text, backend_payload = _run_vllm_ocr(
                api_base_url=api_base_url,
                model=model,
                image_path=image_path,
                language_hint=language_hint,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
            parsed_payload = extract_json_object_from_text(raw_text)
            parsed = parse_ocr_response(parsed_payload)
            lines = parsed["lines"]
            predicted_text = parsed["text"]
            content_tags = parsed["content_tags"]
            elapsed = perf_counter() - start_time
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

    processed_ok = [row for row in per_image_rows if row["status"] == "ok"]
    summary = {
        "engine": "vllm_ocr",
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
        "reference_per_image_csv": str(reference_per_image_csv.resolve())
        if reference_per_image_csv
        else "",
        "reference_shared_images": None,
        "reference_token_recall": None,
        "reference_token_precision": None,
        "reference_keyword_recall": None,
        "reference_tokens": None,
        "candidate_tokens": None,
    }

    if reference_per_image_csv is not None:
        if not reference_per_image_csv.exists():
            raise FileNotFoundError(
                f"Reference per-image CSV does not exist: {reference_per_image_csv}"
            )
        reference_rows = _load_per_image_csv(reference_per_image_csv)
        candidate_rows = {
            row["image_name"]: {key: str(value) for key, value in row.items()}
            for row in per_image_rows
        }
        reference_metrics = compute_reference_overlap_metrics(reference_rows, candidate_rows)
        summary.update(reference_metrics)

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
        "# vLLM OCR Benchmark Summary",
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
        description="Run OCR benchmark for vLLM OpenAI-compatible server and save artifacts."
    )
    parser.add_argument(
        "--input-glob",
        default="/tmp/scr/*.png",
        help="Glob pattern for input images.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Default: data/ocr_bench/vllm/<model>/<timestamp>.",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://127.0.0.1:8001",
        help="vLLM server base URL, with or without /v1 suffix.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model name/id sent in OpenAI request.",
    )
    parser.add_argument(
        "--language-hint",
        default="pl,en",
        help="Language hint embedded in OCR prompt.",
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
        default=360.0,
        help="Per-request timeout.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="OpenAI max_tokens value.",
    )
    parser.add_argument(
        "--ground-truth",
        default="",
        help="Optional path to ground-truth file (.json or .csv).",
    )
    parser.add_argument(
        "--reference-per-image-csv",
        default="",
        help=(
            "Optional per_image.csv from another run (e.g. Paddle) for layout-agnostic "
            "token/keyword overlap metrics."
        ),
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
    reference_per_image_csv = (
        Path(args.reference_per_image_csv) if args.reference_per_image_csv else None
    )
    summary = run_benchmark(
        input_glob=args.input_glob,
        output_dir=output_dir,
        api_base_url=args.api_base_url,
        model=args.model,
        language_hint=args.language_hint,
        temperature=args.temperature,
        timeout_seconds=args.timeout_seconds,
        max_output_tokens=args.max_output_tokens,
        ground_truth_path=ground_truth_path,
        reference_per_image_csv=reference_per_image_csv,
        limit=args.limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
