from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from snapgit.domain.models import Asset, Blob, PipelineRun
from snapgit.enrichment.service import run_enrichment_best_effort
from snapgit.index.service import run_index_stage
from snapgit.ocr.service import run_ocr_stage
from snapgit.search.service import search_asset_ids
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_enrichment_failure_does_not_block_searchable_state(tmp_path):
    engine = _upgraded_engine(tmp_path)

    with Session(engine) as session:
        blob = Blob(
            sha256="9" * 64,
            storage_path="/tmp/optional-stage.png",
            mime_type="image/png",
            size_bytes=1234,
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
            config_hash="1" * 64,
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
        ocr_text="Receipt includes lunch and parking charges",
    )
    run_index_stage(
        engine,
        pipeline_run_id=pipeline_run_id,
        asset_id=asset_id,
        title="Expense notes",
    )

    with engine.begin() as connection:
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

    success, error = run_enrichment_best_effort(
        engine,
        pipeline_run_id=pipeline_run_id,
        asset_id=asset_id,
        enrich_fn=_force_enrichment_failure,
    )

    assert success is False
    assert error is not None
    assert "forced enrichment failure" in error

    with Session(engine) as session:
        refreshed_asset = session.get(Asset, asset_id)
        refreshed_run = session.get(PipelineRun, pipeline_run_id)
        assert refreshed_asset is not None
        assert refreshed_asset.state == "searchable"
        assert refreshed_run is not None
        assert refreshed_run.status == "completed"

    with engine.begin() as connection:
        enrichment_row = connection.execute(
            text(
                """
                SELECT status, error
                FROM stage_results
                WHERE pipeline_run_id = :pipeline_run_id
                  AND stage_name = 'enrichment'
                """
            ),
            {"pipeline_run_id": pipeline_run_id},
        ).fetchone()

    assert enrichment_row is not None
    assert enrichment_row[0] == "failed"
    assert "forced enrichment failure" in enrichment_row[1]
    assert search_asset_ids(engine, "lunch") == [asset_id]


def _force_enrichment_failure() -> None:
    raise RuntimeError("forced enrichment failure")


def _upgraded_engine(tmp_path):
    database_path = tmp_path / "optional_stages_non_blocking.db"
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"

    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")

    return create_engine_with_sqlite_pragmas(f"sqlite:///{database_path}")
