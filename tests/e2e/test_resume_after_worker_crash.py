from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from snapgit.domain.models import Asset, Blob, Job, PipelineRun
from snapgit.index.service import run_index_stage
from snapgit.ocr.service import run_ocr_stage
import snapgit.pipeline.queue as queue_module
from snapgit.pipeline.queue import recover_expired_jobs
from snapgit.search.service import search_asset_ids
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_resume_after_crash_recovers_expired_lease_and_completes(tmp_path, monkeypatch):
    engine = _upgraded_engine(tmp_path)
    now = datetime.now(timezone.utc)
    database_url = str(engine.url)

    monkeypatch.setattr(
        queue_module, "Settings", lambda: type("SettingsOverride", (), {"database_url": database_url})()
    )

    with Session(engine) as session:
        blob = Blob(
            sha256="1" * 64,
            storage_path="/tmp/crash-recovery.png",
            mime_type="image/png",
            size_bytes=256,
        )
        session.add(blob)
        session.flush()

        asset = Asset(blob_id=blob.id, source_type="filesystem", state="ingested")
        session.add(asset)
        session.flush()

        pipeline_run = PipelineRun(
            asset_id=asset.id,
            trigger_type="ingest",
            pipeline_version="baseline-v1",
            config_hash="2" * 64,
            status="running",
        )
        session.add(pipeline_run)
        session.flush()

        job = Job(
            job_type="run_pipeline",
            target_type="pipeline_run",
            target_id=pipeline_run.id,
            payload_json=None,
            status="processing",
            priority=0,
            available_at=now - timedelta(minutes=30),
            locked_at=now - timedelta(minutes=10),
            lease_until=now - timedelta(seconds=1),
            worker_id="worker-crashed",
            attempts=1,
            max_attempts=3,
            last_error=None,
        )
        session.add(job)
        session.commit()

        asset_id = asset.id
        pipeline_run_id = pipeline_run.id
        job_id = job.id

    assert search_asset_ids(engine, "parking") == []

    recovered_count = recover_expired_jobs()
    assert recovered_count == 1

    with Session(engine) as session:
        recovered_job = session.get(Job, job_id)
        assert recovered_job is not None
        assert recovered_job.status == "retry"
        assert _to_utc(recovered_job.available_at) >= now
        assert recovered_job.locked_at is None
        assert recovered_job.lease_until is None
        assert recovered_job.worker_id is None

    run_ocr_stage(
        engine,
        pipeline_run_id=pipeline_run_id,
        asset_id=asset_id,
        ocr_text="Receipt includes parking and lunch",
    )
    run_index_stage(
        engine,
        pipeline_run_id=pipeline_run_id,
        asset_id=asset_id,
        title="Trip expenses",
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE jobs
                SET status = 'done', lease_until = NULL, locked_at = NULL, worker_id = NULL
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        connection.execute(
            text(
                """
                UPDATE pipeline_runs
                SET status = 'completed', finished_at = CURRENT_TIMESTAMP
                WHERE id = :pipeline_run_id
                """
            ),
            {"pipeline_run_id": pipeline_run_id},
        )

    with Session(engine) as session:
        refreshed_asset = session.get(Asset, asset_id)
        refreshed_run = session.get(PipelineRun, pipeline_run_id)
        refreshed_job = session.get(Job, job_id)

        assert refreshed_asset is not None
        assert refreshed_asset.state == "searchable"

        assert refreshed_run is not None
        assert refreshed_run.status == "completed"
        assert refreshed_run.finished_at is not None

        assert refreshed_job is not None
        assert refreshed_job.status == "done"

    with engine.begin() as connection:
        stage_rows = connection.execute(
            text(
                """
                SELECT stage_name, status
                FROM stage_results
                WHERE pipeline_run_id = :pipeline_run_id
                ORDER BY stage_name
                """
            ),
            {"pipeline_run_id": pipeline_run_id},
        ).fetchall()

    assert [(row[0], row[1]) for row in stage_rows] == [
        ("index", "completed"),
        ("ocr", "completed"),
    ]
    assert search_asset_ids(engine, "parking") == [asset_id]


def _upgraded_engine(tmp_path):
    database_path = tmp_path / "resume_after_worker_crash.db"
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    return create_engine_with_sqlite_pragmas(f"sqlite:///{database_path}")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
