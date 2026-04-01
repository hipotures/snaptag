from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from snapgit.domain.models import Asset, Blob, PipelineRun
from snapgit.index.service import run_index_stage
from snapgit.ocr.service import run_ocr_stage
from snapgit.search.service import search_asset_ids
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_search_returns_empty_before_first_index_build(tmp_path):
    engine = _upgraded_engine(tmp_path)

    assert search_asset_ids(engine, "anything") == []


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


def test_ocr_stage_rejects_pipeline_run_asset_mismatch(tmp_path):
    engine = _upgraded_engine(tmp_path)

    with Session(engine) as session:
        blob = Blob(
            sha256="a" * 64,
            storage_path="/tmp/mismatch-ocr.png",
            mime_type="image/png",
            size_bytes=2048,
        )
        session.add(blob)
        session.flush()

        asset_1 = Asset(blob_id=blob.id, source_type="filesystem", state="ingested")
        asset_2 = Asset(blob_id=blob.id, source_type="filesystem", state="ingested")
        session.add_all([asset_1, asset_2])
        session.flush()

        pipeline_run = PipelineRun(
            asset_id=asset_1.id,
            trigger_type="ingest",
            pipeline_version="baseline-v1",
            config_hash="d" * 64,
            status="running",
        )
        session.add(pipeline_run)
        session.commit()

        pipeline_run_id = pipeline_run.id
        mismatched_asset_id = asset_2.id

    with pytest.raises(ValueError, match="does not belong to asset_id"):
        run_ocr_stage(
            engine,
            pipeline_run_id=pipeline_run_id,
            asset_id=mismatched_asset_id,
            ocr_text="Should not be written",
        )

    with engine.begin() as connection:
        stage_results_count = connection.execute(
            text("SELECT COUNT(*) FROM stage_results WHERE pipeline_run_id = :run_id"),
            {"run_id": pipeline_run_id},
        ).scalar_one()
        ocr_rows_count = connection.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'asset_ocr_texts'"),
        ).scalar_one()

    assert stage_results_count == 0
    assert ocr_rows_count == 0


def test_index_stage_rejects_pipeline_run_asset_mismatch(tmp_path):
    engine = _upgraded_engine(tmp_path)

    with Session(engine) as session:
        blob = Blob(
            sha256="b" * 64,
            storage_path="/tmp/mismatch-index.png",
            mime_type="image/png",
            size_bytes=4096,
        )
        session.add(blob)
        session.flush()

        asset_1 = Asset(blob_id=blob.id, source_type="filesystem", state="ingested")
        asset_2 = Asset(blob_id=blob.id, source_type="filesystem", state="ingested")
        session.add_all([asset_1, asset_2])
        session.flush()

        pipeline_run = PipelineRun(
            asset_id=asset_1.id,
            trigger_type="ingest",
            pipeline_version="baseline-v1",
            config_hash="e" * 64,
            status="running",
        )
        session.add(pipeline_run)
        session.commit()

        pipeline_run_id = pipeline_run.id
        run_asset_id = asset_1.id
        mismatched_asset_id = asset_2.id

    run_ocr_stage(
        engine,
        pipeline_run_id=pipeline_run_id,
        asset_id=run_asset_id,
        ocr_text="OCR for asset one",
    )

    with pytest.raises(ValueError, match="does not belong to asset_id"):
        run_index_stage(
            engine,
            pipeline_run_id=pipeline_run_id,
            asset_id=mismatched_asset_id,
            title="Should fail",
        )

    with Session(engine) as session:
        untouched_asset = session.get(Asset, mismatched_asset_id)
        assert untouched_asset is not None
        assert untouched_asset.state == "ingested"

    with engine.begin() as connection:
        stage_rows = connection.execute(
            text(
                """
                SELECT stage_name
                FROM stage_results
                WHERE pipeline_run_id = :run_id
                ORDER BY stage_name
                """
            ),
            {"run_id": pipeline_run_id},
        ).fetchall()

    assert [row[0] for row in stage_rows] == ["ocr"]


def _upgraded_engine(tmp_path):
    database_path = tmp_path / "searchable_baseline.db"
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"

    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")

    return create_engine_with_sqlite_pragmas(f"sqlite:///{database_path}")
