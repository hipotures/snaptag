from __future__ import annotations

import argparse
import base64
import csv
import glob
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from snapgit.ocr_benchmark.ollama_ocr_benchmark import (
    build_ollama_options_payload,
    build_ollama_reasoning_payload,
    extract_json_object_from_text,
    is_ollama_api_base_url,
    ollama_post_json,
    prepare_ollama_model,
    unload_ollama_model,
)

_APP_HINT_PATTERN = re.compile(r"^Screenshot_(\d{8})_(\d{6})_(.+)$")
_DUPLICATE_SUFFIX_PATTERN = re.compile(r"~\d+$")
_CSV_FIELDS = [
    "image_name",
    "image_path",
    "status",
    "app_hint_from_filename",
    "summary_pl",
    "summary_en",
    "ocr_excerpt",
    "categories_json",
    "category_words_pl",
    "category_words_en",
    "elapsed_seconds",
    "error",
]


def extract_app_hint_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    match = _APP_HINT_PATTERN.match(stem)
    if not match:
        return ""
    hint = _DUPLICATE_SUFFIX_PATTERN.sub("", match.group(3)).strip()
    return hint.strip(" _-")


def build_category_prompt(language_hint: str, app_hint_from_filename: str) -> str:
    app_hint_line = (
        f'- Filename app hint: "{app_hint_from_filename}". Treat this as a weak hint, not a fact.\n'
        if app_hint_from_filename
        else "- Filename app hint: unavailable.\n"
    )
    return (
        "You are a screenshot categorization engine.\n"
        "Analyze the image and return exactly one JSON object with no extra text.\n"
        "Schema:\n"
        "{\n"
        '  "summary_pl": string,\n'
        '  "summary_en": string,\n'
        '  "categories": [\n'
        "    {\n"
        '      "pl": string,\n'
        '      "en": string\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- categories must contain 5 to 7 unique category pairs.\n"
        "- Each category must be short and reusable (usually 1-3 words).\n"
        "- Use lowercase for category labels.\n"
        "- Provide Polish and English equivalents for each category.\n"
        "- Prefer generic intent-level labels (e.g. social media, chat, shopping, banking, maps, game).\n"
        "- Do not invent categories unrelated to visible content.\n"
        "- summary_pl and summary_en should be one short sentence each.\n"
        "- Do not transcribe text from the image.\n"
        "- Do not copy exact long strings, code blocks, or IDs from the image.\n"
        "- Use abstract description only, e.g. 'bardzo długi ciąg znaków alfanumerycznych'.\n"
        "- Do not output markdown.\n"
        f"- Language hint for visible text: {language_hint}.\n"
        f"{app_hint_line}"
    )


def parse_category_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary_pl = str(payload.get("summary_pl", "") or "").strip()
    summary_en = str(payload.get("summary_en", "") or "").strip()

    categories_raw = payload.get("categories", [])
    if not isinstance(categories_raw, list):
        raise ValueError("response field 'categories' is not a list")

    categories: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for item in categories_raw:
        if not isinstance(item, Mapping):
            raise ValueError("category item is not an object")
        pl_value = str(item.get("pl", "") or "").strip().lower()
        en_value = str(item.get("en", "") or "").strip().lower()
        if not pl_value or not en_value:
            continue
        pair = (pl_value, en_value)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        categories.append({"pl": pl_value, "en": en_value})

    if len(categories) > 7:
        categories = categories[:7]
    if len(categories) < 5:
        raise ValueError("response must contain at least 5 unique category pairs")

    if not summary_pl:
        summary_pl = ", ".join(item["pl"] for item in categories[:3])
    if not summary_en:
        summary_en = ", ".join(item["en"] for item in categories[:3])

    return {
        "summary_pl": summary_pl,
        "summary_en": summary_en,
        "categories": categories,
    }


def _image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _safe_model_name(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", model)


def _ensure_output_dir(path: Path | None, model: str) -> Path:
    if path is not None:
        output_dir = path
    else:
        now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        output_dir = Path("data/ocr_categories/ollama") / _safe_model_name(model) / now
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw").mkdir(parents=True, exist_ok=True)
    return output_dir


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)


def _append_csv_row(path: Path, row: Mapping[str, Any], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fieldnames})
        handle.flush()
        os.fsync(handle.fileno())


def _jsonl_row_to_csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
    categories: list[Mapping[str, Any]] = []
    categories_raw = row.get("categories")
    if isinstance(categories_raw, list):
        categories = [item for item in categories_raw if isinstance(item, Mapping)]

    if categories:
        categories_json = json.dumps(categories, ensure_ascii=False)
        category_words_pl = "|".join(
            str(item.get("pl", "")).strip().lower() for item in categories if item.get("pl")
        )
        category_words_en = "|".join(
            str(item.get("en", "")).strip().lower() for item in categories if item.get("en")
        )
    else:
        categories_json = str(row.get("categories_json", "") or "")
        category_words_pl = str(row.get("category_words_pl", "") or "")
        category_words_en = str(row.get("category_words_en", "") or "")

    return {
        "image_name": str(row.get("image_name", "") or ""),
        "image_path": str(row.get("image_path", "") or ""),
        "status": str(row.get("status", "") or ""),
        "app_hint_from_filename": str(row.get("app_hint_from_filename", "") or ""),
        "summary_pl": str(row.get("summary_pl", "") or ""),
        "summary_en": str(row.get("summary_en", "") or ""),
        "ocr_excerpt": str(row.get("ocr_excerpt", "") or ""),
        "categories_json": categories_json,
        "category_words_pl": category_words_pl,
        "category_words_en": category_words_en,
        "elapsed_seconds": row.get("elapsed_seconds", ""),
        "error": str(row.get("error", "") or ""),
    }


def _atomic_write_per_image_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_jsonl_row_to_csv_row(row))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _redact_request_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(payload, ensure_ascii=False))
    messages = sanitized.get("messages", [])
    if not isinstance(messages, list):
        return sanitized
    for message in messages:
        if not isinstance(message, dict):
            continue
        images = message.get("images")
        if not isinstance(images, list):
            continue
        redacted_images: list[dict[str, Any]] = []
        for index, image_value in enumerate(images):
            redacted_images.append(
                {
                    "index": index,
                    "redacted": True,
                    "encoding": "base64",
                    "char_length": len(str(image_value)),
                }
            )
        message["images"] = redacted_images
    return sanitized


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError(f"invalid JSONL row at line {index}: expected object")
            rows.append(payload)
    return rows


def _load_input_paths(
    *,
    input_glob: str,
    input_list_file: Path | None,
    limit: int | None,
) -> list[Path]:
    if input_list_file is not None:
        if not input_list_file.exists():
            raise FileNotFoundError(f"Input list file does not exist: {input_list_file}")
        paths: list[Path] = []
        with input_list_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    paths.append(Path(stripped))
        image_paths = sorted(paths)
    else:
        image_paths = sorted(Path(path) for path in glob.glob(input_glob))

    if limit is not None:
        image_paths = image_paths[:limit]
    return image_paths


def _dedupe_rows_by_image_path(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    deduped: list[dict[str, Any]] = []
    index_by_path: dict[str, int] = {}
    for row in rows:
        image_path = str(row.get("image_path", "") or "")
        if not image_path:
            continue
        if image_path in index_by_path:
            deduped[index_by_path[image_path]] = row
        else:
            index_by_path[image_path] = len(deduped)
            deduped.append(row)
    return deduped, index_by_path


def _select_image_queue(
    *,
    image_paths: list[Path],
    rows: list[Mapping[str, Any]],
    row_index_by_path: Mapping[str, int],
    retry_failed_only: bool,
) -> list[Path]:
    if retry_failed_only:
        selected: list[Path] = []
        for image_path in image_paths:
            image_key = str(image_path)
            row_index = row_index_by_path.get(image_key)
            if row_index is None:
                continue
            if str(rows[row_index].get("status", "")).lower() == "error":
                selected.append(image_path)
        return selected
    return [path for path in image_paths if str(path) not in row_index_by_path]


def _count_by_status(rows: list[Mapping[str, Any]]) -> tuple[int, int]:
    ok_count = 0
    failed_count = 0
    for row in rows:
        if row.get("status") == "ok":
            ok_count += 1
        else:
            failed_count += 1
    return ok_count, failed_count


def _build_category_totals(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if row.get("status") != "ok":
            continue
        categories = row.get("categories", [])
        if not isinstance(categories, list):
            continue
        for item in categories:
            if not isinstance(item, Mapping):
                continue
            pl_value = str(item.get("pl", "")).strip().lower()
            en_value = str(item.get("en", "")).strip().lower()
            if pl_value and en_value:
                counts[(pl_value, en_value)] += 1
    ranking = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0][1]))
    return [{"pl": key[0], "en": key[1], "count": count} for key, count in ranking]


def _build_state(
    *,
    destination: Path,
    input_glob: str,
    model: str,
    api_base_url: str,
    language_hint: str,
    total_images: int,
    rows: list[Mapping[str, Any]],
    resumed: bool,
    last_image_name: str,
) -> dict[str, Any]:
    ok_count, failed_count = _count_by_status(rows)
    return {
        "engine": "ollama_screenshot_categorizer",
        "utc_updated_at": datetime.now(timezone.utc).isoformat(),
        "input_glob": input_glob,
        "model": model,
        "api_base_url": api_base_url,
        "language_hint": language_hint,
        "images_total": total_images,
        "images_processed": len(rows),
        "images_ok": ok_count,
        "images_failed": failed_count,
        "resumed": resumed,
        "last_image_name": last_image_name,
        "output_dir": str(destination.resolve()),
    }


def _build_summary(
    *,
    destination: Path,
    input_glob: str,
    rows: list[Mapping[str, Any]],
    model: str,
    api_base_url: str,
    language_hint: str,
    total_images: int,
    elapsed_values: list[float],
) -> dict[str, Any]:
    ok_count, failed_count = _count_by_status(rows)
    mean_elapsed = sum(elapsed_values) / len(elapsed_values) if elapsed_values else None
    category_totals = _build_category_totals(rows)
    return {
        "engine": "ollama_screenshot_categorizer",
        "utc_timestamp": datetime.now(timezone.utc).isoformat(),
        "input_glob": input_glob,
        "images_total": total_images,
        "images_processed": len(rows),
        "images_ok": ok_count,
        "images_failed": failed_count,
        "elapsed_seconds_mean": mean_elapsed,
        "output_dir": str(destination.resolve()),
        "model": model,
        "api_base_url": api_base_url,
        "language_hint": language_hint,
        "category_totals_top20": category_totals[:20],
    }


def _format_seconds(value: float | None) -> str:
    if value is None:
        return "?"
    if value < 60:
        return f"{value:.1f}s"
    minutes = int(value // 60)
    seconds = int(value % 60)
    return f"{minutes}m{seconds:02d}s"


def _render_progress_line(
    *,
    processed: int,
    total: int,
    ok_count: int,
    failed_count: int,
    average_seconds: float | None,
) -> str:
    ratio = (processed / total) if total else 1.0
    percent = ratio * 100.0
    bar_width = 24
    filled = int(ratio * bar_width)
    bar = "#" * filled + "-" * (bar_width - filled)
    remaining = max(total - processed, 0)
    eta = (average_seconds * remaining) if average_seconds is not None else None
    return (
        f"[{bar}] {processed}/{total} ({percent:5.1f}%) "
        f"ok={ok_count} err={failed_count} "
        f"avg={_format_seconds(average_seconds)} eta={_format_seconds(eta)}"
    )


def _emit_progress(line: str, *, final: bool) -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r" + line)
        if final:
            sys.stderr.write("\n")
    else:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _run_ollama_categorization(
    *,
    api_base_url: str,
    model: str,
    image_path: Path,
    language_hint: str,
    app_hint_from_filename: str,
    temperature: float,
    timeout_seconds: float,
    max_output_tokens: int | None,
    ollama_num_predict: int | None,
    ollama_num_ctx: int | None,
    ollama_seed: int | None,
    ollama_keep_alive: str,
    ollama_think: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    prompt = build_category_prompt(
        language_hint=language_hint,
        app_hint_from_filename=app_hint_from_filename,
    )
    image_b64 = _image_to_base64(image_path)

    options = build_ollama_options_payload(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        ollama_num_predict=ollama_num_predict,
        ollama_num_ctx=ollama_num_ctx,
    )
    if ollama_seed is not None:
        options["seed"] = int(ollama_seed)

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
                "summary_pl": {"type": "string"},
                "summary_en": {"type": "string"},
                "categories": {
                    "type": "array",
                    "minItems": 5,
                    "maxItems": 7,
                    "items": {
                        "type": "object",
                        "properties": {
                            "pl": {"type": "string"},
                            "en": {"type": "string"},
                        },
                        "required": ["pl", "en"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["summary_pl", "summary_en", "categories"],
            "additionalProperties": False,
        },
        "options": options,
    }
    payload.update(build_ollama_reasoning_payload(ollama_think))

    used_payload = payload
    try:
        response_payload = ollama_post_json(api_base_url, "/api/chat", payload, timeout_seconds)
    except Exception:
        payload_without_format = dict(payload)
        payload_without_format.pop("format", None)
        used_payload = payload_without_format
        response_payload = ollama_post_json(
            api_base_url, "/api/chat", payload_without_format, timeout_seconds
        )

    message = response_payload.get("message", {})
    if not isinstance(message, dict):
        raise ValueError("missing response message")
    content = str(message.get("content", "")).strip()
    if not content:
        raise ValueError("empty response content")
    return content, response_payload, used_payload


def run_categorization(
    *,
    input_glob: str,
    input_list_file: Path | None,
    output_dir: Path | None,
    api_base_url: str,
    model: str,
    language_hint: str,
    temperature: float,
    timeout_seconds: float,
    max_output_tokens: int | None,
    ollama_num_predict: int | None,
    ollama_num_ctx: int | None,
    ollama_seed: int | None,
    ollama_keep_alive: str,
    ollama_think: str,
    ollama_load_poll_seconds: float,
    ollama_load_poll_attempts: int,
    manage_model_loading: bool,
    limit: int | None,
    resume: bool,
    save_raw: bool,
    progress: bool,
    retry_failed_only: bool,
) -> dict[str, Any]:
    image_paths = _load_input_paths(
        input_glob=input_glob,
        input_list_file=input_list_file,
        limit=limit,
    )
    if not image_paths:
        if input_list_file is not None:
            raise RuntimeError(f"No input images found in list file: {input_list_file}")
        raise RuntimeError(f"No input images found for pattern: {input_glob}")

    destination = _ensure_output_dir(output_dir, model)
    results_jsonl_path = destination / "results.jsonl"
    per_image_csv_path = destination / "per_image.csv"
    state_path = destination / "state.json"
    raw_dir = destination / "raw"

    if not resume and results_jsonl_path.exists():
        raise RuntimeError(
            f"Output already contains results: {results_jsonl_path}. "
            "Use --resume or provide a different --output-dir."
        )

    existing_rows_raw = _load_jsonl_rows(results_jsonl_path) if resume else []
    existing_rows, row_index_by_path = _dedupe_rows_by_image_path(existing_rows_raw)

    if retry_failed_only and not resume:
        raise RuntimeError("--retry-failed-only requires --resume.")
    if retry_failed_only and not existing_rows:
        raise RuntimeError(
            f"No existing rows found in {results_jsonl_path}. "
            "Run a full categorization first or disable --retry-failed-only."
        )

    image_queue = _select_image_queue(
        image_paths=image_paths,
        rows=existing_rows,
        row_index_by_path=row_index_by_path,
        retry_failed_only=retry_failed_only,
    )

    if not image_queue and existing_rows:
        summary = _build_summary(
            destination=destination,
            input_glob=input_glob,
            rows=existing_rows,
            model=model,
            api_base_url=api_base_url,
            language_hint=language_hint,
            total_images=len(image_paths),
            elapsed_values=[
                float(row["elapsed_seconds"])
                for row in existing_rows
                if row.get("status") == "ok" and row.get("elapsed_seconds") not in {"", None}
            ],
        )
        _atomic_write_json(
            state_path,
            _build_state(
                destination=destination,
                input_glob=input_glob,
                model=model,
                api_base_url=api_base_url,
                language_hint=language_hint,
                total_images=len(image_paths),
                rows=existing_rows,
                resumed=True,
                last_image_name="",
            ),
        )
        _write_json(destination / "summary.json", summary)
        _write_json(destination / "category_totals.json", _build_category_totals(existing_rows))
        if progress:
            ok_count, failed_count = _count_by_status(existing_rows)
            elapsed_samples = [
                float(row["elapsed_seconds"])
                for row in existing_rows
                if row.get("elapsed_seconds") not in {"", None}
            ]
            avg = (sum(elapsed_samples) / len(elapsed_samples)) if elapsed_samples else None
            _emit_progress(
                _render_progress_line(
                    processed=len(existing_rows) if not retry_failed_only else 0,
                    total=len(image_paths) if not retry_failed_only else len(image_queue),
                    ok_count=ok_count,
                    failed_count=failed_count,
                    average_seconds=avg,
                ),
                final=True,
            )
        return summary

    runtime_samples = (
        [
            float(row["elapsed_seconds"])
            for row in existing_rows
            if row.get("elapsed_seconds") not in {"", None}
        ]
        if not retry_failed_only
        else []
    )
    rows: list[dict[str, Any]] = list(existing_rows)
    last_image_name = ""
    retry_processed = 0
    progress_total = len(image_paths) if not retry_failed_only else len(image_queue)

    _atomic_write_json(
        state_path,
        _build_state(
            destination=destination,
            input_glob=input_glob,
            model=model,
            api_base_url=api_base_url,
            language_hint=language_hint,
            total_images=len(image_paths),
            rows=rows,
            resumed=bool(existing_rows),
            last_image_name=last_image_name,
        ),
    )

    if progress:
        ok_count, failed_count = _count_by_status(rows)
        average_seconds = (sum(runtime_samples) / len(runtime_samples)) if runtime_samples else None
        _emit_progress(
            _render_progress_line(
                processed=len(rows) if not retry_failed_only else retry_processed,
                total=progress_total,
                ok_count=ok_count,
                failed_count=failed_count,
                average_seconds=average_seconds,
            ),
            final=False,
        )

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

    try:
        for image_path in image_queue:
            image_name = image_path.name
            app_hint = extract_app_hint_from_filename(image_name)
            start_time = time.perf_counter()
            row: dict[str, Any] = {
                "image_name": image_name,
                "image_path": str(image_path),
                "status": "error",
                "app_hint_from_filename": app_hint,
                "summary_pl": "",
                "summary_en": "",
                "ocr_excerpt": "",
                "categories_json": "",
                "category_words_pl": "",
                "category_words_en": "",
                "elapsed_seconds": "",
                "error": "",
            }
            row_for_jsonl: dict[str, Any] = dict(row)
            row_for_jsonl["categories"] = []

            raw_text = ""
            response_payload: dict[str, Any] | None = None
            request_payload: dict[str, Any] | None = None

            try:
                raw_text, response_payload, request_payload = _run_ollama_categorization(
                    api_base_url=api_base_url,
                    model=model,
                    image_path=image_path,
                    language_hint=language_hint,
                    app_hint_from_filename=app_hint,
                    temperature=temperature,
                    timeout_seconds=timeout_seconds,
                    max_output_tokens=max_output_tokens,
                    ollama_num_predict=ollama_num_predict,
                    ollama_num_ctx=ollama_num_ctx,
                    ollama_seed=ollama_seed,
                    ollama_keep_alive=ollama_keep_alive,
                    ollama_think=ollama_think,
                )
                parsed_payload = extract_json_object_from_text(raw_text)
                parsed = parse_category_response(parsed_payload)
                elapsed = time.perf_counter() - start_time

                categories = parsed["categories"]
                category_words_pl = [item["pl"] for item in categories]
                category_words_en = [item["en"] for item in categories]

                row.update(
                    {
                        "status": "ok",
                        "summary_pl": parsed["summary_pl"],
                        "summary_en": parsed["summary_en"],
                        "ocr_excerpt": "",
                        "categories_json": json.dumps(categories, ensure_ascii=False),
                        "category_words_pl": "|".join(category_words_pl),
                        "category_words_en": "|".join(category_words_en),
                        "elapsed_seconds": elapsed,
                    }
                )
                row_for_jsonl.update(
                    {
                        "status": "ok",
                        "summary_pl": parsed["summary_pl"],
                        "summary_en": parsed["summary_en"],
                        "ocr_excerpt": "",
                        "categories": categories,
                        "elapsed_seconds": elapsed,
                    }
                )
            except Exception as exc:
                elapsed = time.perf_counter() - start_time
                row["elapsed_seconds"] = elapsed
                row["error"] = str(exc)
                row_for_jsonl["elapsed_seconds"] = elapsed
                row_for_jsonl["error"] = str(exc)
            finally:
                runtime_samples.append(float(row["elapsed_seconds"]))
                if save_raw:
                    if request_payload is not None:
                        _write_json(
                            raw_dir / f"{image_path.stem}.request.json",
                            _redact_request_payload(request_payload),
                        )
                    if response_payload is not None:
                        _write_json(raw_dir / f"{image_path.stem}.response.json", response_payload)
                    if raw_text:
                        (raw_dir / f"{image_path.stem}.raw.txt").write_text(
                            raw_text, encoding="utf-8"
                        )

                if retry_failed_only:
                    image_key = str(image_path)
                    row_index = row_index_by_path.get(image_key)
                    if row_index is None:
                        row_index_by_path[image_key] = len(rows)
                        rows.append(row_for_jsonl)
                    else:
                        rows[row_index] = row_for_jsonl
                    _atomic_write_jsonl(results_jsonl_path, rows)
                    _atomic_write_per_image_csv(per_image_csv_path, rows)
                else:
                    _append_jsonl(results_jsonl_path, row_for_jsonl)
                    _append_csv_row(per_image_csv_path, row, _CSV_FIELDS)
                    row_index_by_path[str(image_path)] = len(rows)
                    rows.append(row_for_jsonl)
                last_image_name = image_name
                if retry_failed_only:
                    retry_processed += 1
                if progress:
                    ok_count, failed_count = _count_by_status(rows)
                    average_seconds = (
                        (sum(runtime_samples) / len(runtime_samples)) if runtime_samples else None
                    )
                    _emit_progress(
                        _render_progress_line(
                            processed=len(rows) if not retry_failed_only else retry_processed,
                            total=progress_total,
                            ok_count=ok_count,
                            failed_count=failed_count,
                            average_seconds=average_seconds,
                        ),
                        final=False,
                    )
                _atomic_write_json(
                    state_path,
                    _build_state(
                        destination=destination,
                        input_glob=input_glob,
                        model=model,
                        api_base_url=api_base_url,
                        language_hint=language_hint,
                        total_images=len(image_paths),
                        rows=rows,
                        resumed=bool(existing_rows),
                        last_image_name=last_image_name,
                    ),
                )
    finally:
        if manage_model_loading and is_ollama_api_base_url(api_base_url):
            unload_ollama_model(
                api_base_url=api_base_url,
                model_name=model,
                poll_seconds=ollama_load_poll_seconds,
                poll_attempts=ollama_load_poll_attempts,
                timeout_seconds=timeout_seconds,
            )
        if progress:
            ok_count, failed_count = _count_by_status(rows)
            average_seconds = (sum(runtime_samples) / len(runtime_samples)) if runtime_samples else None
            _emit_progress(
                _render_progress_line(
                    processed=len(rows) if not retry_failed_only else retry_processed,
                    total=progress_total,
                    ok_count=ok_count,
                    failed_count=failed_count,
                    average_seconds=average_seconds,
                ),
                final=True,
            )

    elapsed_values = [
        float(row["elapsed_seconds"])
        for row in rows
        if row.get("status") == "ok" and row.get("elapsed_seconds") not in {"", None}
    ]
    summary = _build_summary(
        destination=destination,
        input_glob=input_glob,
        rows=rows,
        model=model,
        api_base_url=api_base_url,
        language_hint=language_hint,
        total_images=len(image_paths),
        elapsed_values=elapsed_values,
    )
    _write_json(destination / "summary.json", summary)
    _write_json(destination / "category_totals.json", _build_category_totals(rows))
    (destination / "summary.md").write_text(
        "\n".join(
            [
                "# Ollama Screenshot Categorization Summary",
                "",
                f"- Model: `{model}`",
                f"- Input glob: `{input_glob}`",
                f"- Images total: `{summary['images_total']}`",
                f"- Images processed: `{summary['images_processed']}`",
                f"- Images ok: `{summary['images_ok']}`",
                f"- Images failed: `{summary['images_failed']}`",
                f"- Mean elapsed (s): `{summary['elapsed_seconds_mean']}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Categorize screenshots with Ollama multimodal model and persist progress "
            "after every image."
        )
    )
    parser.add_argument(
        "--input-glob",
        default="/tmp/Screenshots/*",
        help="Glob pattern for input images.",
    )
    parser.add_argument(
        "--input-list-file",
        default="",
        help="Optional newline-delimited file with absolute input image paths.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Default: data/ocr_categories/ollama/<model>/<timestamp>.",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://127.0.0.1:11434",
        help="Ollama API base URL.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Ollama model name, e.g. qwen3.5:9b.",
    )
    parser.add_argument(
        "--language-hint",
        default="pl,en",
        help="Language hint embedded in the categorization prompt.",
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
        "--ollama-seed",
        type=int,
        default=None,
        help="Optional Ollama seed passed as options.seed.",
    )
    parser.add_argument(
        "--ollama-think",
        choices=("inherit", "false", "true", "low", "medium", "high"),
        default="false",
        help="Thinking mode for Ollama models. Default: false.",
    )
    parser.add_argument(
        "--ollama-keep-alive",
        default="30m",
        help="Ollama keep_alive duration used during categorization.",
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
        "--limit",
        type=int,
        default=None,
        help="Optional limit of processed images after sorting.",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume from existing results.jsonl in output directory. Default: enabled.",
    )
    parser.add_argument(
        "--save-raw",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist raw request/response artifacts per image. Default: enabled.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show live progress in stderr. Default: enabled.",
    )
    parser.add_argument(
        "--retry-failed-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Retry only rows with status=error from existing results.jsonl in --output-dir. "
            "When enabled, matching rows are replaced in-place instead of appended."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir) if args.output_dir else None
    input_list_file = Path(args.input_list_file) if args.input_list_file else None
    summary = run_categorization(
        input_glob=args.input_glob,
        input_list_file=input_list_file,
        output_dir=output_dir,
        api_base_url=args.api_base_url,
        model=args.model,
        language_hint=args.language_hint,
        temperature=args.temperature,
        timeout_seconds=args.timeout_seconds,
        max_output_tokens=args.max_output_tokens,
        ollama_num_predict=args.ollama_num_predict,
        ollama_num_ctx=args.ollama_num_ctx,
        ollama_seed=args.ollama_seed,
        ollama_keep_alive=args.ollama_keep_alive,
        ollama_think=args.ollama_think,
        ollama_load_poll_seconds=args.ollama_load_poll_seconds,
        ollama_load_poll_attempts=args.ollama_load_poll_attempts,
        manage_model_loading=args.manage_model_loading,
        limit=args.limit,
        resume=args.resume,
        save_raw=args.save_raw,
        progress=args.progress,
        retry_failed_only=args.retry_failed_only,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
