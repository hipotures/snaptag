# SnapGit Backend (Phase 1)

Local-only backend for screenshot ingest, pipeline orchestration, and search.

## Prerequisites

- Python 3.13+
- `uv`

## Local Setup

```bash
uv sync --extra dev
uv run alembic upgrade head
```

## Run the API

```bash
uv run uvicorn snapgit.main:app --host 127.0.0.1 --port 8000 --reload
```

## Smoke Check the API

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS -X POST http://127.0.0.1:8000/ingest -H "Content-Type: application/json" -d '{"source_type":"filesystem_backfill","path":"/tmp/example.png"}'
curl -sS "http://127.0.0.1:8000/search?q=test"
```

## Backfill From a Folder

```bash
bash scripts/backfill_from_folder.sh /absolute/path/to/screenshots http://127.0.0.1:8000
```

## Run Tests

```bash
uv run pytest -v
uv run pytest tests/unit/test_docs_exist.py -v
```

## Additional Documentation

- Runtime and runbook: `docs/architecture/backend-runtime.md`
- Architecture decision records: `docs/adr/`
