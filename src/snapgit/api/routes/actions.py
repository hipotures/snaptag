from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from snapgit.api.db import build_engine
from snapgit.domain.models import Asset, PipelineRun
from snapgit.index.service import run_index_stage

router = APIRouter()
DEFAULT_PIPELINE_VERSION = "baseline-v1"
DEFAULT_CONFIG_HASH = "0" * 64


class ReindexRequest(BaseModel):
    asset_id: int
    title: str | None = None


@router.post("/actions/reindex")
def reindex(request: ReindexRequest) -> dict[str, int | str]:
    engine = build_engine()
    pipeline_run_id: int | None = None
    try:
        with Session(engine) as session:
            asset = session.get(Asset, request.asset_id)
            if asset is None:
                raise HTTPException(status_code=404, detail="asset not found")

            run = PipelineRun(
                asset_id=asset.id,
                trigger_type="maintenance",
                pipeline_version=DEFAULT_PIPELINE_VERSION,
                config_hash=DEFAULT_CONFIG_HASH,
                status="running",
            )
            session.add(run)
            session.commit()
            pipeline_run_id = run.id

        run_index_stage(
            engine,
            pipeline_run_id=pipeline_run_id,
            asset_id=request.asset_id,
            title=request.title,
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
        return {"status": "completed", "asset_id": request.asset_id, "pipeline_run_id": pipeline_run_id}
    except HTTPException:
        raise
    except Exception as exc:
        if pipeline_run_id is not None:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE pipeline_runs
                        SET status = 'failed', finished_at = CURRENT_TIMESTAMP
                        WHERE id = :pipeline_run_id
                        """
                    ),
                    {"pipeline_run_id": pipeline_run_id},
                )
        raise HTTPException(status_code=500, detail=f"reindex failed: {exc}") from exc
