from __future__ import annotations

import argparse
import csv
import glob
import json
import subprocess
from collections import defaultdict
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


def _safe_float(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def parse_tesseract_tsv(tsv_text: str) -> list[dict[str, Any]]:
    lines = [line for line in tsv_text.splitlines() if line.strip()]
    if not lines:
        return []

    reader = csv.DictReader(lines, delimiter="\t")
    words: list[dict[str, Any]] = []
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue

        conf_value = _safe_float((row.get("conf") or "").strip())
        words.append(
            {
                "page_num": _safe_int((row.get("page_num") or "").strip()),
                "block_num": _safe_int((row.get("block_num") or "").strip()),
                "par_num": _safe_int((row.get("par_num") or "").strip()),
                "line_num": _safe_int((row.get("line_num") or "").strip()),
                "word_num": _safe_int((row.get("word_num") or "").strip()),
                "text": text,
                "conf": conf_value,
                "bbox": {
                    "left": _safe_int((row.get("left") or "").strip()),
                    "top": _safe_int((row.get("top") or "").strip()),
                    "width": _safe_int((row.get("width") or "").strip()),
                    "height": _safe_int((row.get("height") or "").strip()),
                },
            }
        )
    return words


def aggregate_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        key = (
            int(word.get("page_num", 0)),
            int(word.get("block_num", 0)),
            int(word.get("par_num", 0)),
            int(word.get("line_num", 0)),
        )
        grouped[key].append(word)

    lines: list[dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        group = sorted(grouped[key], key=lambda item: int(item.get("word_num", 0)))
        text = " ".join(str(item.get("text", "")) for item in group if item.get("text"))
        confidences = [
            float(item["conf"])
            for item in group
            if item.get("conf") is not None and float(item["conf"]) >= 0
        ]
        left = min(int(item["bbox"]["left"]) for item in group)
        top = min(int(item["bbox"]["top"]) for item in group)
        right = max(int(item["bbox"]["left"]) + int(item["bbox"]["width"]) for item in group)
        bottom = max(int(item["bbox"]["top"]) + int(item["bbox"]["height"]) for item in group)

        lines.append(
            {
                "text": text,
                "confidence": mean(confidences) if confidences else None,
                "bbox": {
                    "left": left,
                    "top": top,
                    "width": max(right - left, 0),
                    "height": max(bottom - top, 0),
                },
                "page_num": key[0],
                "block_num": key[1],
                "par_num": key[2],
                "line_num": key[3],
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


def _ensure_output_dir(path: Path | None) -> Path:
    if path is not None:
        output_dir = path
    else:
        now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        output_dir = Path("data/ocr_bench/tesseract") / now
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


def _normalize_lang(lang: str) -> str:
    aliases = {"pl": "pol", "en": "eng"}
    return aliases.get(lang.lower(), lang)


def _list_available_langs() -> set[str]:
    completed = subprocess.run(
        ["tesseract", "--list-langs"],
        check=True,
        capture_output=True,
        text=True,
    )
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return {line for line in output_lines if not line.startswith("List of available languages")}


def _validate_langs(lang: str) -> None:
    requested = [_normalize_lang(token) for token in lang.split("+") if token.strip()]
    available = _list_available_langs()
    missing = [token for token in requested if token not in available]
    if missing:
        raise RuntimeError(
            f"Missing Tesseract language data: {missing}. "
            f"Installed languages: {sorted(available)}"
        )


def _run_tesseract(image_path: Path, *, lang: str, psm: int, oem: int) -> str:
    normalized_lang = "+".join(_normalize_lang(token) for token in lang.split("+") if token.strip())
    command = [
        "tesseract",
        str(image_path),
        "stdout",
        "--oem",
        str(oem),
        "--psm",
        str(psm),
        "-l",
        normalized_lang,
        "tsv",
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def run_benchmark(
    *,
    input_glob: str,
    output_dir: Path | None,
    lang: str,
    psm: int,
    oem: int,
    ground_truth_path: Path | None,
    limit: int | None,
) -> dict[str, Any]:
    image_paths = sorted(Path(path) for path in glob.glob(input_glob))
    if limit is not None:
        image_paths = image_paths[:limit]
    if not image_paths:
        raise RuntimeError(f"No input images found for pattern: {input_glob}")

    _validate_langs(lang)
    gt_map = _load_ground_truth(ground_truth_path)
    destination = _ensure_output_dir(output_dir)

    per_image_rows: list[dict[str, Any]] = []
    per_line_rows: list[dict[str, Any]] = []
    confidence_values: list[float] = []
    cer_values: list[float] = []
    wer_values: list[float] = []
    failures: list[dict[str, str]] = []

    for image_path in image_paths:
        image_name = image_path.name
        try:
            tsv_text = _run_tesseract(image_path, lang=lang, psm=psm, oem=oem)
            words = parse_tesseract_tsv(tsv_text)
            lines = aggregate_lines(words)
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
                    "error": str(exc),
                }
            )
            continue

        (destination / "raw" / f"{image_path.stem}.tsv").write_text(tsv_text, encoding="utf-8")
        _write_json(destination / "raw" / f"{image_path.stem}.lines.json", lines)

        predicted_text = "\n".join(str(line["text"]) for line in lines if line.get("text"))
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
        "engine": "tesseract",
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
        "lang": "+".join(_normalize_lang(token) for token in lang.split("+") if token.strip()),
        "psm": psm,
        "oem": oem,
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
        "# Tesseract Benchmark Summary",
        "",
        f"- Input glob: `{input_glob}`",
        f"- Lang: `{summary['lang']}`",
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
        description="Run Tesseract benchmark on screenshots and save quality artifacts."
    )
    parser.add_argument(
        "--input-glob",
        default="/tmp/scr/*.png",
        help="Glob pattern for input images.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Default: data/ocr_bench/tesseract/<timestamp>.",
    )
    parser.add_argument(
        "--lang",
        default="pol",
        help="Tesseract lang code (e.g. pol, eng, pol+eng). Aliases: pl->pol, en->eng.",
    )
    parser.add_argument("--psm", type=int, default=3, help="Tesseract page segmentation mode.")
    parser.add_argument("--oem", type=int, default=1, help="Tesseract OCR engine mode.")
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
        psm=args.psm,
        oem=args.oem,
        ground_truth_path=ground_truth_path,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

