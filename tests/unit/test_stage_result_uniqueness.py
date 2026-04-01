import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from snapgit.domain.models import Asset, Base, Blob, PipelineRun, StageResult
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_stage_result_is_unique_per_pipeline_run_and_stage_name():
    engine = create_engine_with_sqlite_pragmas("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        blob = Blob(
            sha256="a" * 64,
            storage_path="/tmp/a.jpg",
            mime_type="image/jpeg",
            size_bytes=123,
        )
        session.add(blob)
        session.flush()

        asset = Asset(blob_id=blob.id, source_type="upload", state="ingested")
        session.add(asset)
        session.flush()

        run = PipelineRun(
            asset_id=asset.id,
            trigger_type="manual",
            pipeline_version="v1",
            config_hash="b" * 64,
            status="failed",
        )
        session.add(run)
        session.flush()

        first = StageResult(
            pipeline_run_id=run.id,
            asset_id=asset.id,
            stage_name="ingest",
            status="failed",
        )
        session.add(first)
        session.commit()

        duplicate = StageResult(
            pipeline_run_id=run.id,
            asset_id=asset.id,
            stage_name="ingest",
            status="completed",
        )
        session.add(duplicate)

        with pytest.raises(IntegrityError):
            session.commit()
