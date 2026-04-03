# SnapTag (Experimental) - Backend Repository

SnapTag is a local-first screenshot memory system for a single user.
Goal: turn a stream of screenshots into searchable, structured memory and action suggestions.

This repository currently contains the backend service (codename: `snapgit`) and OCR benchmarking tooling.

## Project Status

This project is in an active experimental phase.

- Product direction is defined, but implementation is still phase-1 and backend-first.
- Core backend flow works locally (ingest -> OCR -> index -> search).
- API and schema are still evolving.
- OCR model selection and benchmarking are in progress.
- Expect breaking changes while phase 1 is being refined.

## What The Project Should Do

End-state product flow:

1. Collect screenshots from user devices (primary source: Android).
2. Ingest screenshots into local storage on Linux backend.
3. Run OCR and extract searchable text/entities.
4. Build a search index and provide fast retrieval.
5. Generate optional action suggestions based on extracted content.

Current repository scope:

- Implemented here: backend API, ingest pipeline, search indexing, OCR benchmarking.
- Not implemented here as production components: mobile app/client UX and full product UI.

## What Is Implemented

- FastAPI service with local SQLite storage.
- Ingest with blob deduplication (`sha256`) and timestamp selection.
- Searchable baseline pipeline (`ocr` + `index`) with FTS-style querying.
- Reindex maintenance endpoint.
- OCR benchmark runners (Tesseract, PaddleOCR, Ollama, llama.cpp, vLLM).

## Requirements

- Python 3.13+
- `uv`

## Quickstart

```bash
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn snapgit.main:app --host 127.0.0.1 --port 8000 --reload
```

## API Smoke Test

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS -X POST http://127.0.0.1:8000/ingest -H "Content-Type: application/json" -d '{"source_type":"filesystem_backfill","path":"/tmp/example.png"}'
curl -sS "http://127.0.0.1:8000/search?q=example"
curl -sS "http://127.0.0.1:8000/screenshots?limit=10&offset=0"
```

## Main Endpoints

- `GET /health`
- `POST /ingest`
- `GET /search?q=<query>`
- `GET /screenshots?limit=<n>&offset=<n>`
- `POST /actions/reindex`

## Backfill From Folder

```bash
bash scripts/backfill_from_folder.sh /absolute/path/to/screenshots http://127.0.0.1:8000
```

## OCR Benchmarking (Model Selection)

Benchmark outputs are saved under `data/ocr_bench/<engine>/...` and include:

- `summary.json` and `summary.md`
- `per_image.csv`
- `per_line.csv`
- raw model outputs (`raw/`)

Examples:

```bash
bash scripts/run_tesseract_ocr_benchmark.sh --input-glob "/tmp/scr/*.png"
bash scripts/run_paddleocr_benchmark.sh --input-glob "/tmp/scr/*.png"
bash scripts/run_ollama_ocr_benchmark.sh --input-glob "/tmp/scr/*.png" --model qwen2.5vl:7b
bash scripts/run_llamacpp_ocr_benchmark.sh --input-glob "/tmp/scr/*.png" --model qwen2.5-vl-instruct
bash scripts/run_vllm_ocr_benchmark.sh --input-glob "/tmp/scr/*.png" --model Qwen/Qwen2.5-VL-7B-Instruct
bash scripts/run_ollama_screenshot_categorizer.sh --input-glob "/tmp/Screenshots/*" --model qwen3.5:9b
```

Notes:

- Paddle runner defaults to `.venv-paddle313/bin/python`.
- Others default to `.venv/bin/python`.
- Some benchmark engines require external services running locally (Ollama / OpenAI-compatible server).
- Screenshot categorizer writes incremental checkpoints after each image (`results.jsonl`, `state.json`), so interrupted runs can be resumed safely.

## Tests

```bash
uv run pytest -v
uv run pytest tests/integration/test_api_ingest_search.py -v
uv run pytest tests/unit/test_docs_exist.py -v
```

## Docs

- Runtime/runbook: `docs/architecture/backend-runtime.md`
- ADRs: `docs/adr/`
- Design spec: `docs/superpowers/specs/2026-04-01-snapgit-backend-design.md`

## License

CC0 1.0 Universal (see `LICENSE`).
