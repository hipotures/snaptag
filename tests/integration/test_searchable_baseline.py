from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from snapgit.domain.models import Asset, Blob, PipelineRun
from snapgit.index.service import run_index_stage
from snapgit.ocr.service import run_ocr_stage
from snapgit.search.service import search_asset_ids
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_asset_becomes_searchable_after_ocr_and_index_only(tmp_path):
    engine = _upgraded_engine(tmp_path)

    with Session(engine) as session:
        blob = Blob(
            sha256="f" * 64,
            storage_path="/tmp/screenshot.png",
            mime_type="image/png",
            size_bytes=1024,
        )
        session.add(blob)
        session.flush()

        asset = Asset(
            blob_id=blob.id,
            source_type="filesystem",
            state="ingested",
        )
        session.add(asset)
        session.flush()

        pipeline_run = PipelineRun(
            asset_id=asset.id,
            trigger_type="ingest",
            pipeline_version="baseline-v1",
            config_hash="c" * 64,
            status="running",
        )
        session.add(pipeline_run)
        session.commit()

        asset_id = asset.id
        pipeline_run_id = pipeline_run.id

    run_ocr_stage(
        engine,
        pipeline_run_id=pipeline_run_id,
        asset_id=asset_id,
        ocr_text="Expense report includes lunch and travel reimbursement",
    )
    run_index_stage(
        engine,
        pipeline_run_id=pipeline_run_id,
        asset_id=asset_id,
        title="April notes",
    )

    with Session(engine) as session:
        refreshed_asset = session.get(Asset, asset_id)
        assert refreshed_asset is not None
        assert refreshed_asset.state == "searchable"

    with engine.begin() as connection:
        stage_rows = connection.execute(
            text(
                """
                SELECT stage_name
                FROM stage_results
                WHERE pipeline_run_id = :pipeline_run_id
                ORDER BY stage_name
                """
            ),
            {"pipeline_run_id": pipeline_run_id},
        ).fetchall()

    assert [row[0] for row in stage_rows] == ["index", "ocr"]

    assert search_asset_ids(engine, "lunch") == [asset_id]
    assert search_asset_ids(engine, "April") == [asset_id]


def _upgraded_engine(tmp_path):
    database_path = tmp_path / "searchable_baseline.db"
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"

    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")

    return create_engine_with_sqlite_pragmas(f"sqlite:///{database_path}")
