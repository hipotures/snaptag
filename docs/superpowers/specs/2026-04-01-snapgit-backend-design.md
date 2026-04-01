# SnapGit Backend Design (Phase 1, V2.1)

## 1. Context and Product Direction

SnapGit is a local-first screenshot intelligence backend for one user.
Input is a stream of screenshots (Android source, Linux backend).
The system transforms screenshots into searchable, structured memory with optional action suggestions.

Phase 1 constraints:
- one machine,
- one user,
- one sequential AI worker,
- SQLite as canonical storage,
- interruption-safe processing.

## 2. Goals

- Build a reliable ingest-to-search backend for screenshots.
- Keep modules independently testable with explicit boundaries.
- Guarantee idempotent processing and safe recovery after crash/restart.
- Keep data model migration-friendly (SQLite now, PostgreSQL/MySQL later).
- Optimize for implementation speed without hidden architectural debt.

## 3. Non-Goals (Phase 1)

- Multi-user accounts and tenant isolation.
- Cloud-first architecture.
- Distributed workers or parallel GPU inference.
- Autonomous action execution without confirmation.
- Production-scale internet exposure hardening.

## 4. Architecture Summary

Recommended architecture: **modular monolith + relational queue in SQLite**.

Core modules:
- `ingest`: receive screenshot, validate input, store file, deduplicate.
- `pipeline`: orchestration only (enqueue/dequeue/retry/lease), no model logic.
- `ocr`: OCR and layout extraction.
- `vision`: screenshot classification and entity extraction.
- `enrichment`: optional best-effort enhancement of extracted entities.
- `index`: build search projections (FTS documents) from canonical data.
- `search`: retrieval over FTS + metadata filters.
- `actions`: generate action suggestions only.
- `api`: HTTP surface for ingest, browse, search, and suggestions.
- `storage`: filesystem and relational persistence.
- `observability`: logs and minimal metrics.

## 5. Domain Model (Authoritative Separation)

Four core entities are explicitly separated:

1. `blob`
- Physical file identity (content hash, path, size, mime).
- Immutable once written.

2. `asset`
- Logical screenshot object in the product domain.
- Points to one blob and carries source/capture metadata.
- Stable identity across all reprocessing.

3. `pipeline_run`
- One execution instance of the pipeline for one asset.
- Triggered by ingest, replay-stage, replay-all, manual backfill, or maintenance operations.

4. `job`
- Ephemeral execution unit used by worker scheduling.
- Jobs are execution mechanics, not authoritative pipeline history.

## 6. Canonical vs Derived State

Canonical state (source of truth):
- `blobs`
- `assets`
- `pipeline_runs`
- `stage_results`
- normalized extraction tables (`asset_ocr_blocks`, `asset_entities`, `asset_action_suggestions`)

Derived state (rebuildable projections):
- FTS tables / search projections
- optional embedding artifacts (future)
- cached enrichment responses
- operational metric aggregates

Rule: derived state can be dropped and rebuilt from canonical state.

## 7. Repository Structure

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
        orchestrator.py
        queue.py
        retry.py
        leases.py
        events.py

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
        resolvers/
          github.py
          huggingface.py
        cache.py

      index/
        service.py
        fts_projection.py

      search/
        service.py
        ranking.py
        filters.py

      actions/
        service.py
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

  tests/
    unit/
    integration/
    e2e/
    fixtures/
      screenshots/
      ocr_outputs/
      vision_outputs/
```

## 8. Persistence Stack and SQLite Baseline

- ORM/data layer: **SQLAlchemy 2.x**.
- Migrations: **Alembic** from day one.
- Database backend in Phase 1: **SQLite**.

Required SQLite baseline:
- WAL mode enabled.
- Busy timeout configured.
- Deterministic transaction boundaries.
- Indexes for queue fetch and key lookups.

## 9. Data Model (Phase 1)

### 9.1 Core tables

`blobs`
- `id`
- `sha256` (unique)
- `storage_path`
- `mime_type`
- `size_bytes`
- `created_at`

`assets`
- `id`
- `blob_id`
- `source_type` (`android_upload`, `backfill_folder`, etc.)
- `captured_at`
- `ingested_at`
- `state` (`ingested`, `processing`, `searchable`, `failed`)
- `created_at`
- `updated_at`

`pipeline_runs`
- `id`
- `asset_id`
- `trigger_type` (`ingest`, `replay_stage`, `replay_all`, `backfill`, `manual`)
- `pipeline_version`
- `config_hash`
- `status` (`running`, `completed`, `failed`)
- `started_at`
- `finished_at`

`stage_results`
- `id`
- `pipeline_run_id`
- `asset_id`
- `stage_name` (`ocr`, `vision`, `enrichment`, `index`, `actions`)
- `stage_version`
- `input_hash`
- `output_hash`
- `status` (`running`, `completed`, `failed`, `skipped`)
- `artifact_ref`
- `error`
- `started_at`
- `finished_at`

`jobs`
- `id`
- `job_type`
- `target_type` (`pipeline_run`, `stage_result`)
- `target_id`
- `payload_json`
- `status` (`queued`, `processing`, `retry`, `done`, `failed_terminal`)
- `priority`
- `available_at`
- `lease_until`
- `attempts`
- `max_attempts`
- `last_error`
- `created_at`
- `updated_at`

### 9.2 Stage output tables

`asset_ocr_blocks`
- OCR text blocks and bounding boxes.
- Version-linked through `stage_result_id`.

`asset_entities`
- Normalized entities (`type`, `value`, `confidence`, provenance).
- Version-linked through `stage_result_id`.

`asset_action_suggestions`
- Suggested actions only (`action_type`, `payload_json`, `confidence`, `requires_confirmation`).
- Version-linked through `stage_result_id`.

`pipeline_events`
- Append-only diagnostics.
- Best-effort and may be incomplete.
- Not authoritative system state.

## 10. State Semantics

`asset.state` is a **materialized summary field** for fast API/UI access.
Authoritative progress is derived from latest effective `pipeline_runs` + `stage_results`.

`asset.state` transition intent:
- `ingested`: asset persisted, no active run yet.
- `processing`: active run is executing mandatory stages.
- `searchable`: minimal searchable baseline reached.
- `failed`: latest effective run did not reach searchable baseline.

## 11. Minimal Searchable Baseline

An asset becomes searchable when all are true:
- blob is persisted,
- asset exists,
- OCR stage completed successfully,
- index projection stage completed successfully.

`vision`, `enrichment`, and `actions` are non-gating in Phase 1.
They improve quality but do not block searchability.

## 12. Queue and Pipeline Semantics

Queue role is strictly execution orchestration.
Pipeline truth is represented by `pipeline_runs` and `stage_results`.

Execution model:
- at-least-once at the job level,
- sequential worker,
- stage-level idempotency.

Lease/recovery:
- worker acquires one job,
- sets `processing` + `lease_until`,
- crash leaves job recoverable after lease expiration,
- restart continues from canonical state.

`pipeline_run.status` semantics:
- `completed`: searchable baseline achieved (optional stages may fail).
- `failed`: searchable baseline not achieved after retry policy.

## 13. Stage Result Semantics

`stage_results` are stored **per pipeline_run**.

Implications:
- replay-all may create semantically identical stage outputs in a new run history,
- this is valid and preserves run auditability.

Idempotency key shape:
- `(asset_id, stage_name, stage_version, input_hash)`.

Behavior:
- if a compatible prior result exists, stage may be marked `skipped` with reused `artifact_ref`.
- otherwise, stage computes and writes a new result for the current run.

## 14. Artifact Reference Contract

`artifact_ref` is a stable filesystem reference to raw or heavy stage output under `data/artifacts/`.

Storage rule:
- normalized queryable outputs go to relational tables,
- large raw model payloads go to artifact files,
- `artifact_ref` points to artifact path (relative to repository root or storage root).

## 15. Dedup Strategy

Phase 1 dedup is binary and deterministic:
- dedup key = file content `sha256` in `blobs`.

Behavior on duplicate upload:
- existing blob is reused,
- new asset may still be created if capture context is different.

Perceptual dedup is explicitly out of scope for Phase 1.

## 16. Search Strategy (Phase 1)

Default search stack: **SQLite + FTS5 + metadata filters**.

No external vector database in Phase 1.
No mandatory embedding pipeline in Phase 1.

FTS document projection combines:
- OCR normalized text,
- selected entity values,
- concise summary/title fields,
- optional aliases.

Ranking:
- BM25 from FTS5,
- optional metadata boosts in ranking layer.

Future vector search is additive and must remain derived/rebuildable.

## 17. Enrichment and Actions Scope

Enrichment behavior:
- optional,
- non-blocking,
- best-effort,
- failure does not block base searchability.

Actions behavior in Phase 1:
- suggestion generation only,
- no autonomous external side effects,
- confirmation-required model supported via flag.

## 18. Versioning Rules

`stage_version` must be deterministic and auditable.
Recommended composition:
- `model_version`
- `prompt_version`
- `code_version`
- `config_hash`

Any change in these inputs produces a new effective stage version.

## 19. Failure Classes and Retry Policy

Failure classes:
- transient I/O (retry)
- model runtime transient (retry)
- deterministic unsupported input (no retry)
- corrupted payload (no retry)
- enrichment timeout/error (retry-limited, non-blocking)
- index projection failure (retry)

Retry policy:
- bounded retries with backoff,
- after max attempts -> `failed_terminal`,
- manual replay entry points remain available.

## 20. Minimal Stage Contract

Mandatory stages (gating):
- `ocr`: input blob path -> canonical `asset_ocr_blocks` + stage result.
- `index`: input normalized extraction -> derived FTS projection + stage result.

Optional stages (non-gating):
- `vision`: classification/entities enrichment of canonical extraction data.
- `enrichment`: resolver-based metadata enhancement.
- `actions`: generate suggestions for downstream confirmation.

Each stage defines:
- declared inputs,
- canonical writes,
- derived writes,
- retryable error classes,
- gating flag.

## 21. Rebuild Operations

Expected maintenance operations:
- `reindex(asset_id)`
- `reindex_all()`
- `rebuild_fts_from_canonical()`
- `recompute_actions(asset_id, stage_version)`

All rebuild operations must preserve canonical state and only rewrite derived projections.

## 22. Filesystem Layout Guarantees

- Blob paths are immutable once persisted.
- Artifact paths are deterministic by asset/stage/run identity.
- Deleting derived index artifacts is safe if rebuild operations exist.
- Deleting canonical blob/artifact files is never part of normal pipeline execution.

## 23. Backfill Semantics

Backfill ingestion uses `trigger_type=backfill`.

Backfill rules:
- file mtime may map to `captured_at` when explicit capture timestamp is unavailable,
- duplicate blobs reuse existing blob records,
- a new asset may still be created for each capture event context.

## 24. Domain Invariants

1. `asset_id` is stable across all pipeline runs.
2. Jobs may execute more than once; stage writes must be idempotent.
3. `pipeline_events` are diagnostic only.
4. Search projections are derived and rebuildable.
5. Worker crash must never permanently block queue progress.
6. Enrichment failure must not block base asset retrieval.
7. Asset searchability depends on mandatory baseline stages only.

## 25. Testing Strategy

- Unit tests per module service and adapter boundaries.
- Integration tests for ingest -> pipeline -> stage transitions.
- E2E tests for ingest to searchable asset.
- Crash/restart tests for lease recovery and resume.
- Replay tests validating deterministic reprocessing semantics.
- Fixture-based regression set from real screenshots.

## 26. Evolution Path

Near-term evolution order:
1. Stabilize canonical schema and replay behavior.
2. Add richer ranking and entity normalization.
3. Add optional semantic retrieval only if FTS recall is insufficient.
4. If needed, migrate DB backend to PostgreSQL/MySQL via SQLAlchemy/Alembic.

## 27. Final Decisions (Accepted)

- Local-only Linux backend in Phase 1.
- Single-user architecture.
- Sequential processing with one worker.
- SQLite + SQLAlchemy 2.x + Alembic.
- Relational queue in same DB as metadata.
- Explicit separation: blob, asset, pipeline_run, stage_result, job.
- Idempotent, interruption-safe pipeline with resume/replay.
- Search starts with SQLite FTS5 and metadata filters.
