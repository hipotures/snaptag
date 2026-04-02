#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-.venv-paddle313/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  echo "Set PYTHON_BIN or create .venv-paddle313 first." >&2
  exit 1
fi

: "${PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK:=True}"
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK

PYTHONPATH=src "$PYTHON_BIN" -m snapgit.ocr_benchmark.paddle_benchmark "$@"
