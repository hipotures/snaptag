#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <folder_path> [api_base_url]"
  exit 1
fi

folder_path="$1"
api_base_url="${2:-http://127.0.0.1:8000}"

if [ ! -d "$folder_path" ]; then
  echo "Folder not found: $folder_path"
  exit 1
fi

for screenshot_path in "$folder_path"/*; do
  if [ -f "$screenshot_path" ]; then
    payload="$(SNAPGIT_PATH="$screenshot_path" python3 -c 'import json, os; print(json.dumps({"source_type": "filesystem_backfill", "path": os.environ["SNAPGIT_PATH"]}))')"
    curl --fail --silent --show-error \
      -X POST \
      -H "Content-Type: application/json" \
      -d "$payload" \
      "$api_base_url/ingest"
    echo
  fi
done
