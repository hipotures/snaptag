#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  echo "Set PYTHON_BIN or create .venv first." >&2
  exit 1
fi

PYTHONPATH=src "$PYTHON_BIN" -m snapgit.ocr_benchmark.vllm_ocr_benchmark "$@"
