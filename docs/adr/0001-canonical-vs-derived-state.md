# ADR 0001: Canonical vs Derived State

- Status: Accepted
- Date: 2026-04-01

## Context

The backend stores operational data in SQLite and exposes a search experience built from OCR/indexing outputs.
We need clear rules for what data is authoritative and what data can be rebuilt.

## Decision

Treat relational tables as canonical state, and treat full-text search projection as derived state.

Canonical state includes:

- `blobs`
- `assets`
- `pipeline_runs`
- `stage_results`
- stage output tables (for example, OCR text tables used to build search docs)

Derived state includes:

- FTS5 projection table `search_docs_fts`

## Rationale

- Canonical rows must survive process restarts and pipeline retries.
- Derived search projection can be recomputed deterministically from canonical stage outputs.
- This separation allows replay/reindex operations without mutating source-of-truth rows.

## Consequences

Positive:

- Recovery is simpler: rebuild indexes instead of rewriting canonical records.
- Replay and backfill stay idempotent because canonical identity keys remain stable.

Trade-offs:

- Additional operational step is needed when canonical OCR/index inputs change: reindex derived tables.

## Operational Commands

Apply schema and start API:

```bash
uv run alembic upgrade head
uv run uvicorn snapgit.main:app --host 127.0.0.1 --port 8000 --reload
```

Rebuild derived state for one asset:

```bash
uv run python -c "from snapgit.scripts.reindex import reindex_asset; reindex_asset(1)"
```

Validate expected API behavior after rebuild:

```bash
curl -sS "http://127.0.0.1:8000/search?q=example"
```
