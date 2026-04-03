from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _normalize_label(value: Any) -> str:
    return str(value or "").strip().lower()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid JSONL object at line {line_number}")
            rows.append(payload)
    return rows


def _extract_categories(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    categories_raw = row.get("categories")
    if isinstance(categories_raw, list):
        categories = categories_raw
    else:
        categories_json_raw = row.get("categories_json", "")
        categories = []
        if categories_json_raw:
            try:
                parsed = json.loads(str(categories_json_raw))
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                categories = parsed

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in categories:
        if not isinstance(item, Mapping):
            continue
        pl = _normalize_label(item.get("pl"))
        en = _normalize_label(item.get("en"))
        if not pl or not en:
            continue
        pair = (pl, en)
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    return pairs


def summarize_category_frequency(
    *,
    input_jsonl: Path,
    include_status: str,
) -> dict[str, Any]:
    rows = _read_jsonl(input_jsonl)

    total_rows = len(rows)
    processed_rows = 0
    pair_counter: Counter[tuple[str, str]] = Counter()
    pl_counter: Counter[str] = Counter()
    en_counter: Counter[str] = Counter()

    for row in rows:
        status = str(row.get("status", "")).strip().lower()
        if include_status == "ok" and status != "ok":
            continue
        if include_status == "error" and status != "error":
            continue
        processed_rows += 1

        for pl, en in _extract_categories(row):
            pair_counter[(pl, en)] += 1
            pl_counter[pl] += 1
            en_counter[en] += 1

    pair_rows = [
        {
            "pl": pl,
            "en": en,
            "count": count,
        }
        for (pl, en), count in pair_counter.items()
    ]
    pair_rows.sort(key=lambda item: (int(item["count"]), str(item["en"]), str(item["pl"])))

    pl_rows = [{"label": label, "count": count} for label, count in pl_counter.items()]
    pl_rows.sort(key=lambda item: (int(item["count"]), str(item["label"])))

    en_rows = [{"label": label, "count": count} for label, count in en_counter.items()]
    en_rows.sort(key=lambda item: (int(item["count"]), str(item["label"])))

    return {
        "input_jsonl": str(input_jsonl.resolve()),
        "rows_total": total_rows,
        "rows_included": processed_rows,
        "status_filter": include_status,
        "pairs": pair_rows,
        "pl_only": pl_rows,
        "en_only": en_rows,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize category frequency from results.jsonl. "
            "Default sort is ascending (rarest to most common)."
        )
    )
    parser.add_argument(
        "--input-jsonl",
        default="data/ocr_categories/ollama/qwen3_5_9b_screenshots/results.jsonl",
        help="Path to categorizer results JSONL.",
    )
    parser.add_argument(
        "--status",
        choices=("ok", "error", "all"),
        default="ok",
        help="Which row statuses to include.",
    )
    parser.add_argument(
        "--view",
        choices=("pairs", "pl", "en"),
        default="pairs",
        help="Which frequency list to print to stdout.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional number of rows to print.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional JSON output path with full summary.",
    )
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional CSV output path for selected --view.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    input_jsonl = Path(args.input_jsonl)
    if not input_jsonl.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_jsonl}")

    summary = summarize_category_frequency(
        input_jsonl=input_jsonl,
        include_status=args.status,
    )

    if args.view == "pairs":
        rows: list[dict[str, Any]] = list(summary["pairs"])
        fieldnames = ["count", "pl", "en"]
    elif args.view == "pl":
        rows = list(summary["pl_only"])
        fieldnames = ["count", "label"]
    else:
        rows = list(summary["en_only"])
        fieldnames = ["count", "label"]

    if args.limit is not None:
        rows = rows[: args.limit]

    print(
        f"# source={summary['input_jsonl']} "
        f"rows_included={summary['rows_included']}/{summary['rows_total']} "
        f"status={summary['status_filter']} view={args.view}"
    )
    for row in rows:
        if args.view == "pairs":
            print(f"{row['count']}\t{row['pl']}\t{row['en']}")
        else:
            print(f"{row['count']}\t{row['label']}")

    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.output_csv:
        output_csv = Path(args.output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(output_csv, rows, fieldnames)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
