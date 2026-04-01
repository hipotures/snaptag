from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from snapgit.domain.models import Asset, Blob, PipelineRun
import snapgit.pipeline.orchestrator as orchestrator_module
from snapgit.pipeline.orchestrator import replay_all
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_replay_all_creates_new_pipeline_run(tmp_path, monkeypatch):
    engine = _upgraded_engine(tmp_path)
    database_url = str(engine.url)

    monkeypatch.setattr(
        orchestrator_module,
        "Settings",
        lambda: type("SettingsOverride", (), {"database_url": database_url})(),
    )

    with Session(engine) as session:
        blob = Blob(
            sha256="3" * 64,
            storage_path="/tmp/replay-all.png",
            mime_type="image/png",
            size_bytes=1024,
        )
        session.add(blob)
        session.flush()

        asset = Asset(blob_id=blob.id, source_type="filesystem", state="searchable")
        session.add(asset)
        session.flush()

        initial_run = PipelineRun(
            asset_id=asset.id,
            trigger_type="ingest",
            pipeline_version="baseline-v1",
            config_hash="4" * 64,
            status="completed",
        )
        session.add(initial_run)
        session.commit()

        asset_id = asset.id
        initial_run_id = initial_run.id

    new_run_id = replay_all(asset_id=asset_id)

    assert isinstance(new_run_id, int)
    assert new_run_id != initial_run_id

    with Session(engine) as session:
        runs = session.scalars(
            select(PipelineRun).where(PipelineRun.asset_id == asset_id).order_by(PipelineRun.id)
        ).all()

    assert [run.id for run in runs] == [initial_run_id, new_run_id]

    replay_run = runs[-1]
    assert replay_run.trigger_type == "replay_all"
    assert replay_run.pipeline_version == "baseline-v1"
    assert replay_run.config_hash == "4" * 64
    assert replay_run.status == "running"
    assert replay_run.finished_at is None


def _upgraded_engine(tmp_path):
    database_path = tmp_path / "replay_stage_and_replay_all.db"
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    return create_engine_with_sqlite_pragmas(f"sqlite:///{database_path}")
