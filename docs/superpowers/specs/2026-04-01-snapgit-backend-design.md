# SnapGit Backend Design (Phase 1)

## 1. Context and Product Direction

SnapGit is a local-first screenshot intelligence backend for one user.
The input is a stream of screenshots (initially Android source, Linux backend).
The backend must extract useful structure from screenshots and provide reliable retrieval and action suggestions.

This design intentionally optimizes for:
- local deployment on one Linux machine,
- sequential AI processing,
- modular and independently testable components,
- safe recovery after interruption,
- future migration path from SQLite to PostgreSQL/MySQL.

## 2. Goals

- Build a backend foundation that supports screenshot ingest, analysis, indexing, and retrieval.
- Keep modules isolated so each component can be tested independently.
- Ensure pipeline execution is idempotent and restart-safe.
- Use SQLite initially without locking the design to SQLite-specific queue tooling.
- Keep architecture simple for fast iteration before Android frontend integration.

## 3. Non-Goals (Phase 1)

- Multi-user accounts, authentication, tenant isolation.
- Cloud-first deployment.
- High-throughput parallel GPU inference.
- Aggressive autonomous action execution without user confirmation.
- Full production hardening for internet-exposed deployment.

## 4. High-Level Architecture

Recommended approach: **Modular monolith + local job queue**.

Core modules:
- `ingest`: receives screenshot payloads, validates input, stores files, deduplicates.
- `pipeline`: orchestrates stage jobs and retries; contains no model logic.
- `ocr`: text and layout extraction.
- `vision`: screenshot-type classification and entity extraction via VLM.
- `enrichment`: optional local enrichment of extracted entities.
- `index`: normalized document build, embeddings, and indexing.
- `search`: full-text + semantic retrieval + filters.
- `actions`: confidence-based suggestion generation.
- `api`: HTTP interface for ingest, browse, search, and action suggestions.
- `storage`: file/object paths and relational persistence.
- `observability`: logs, metrics, and pipeline event trace.

## 5. Repository Structure

```text
snapgit/
  pyproject.toml
  README.md
  .env.example

  configs/
    app.yaml
    logging.yaml
    models.yaml
    pipeline.yaml

  scripts/
    dev_run_api.sh
    dev_run_worker.sh
    reindex.sh
    backfill_from_folder.sh

  data/
    inbox/
    screenshots/
    artifacts/
    exports/
    db/
    index/
    cache/

  docs/
    architecture/
    adr/
    superpowers/
      specs/

  src/
    snapgit/
      __init__.py
      main.py

      api/
        app.py
        deps.py
        routes/
          health.py
          ingest.py
          screenshots.py
          search.py
          actions.py

      ingest/
        service.py
        validators.py
        dedup.py
        repository.py

      pipeline/
        events.py
        jobs.py
        orchestrator.py
        retry.py
        dead_letter.py

      ocr/
        service.py
        adapters/
          paddle.py
          surya.py
        postprocess.py

      vision/
        service.py
        adapters/
          qwen_vl.py
        prompts/
          classify.txt
          extract_entities.txt

      enrichment/
        service.py
        providers/
          github_local.py
          hf_local.py
        cache.py

      index/
        service.py
        embedding.py
        vector_store.py
        text_index.py

      search/
        service.py
        ranking.py
        filters.py

      actions/
        service.py
        rules.py
        suggestions.py

      storage/
        files.py
        metadata_db.py
        migrations/

      domain/
        models.py
        schemas.py
        enums.py

      common/
        settings.py
        logging.py
        time.py
        errors.py
        utils.py

      observability/
        metrics.py
        tracing.py

  tests/
    unit/
    integration/
    e2e/
    fixtures/
      screenshots/
      ocr_outputs/
      vision_outputs/
```

## 6. Data and Queue Model

Persistence stack:
- **SQLAlchemy 2.x** for data access and domain mapping.
- **Alembic** for migrations from day one.
- **SQLite** as initial database backend.

Queue model:
- Queue is implemented as regular relational tables, not a SQLite-only queue library.
- This keeps queue and metadata in one data model and simplifies migration later.

Primary tables:
- `assets`: screenshot metadata and lifecycle state.
- `jobs`: pipeline jobs.
- `asset_ocr_blocks`: OCR output blocks and bounding boxes.
- `asset_entities`: extracted entities from OCR/VLM stages.
- `asset_actions`: generated action suggestions.
- `asset_embeddings`: embedding vectors and model version metadata.
- `pipeline_events`: append-only stage events for debugging and replay visibility.

Minimal `jobs` columns:
- `id`
- `asset_id`
- `job_type`
- `status` (`queued`, `processing`, `retry`, `done`, `failed`)
- `priority`
- `available_at`
- `locked_at`
- `lease_until`
- `attempts`
- `max_attempts`
- `last_error`
- `created_at`
- `updated_at`

Recommended SQLite runtime settings:
- WAL mode enabled.
- Busy timeout configured.
- Indexes for job fetch path (for example by status and availability).

## 7. Processing Semantics

Execution assumptions:
- One ingest path, one sequential AI worker in Phase 1.
- Jobs can accumulate; processing remains one-by-one by design.

Reliability contract:
- Pipeline is **at-least-once** at the job level.
- Each stage is **idempotent**.
- Any process interruption must be recoverable without critical system failure.

Restart behavior:
- Worker uses lease semantics (`lease_until`).
- If worker crashes, stale jobs return to queue after lease expiry.
- On restart, worker resumes from valid checkpoint state.

Idempotency strategy:
- Stage outputs are written with deterministic keys (`asset_id`, `stage`, `stage_version`).
- Writes use transactional upsert semantics.
- Before running a stage, worker checks if current-version result already exists.
- Existing valid result short-circuits duplicate recomputation.

Modes of execution:
- `resume` (default): execute only missing stages.
- `replay-stage`: re-run exactly one stage with current config/version.
- `replay-all`: full pipeline recompute with a new run identifier.

## 8. API Surface (Phase 1)

Planned endpoint categories:
- Health and diagnostics.
- Screenshot ingest.
- Asset listing and detail retrieval.
- Search (keyword, semantic, filtered).
- Action suggestions retrieval and confirmation flow.

API should remain thin:
- orchestration in API layer,
- business logic in module services,
- persistence via storage/domain abstractions.

## 9. Testing Strategy

- Unit tests per module service with adapter fakes.
- Integration tests per module boundary (for example ingest->pipeline, pipeline->ocr).
- E2E tests for complete flow from ingest to searchable asset.
- Fixture-driven regression tests based on real screenshot corpus.

Verification priorities:
- Idempotent re-run behavior.
- Resume after forced interruption.
- Deterministic extraction for stable fixtures.
- Search quality smoke checks on known examples.

## 10. Observability and Operability

- Structured JSON logs per pipeline stage.
- Correlation by `asset_id`, `job_id`, `pipeline_run_id`.
- Metrics for queue depth, stage latency, success/failure rates, retries.
- Dead-letter visibility for exhausted jobs.

## 11. Evolution Path

Phase 1 starts with SQLite and local-only execution.
When needed, migration path is:
1. Keep SQLAlchemy models and Alembic history.
2. Switch DSN from SQLite to PostgreSQL/MySQL.
3. Re-tune transaction/locking assumptions.
4. Scale worker count if model throughput justifies it.

This preserves architecture and module boundaries while changing only operational backend details.

## 12. Final Design Decisions

- Local-only backend on Linux for initial phase.
- Single-user architecture.
- Near real-time ingestion with sequential processing.
- Modular monolith with strict component boundaries.
- SQLite + SQLAlchemy 2.x + Alembic.
- Queue as relational tables in same DB as metadata.
- Pipeline is interruption-safe and idempotent with resume/replay modes.
