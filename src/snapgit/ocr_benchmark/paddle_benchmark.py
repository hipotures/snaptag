from __future__ import annotations

import argparse
import csv
import glob
import inspect
import json
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


def _to_serializable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _to_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serializable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "tolist"):
        return _to_serializable(value.tolist())
    return str(value)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_from_mapping(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    texts = item.get("rec_texts") or item.get("texts") or []
    scores = item.get("rec_scores") or item.get("scores") or []
    boxes = item.get("dt_polys") or item.get("boxes") or []

    lines: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        score = _safe_float(scores[index]) if index < len(scores) else None
        box = boxes[index] if index < len(boxes) else None
        lines.append(
            {
                "text": str(text),
                "confidence": score,
                "bbox": _to_serializable(box),
            }
        )
    return lines


def extract_lines_from_result(result: Any) -> list[dict[str, Any]]:
    serializable = _to_serializable(result)

    if isinstance(serializable, Mapping):
        return _extract_from_mapping(serializable)

    if not isinstance(serializable, list):
        return []

    if serializable and isinstance(serializable[0], list):
        flattened = serializable[0]
    else:
        flattened = serializable

    if flattened and isinstance(flattened[0], Mapping):
        lines: list[dict[str, Any]] = []
        for item in flattened:
            lines.extend(_extract_from_mapping(item))
        return lines

    lines = []
    for entry in flattened:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        bbox = entry[0]
        text_conf = entry[1]
        if not isinstance(text_conf, (list, tuple)) or not text_conf:
            continue
        text = str(text_conf[0])
        confidence = _safe_float(text_conf[1]) if len(text_conf) > 1 else None
        lines.append(
            {
                "text": text,
                "confidence": confidence,
                "bbox": _to_serializable(bbox),
            }
        )
    return lines


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


def build_paddle_init_kwargs(
    *,
    supported_params: set[str],
    lang: str,
    use_angle_cls: bool,
    use_gpu: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"lang": lang}

    if "use_textline_orientation" in supported_params:
        kwargs["use_textline_orientation"] = use_angle_cls
    elif "use_angle_cls" in supported_params:
        kwargs["use_angle_cls"] = use_angle_cls

    if "use_gpu" in supported_params:
        kwargs["use_gpu"] = use_gpu
    elif "device" in supported_params:
        kwargs["device"] = "gpu" if use_gpu else "cpu"

    return kwargs


def _build_ocr_engine(*, lang: str, use_angle_cls: bool, use_gpu: bool) -> Any:
    try:
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError(
            "PaddleOCR is not installed. Install it first, e.g. "
            "`uv pip install paddleocr paddlepaddle`."
        ) from exc

    signature = inspect.signature(PaddleOCR.__init__)
    params = signature.parameters
    supported_params = {name for name in params if name not in {"self", "kwargs"}}
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()
    )

    base_kwargs = build_paddle_init_kwargs(
        supported_params=supported_params,
        lang=lang,
        use_angle_cls=use_angle_cls,
        use_gpu=use_gpu,
    )

    attempts: list[dict[str, Any]] = [base_kwargs]
    if accepts_kwargs and "device" not in base_kwargs:
        attempts.append(
            {
                **base_kwargs,
                "device": "gpu" if use_gpu else "cpu",
            }
        )
    attempts.append(
        {
            key: value
            for key, value in base_kwargs.items()
            if key not in {"use_gpu", "device"}
        }
    )
    attempts.append({"lang": lang})

    deduped_attempts: list[dict[str, Any]] = []
    for kwargs in attempts:
        if kwargs not in deduped_attempts:
            deduped_attempts.append(kwargs)

    last_error: Exception | None = None
    for kwargs in deduped_attempts:
        try:
            return PaddleOCR(**kwargs)
        except (TypeError, ValueError) as exc:
            last_error = exc
    if last_error is None:  # pragma: no cover
        raise RuntimeError("Unexpected error creating PaddleOCR engine")
    raise RuntimeError(f"Unable to initialize PaddleOCR: {last_error}") from last_error


def _run_ocr_on_image(ocr: Any, image_path: Path, *, use_angle_cls: bool) -> Any:
    if hasattr(ocr, "predict"):
        try:
            return ocr.predict(
                str(image_path),
                use_textline_orientation=use_angle_cls,
            )
        except TypeError:
            return ocr.predict(str(image_path))
    if hasattr(ocr, "ocr"):
        return ocr.ocr(str(image_path), cls=use_angle_cls)
    raise RuntimeError("Unsupported PaddleOCR object: expected `ocr` or `predict` method")


def _mean_or_none(values: Iterable[float]) -> float | None:
    values_list = list(values)
    if not values_list:
        return None
    return mean(values_list)


def _ensure_output_dir(path: Path | None) -> Path:
    if path is not None:
        output_dir = path
    else:
        now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        output_dir = Path("data/ocr_bench/paddleocr") / now
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


def run_benchmark(
    *,
    input_glob: str,
    output_dir: Path | None,
    lang: str,
    use_angle_cls: bool,
    use_gpu: bool,
    ground_truth_path: Path | None,
    limit: int | None,
) -> dict[str, Any]:
    image_paths = sorted(Path(path) for path in glob.glob(input_glob))
    if limit is not None:
        image_paths = image_paths[:limit]
    if not image_paths:
        raise RuntimeError(f"No input images found for pattern: {input_glob}")

    gt_map = _load_ground_truth(ground_truth_path)
    destination = _ensure_output_dir(output_dir)
    ocr = _build_ocr_engine(lang=lang, use_angle_cls=use_angle_cls, use_gpu=use_gpu)

    per_image_rows: list[dict[str, Any]] = []
    per_line_rows: list[dict[str, Any]] = []
    confidence_values: list[float] = []
    cer_values: list[float] = []
    wer_values: list[float] = []
    failures: list[dict[str, str]] = []

    for image_path in image_paths:
        image_name = image_path.name
        try:
            raw_result = _run_ocr_on_image(ocr, image_path, use_angle_cls=use_angle_cls)
            lines = extract_lines_from_result(raw_result)
        except Exception as exc:  # pragma: no cover - runtime dependency
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
                    "error": str(exc),
                }
            )
            continue

        raw_path = destination / "raw" / f"{image_path.stem}.json"
        _write_json(raw_path, _to_serializable(raw_result))

        predicted_text = "\n".join(line["text"] for line in lines if line.get("text"))
        line_confidences = [
            float(line["confidence"])
            for line in lines
            if line.get("confidence") is not None
        ]
        confidence_values.extend(line_confidences)

        for line_index, line in enumerate(lines):
            per_line_rows.append(
                {
                    "image_name": image_name,
                    "line_index": line_index,
                    "text": line.get("text", ""),
                    "confidence": line.get("confidence", ""),
                    "bbox_json": json.dumps(line.get("bbox"), ensure_ascii=False),
                }
            )

        row: dict[str, Any] = {
            "image_name": image_name,
            "image_path": str(image_path),
            "status": "ok",
            "line_count": len(lines),
            "char_count": len(predicted_text),
            "mean_confidence": _mean_or_none(line_confidences),
            "min_confidence": min(line_confidences) if line_confidences else "",
            "max_confidence": max(line_confidences) if line_confidences else "",
            "predicted_text": predicted_text,
            "ground_truth_text": "",
            "cer": "",
            "wer": "",
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
        "engine": "paddleocr",
        "utc_timestamp": datetime.now(timezone.utc).isoformat(),
        "input_glob": input_glob,
        "images_total": len(image_paths),
        "images_ok": len(processed_ok),
        "images_failed": len(failures),
        "lines_total": int(sum(int(row["line_count"]) for row in processed_ok)),
        "chars_total": int(sum(int(row["char_count"]) for row in processed_ok)),
        "confidence_mean": _mean_or_none(confidence_values),
        "confidence_min": min(confidence_values) if confidence_values else None,
        "confidence_max": max(confidence_values) if confidence_values else None,
        "ground_truth_images": len(cer_values),
        "mean_cer": _mean_or_none(cer_values),
        "mean_wer": _mean_or_none(wer_values),
        "failures": failures,
        "output_dir": str(destination.resolve()),
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
        "# PaddleOCR Benchmark Summary",
        "",
        f"- Input glob: `{input_glob}`",
        f"- Images total: `{summary['images_total']}`",
        f"- Images ok: `{summary['images_ok']}`",
        f"- Images failed: `{summary['images_failed']}`",
        f"- Lines total: `{summary['lines_total']}`",
        f"- Mean confidence: `{summary['confidence_mean']}`",
        f"- Ground-truth images: `{summary['ground_truth_images']}`",
        f"- Mean CER: `{summary['mean_cer']}`",
        f"- Mean WER: `{summary['mean_wer']}`",
    ]
    (destination / "summary.md").write_text("\n".join(markdown_summary) + "\n", encoding="utf-8")

    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run PaddleOCR benchmark on screenshots and save quality artifacts."
    )
    parser.add_argument(
        "--input-glob",
        default="/tmp/scr/*.png",
        help="Glob pattern for input images.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Default: data/ocr_bench/paddleocr/<timestamp>.",
    )
    parser.add_argument("--lang", default="en", help="PaddleOCR language code.")
    parser.add_argument(
        "--no-angle-cls",
        action="store_true",
        help="Disable angle classifier in OCR call.",
    )
    parser.add_argument(
        "--use-gpu",
        action="store_true",
        help="Ask PaddleOCR to use GPU if available.",
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
        lang=args.lang,
        use_angle_cls=not args.no_angle_cls,
        use_gpu=args.use_gpu,
        ground_truth_path=ground_truth_path,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
