#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
PYTHON_FILTER_BIN="${PYTHON_FILTER_BIN:-python3}"
MIN_BYTES="${MIN_BYTES:-500}"
MIN_DIM="${MIN_DIM:-50}"
UNIFORM_STDDEV_THRESHOLD="${UNIFORM_STDDEV_THRESHOLD:-2.0}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  echo "Set PYTHON_BIN or create .venv first." >&2
  exit 1
fi

if ! command -v "$PYTHON_FILTER_BIN" >/dev/null 2>&1; then
  echo "Filter Python interpreter not found: $PYTHON_FILTER_BIN" >&2
  exit 1
fi

INPUT_GLOB="/tmp/Screenshots/*"
OUTPUT_DIR=""
RETRY_FAILED_ONLY=0
ARGS=("$@")

for ((i = 0; i < ${#ARGS[@]}; i++)); do
  case "${ARGS[$i]}" in
  -h | --help)
    PYTHONPATH=src "$PYTHON_BIN" -m snapgit.ocr_benchmark.ollama_screenshot_categorizer "$@"
    exit 0
    ;;
  --input-glob)
    if [ $((i + 1)) -lt ${#ARGS[@]} ]; then
      INPUT_GLOB="${ARGS[$((i + 1))]}"
      i=$((i + 1))
    fi
    ;;
  --input-glob=*)
    INPUT_GLOB="${ARGS[$i]#*=}"
    ;;
  --output-dir)
    if [ $((i + 1)) -lt ${#ARGS[@]} ]; then
      OUTPUT_DIR="${ARGS[$((i + 1))]}"
      i=$((i + 1))
    fi
    ;;
  --output-dir=*)
    OUTPUT_DIR="${ARGS[$i]#*=}"
    ;;
  --retry-failed-only)
    RETRY_FAILED_ONLY=1
    ;;
  --no-retry-failed-only)
    RETRY_FAILED_ONLY=0
    ;;
  esac
done

TMP_ACCEPTED="$(mktemp /tmp/ollama_categorizer.accepted.XXXXXX.txt)"
TMP_REJECTED="$(mktemp /tmp/ollama_categorizer.rejected.XXXXXX.csv)"
TMP_CANDIDATES="$(mktemp /tmp/ollama_categorizer.candidates.XXXXXX.txt)"

cleanup() {
  rm -f "$TMP_ACCEPTED"
  rm -f "$TMP_CANDIDATES"
}
trap cleanup EXIT

FILTER_INPUT_LIST_FILE=""
if [ "$RETRY_FAILED_ONLY" -eq 1 ] && [ -n "$OUTPUT_DIR" ]; then
  RESULTS_JSONL="$OUTPUT_DIR/results.jsonl"
  if [ -f "$RESULTS_JSONL" ]; then
    if ! RESULTS_JSONL="$RESULTS_JSONL" FILTER_OUTPUT_CANDIDATES="$TMP_CANDIDATES" "$PYTHON_FILTER_BIN" - <<'PY'
import json
import os
import sys
from pathlib import Path

results_path = Path(os.environ["RESULTS_JSONL"])
candidates_path = Path(os.environ["FILTER_OUTPUT_CANDIDATES"])

rows = []
with results_path.open("r", encoding="utf-8") as handle:
    for line in handle:
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))

failed_paths = []
for row in rows:
    if str(row.get("status", "")).lower() != "error":
        continue
    image_path = str(row.get("image_path", "")).strip()
    if not image_path:
        continue
    failed_paths.append(image_path)

if not failed_paths:
    print(
        f"No failed rows found in {results_path}. Nothing to retry.",
        file=sys.stderr,
    )
    sys.exit(10)

with candidates_path.open("w", encoding="utf-8") as handle:
    for image_path in sorted(set(failed_paths)):
        handle.write(image_path + "\n")
PY
    then
      status_code=$?
      if [ "$status_code" -eq 10 ]; then
        echo "No failed rows to retry. Exiting." >&2
        exit 0
      fi
      exit "$status_code"
    fi
    FILTER_INPUT_LIST_FILE="$TMP_CANDIDATES"
  else
    echo "Warning: --retry-failed-only set but results file not found: $RESULTS_JSONL" >&2
    echo "Falling back to full input glob filtering." >&2
  fi
elif [ "$RETRY_FAILED_ONLY" -eq 1 ]; then
  echo "Warning: --retry-failed-only set without --output-dir. Falling back to full input glob filtering." >&2
fi

FILTER_INPUT_GLOB="$INPUT_GLOB" \
FILTER_INPUT_LIST_FILE="$FILTER_INPUT_LIST_FILE" \
FILTER_OUTPUT_ACCEPTED="$TMP_ACCEPTED" \
FILTER_OUTPUT_REJECTED="$TMP_REJECTED" \
FILTER_MIN_BYTES="$MIN_BYTES" \
FILTER_MIN_DIM="$MIN_DIM" \
FILTER_UNIFORM_STDDEV_THRESHOLD="$UNIFORM_STDDEV_THRESHOLD" \
  "$PYTHON_FILTER_BIN" - <<'PY'
import csv
import glob
import os
import sys
from pathlib import Path

from PIL import Image, ImageStat

input_glob = os.environ["FILTER_INPUT_GLOB"]
input_list_file = os.environ.get("FILTER_INPUT_LIST_FILE", "").strip()
accepted_path = Path(os.environ["FILTER_OUTPUT_ACCEPTED"])
rejected_path = Path(os.environ["FILTER_OUTPUT_REJECTED"])
min_bytes = int(os.environ["FILTER_MIN_BYTES"])
min_dim = int(os.environ["FILTER_MIN_DIM"])
uniform_stddev_threshold = float(os.environ["FILTER_UNIFORM_STDDEV_THRESHOLD"])
allowed_formats = {"PNG", "JPEG", "GIF"}

if input_list_file:
    with Path(input_list_file).open("r", encoding="utf-8") as handle:
        paths = sorted(Path(line.strip()) for line in handle if line.strip())
    input_source = f"list={input_list_file}"
else:
    paths = sorted(Path(path) for path in glob.glob(input_glob))
    input_source = f"glob={input_glob}"

accepted = 0
rejected = 0

with accepted_path.open("w", encoding="utf-8") as accepted_handle, rejected_path.open(
    "w", encoding="utf-8", newline=""
) as rejected_handle:
    writer = csv.writer(rejected_handle)
    writer.writerow(["path", "reason", "size_bytes", "width", "height", "format", "stddev_max"])

    for path in paths:
        if not path.is_file():
            continue

        size_bytes = path.stat().st_size
        if size_bytes <= min_bytes:
            writer.writerow([str(path), f"size<={min_bytes}", size_bytes, "", "", "", ""])
            rejected += 1
            continue

        try:
            with Image.open(path) as image:
                image.load()
                image_format = str(image.format or "").upper()
                width, height = image.size
                rgb = image.convert("RGB")
                extrema = rgb.getextrema()
                stat = ImageStat.Stat(rgb)
                stddev_max = max(stat.stddev) if stat.stddev else 0.0
        except Exception as exc:
            writer.writerow([str(path), f"invalid_image:{exc}", size_bytes, "", "", "", ""])
            rejected += 1
            continue

        if image_format not in allowed_formats:
            writer.writerow(
                [str(path), "unsupported_format", size_bytes, width, height, image_format, f"{stddev_max:.6f}"]
            )
            rejected += 1
            continue

        if max(width, height) <= min_dim:
            writer.writerow(
                [str(path), f"max_dim<={min_dim}", size_bytes, width, height, image_format, f"{stddev_max:.6f}"]
            )
            rejected += 1
            continue

        is_exact_uniform = all(channel_min == channel_max for channel_min, channel_max in extrema)
        if is_exact_uniform or stddev_max < uniform_stddev_threshold:
            writer.writerow(
                [str(path), "uniform_image", size_bytes, width, height, image_format, f"{stddev_max:.6f}"]
            )
            rejected += 1
            continue

        accepted_handle.write(str(path) + "\n")
        accepted += 1

if accepted == 0:
    print(
        f"Image filter rejected all candidate files from {input_source}. "
        f"See rejected list: {rejected_path}",
        file=sys.stderr,
    )
    sys.exit(2)

print(
    f"Image filter: source={input_source} total={len(paths)} accepted={accepted} rejected={rejected} "
    f"(min_bytes>{min_bytes}, max_dim>{min_dim}, formats=PNG/JPEG/GIF, stddev>={uniform_stddev_threshold})",
    file=sys.stderr,
)
print(f"Rejected details CSV: {rejected_path}", file=sys.stderr)
PY

PYTHONPATH=src "$PYTHON_BIN" -m snapgit.ocr_benchmark.ollama_screenshot_categorizer \
  "$@" \
  --input-glob "$INPUT_GLOB" \
  --input-list-file "$TMP_ACCEPTED"
