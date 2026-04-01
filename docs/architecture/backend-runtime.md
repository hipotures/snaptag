# Backend Runtime and Runbook

## Runtime Overview

The phase 1 backend is a single FastAPI process backed by SQLite.

- API entrypoint: `snapgit.main:app`
- Config object: `snapgit.common.settings.Settings`
- Database migrations: Alembic in `alembic/`
- Reindex function: `snapgit.scripts.reindex.reindex_asset`

## Local Runbook

### 1. Install dependencies

```bash
uv sync --extra dev
```

### 2. Apply schema migrations

```bash
uv run alembic upgrade head
```

### 3. Start API server

```bash
uv run uvicorn snapgit.main:app --host 127.0.0.1 --port 8000 --reload
```

### 4. Verify API health and basic endpoints

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS -X POST http://127.0.0.1:8000/ingest -H "Content-Type: application/json" -d '{"source_type":"filesystem_backfill","path":"/tmp/example.png"}'
curl -sS "http://127.0.0.1:8000/search?q=example"
```

### 5. Backfill screenshots from disk

```bash
bash scripts/backfill_from_folder.sh /absolute/path/to/screenshots http://127.0.0.1:8000
```

### 6. Rebuild derived search projection for an asset

```bash
uv run python -c "from snapgit.scripts.reindex import reindex_asset; reindex_asset(1)"
```

### 7. Run verification tests

```bash
uv run pytest tests/unit/test_docs_exist.py -v
uv run pytest tests/integration/test_api_ingest_search.py -v
```

## Notes

- The worker process and external queue broker are not introduced in phase 1.
- SQLite is the source of truth for canonical state; search index tables are rebuildable.
