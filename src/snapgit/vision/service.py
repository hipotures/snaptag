from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine


def run_vision_best_effort(
    engine: Engine,
    *,
    pipeline_run_id: int,
    asset_id: int,
    vision_fn: Callable[[], object] | None = None,
    stage_version: str = "baseline-v1",
) -> tuple[bool, str | None]:
    with engine.begin() as connection:
        _validate_pipeline_run_asset(connection, pipeline_run_id, asset_id)

        try:
            if vision_fn is not None:
                vision_fn()
        except Exception as exc:
            error_text = str(exc)
            _write_stage_result(
                connection,
                pipeline_run_id=pipeline_run_id,
                asset_id=asset_id,
                stage_version=stage_version,
                status="failed",
                error=error_text,
            )
            return False, error_text

        _write_stage_result(
            connection,
            pipeline_run_id=pipeline_run_id,
            asset_id=asset_id,
            stage_version=stage_version,
            status="completed",
            error=None,
        )
        return True, None


def _write_stage_result(
    connection,
    *,
    pipeline_run_id: int,
    asset_id: int,
    stage_version: str,
    status: str,
    error: str | None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO stage_results (
                pipeline_run_id,
                asset_id,
                stage_name,
                stage_version,
                status,
                error,
                finished_at
            )
            VALUES (
                :pipeline_run_id,
                :asset_id,
                'vision',
                :stage_version,
                :status,
                :error,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT(pipeline_run_id, stage_name) DO UPDATE SET
                stage_version = excluded.stage_version,
                status = excluded.status,
                error = excluded.error,
                finished_at = excluded.finished_at
            """
        ),
        {
            "pipeline_run_id": pipeline_run_id,
            "asset_id": asset_id,
            "stage_version": stage_version,
            "status": status,
            "error": error,
        },
    )


def _validate_pipeline_run_asset(connection, pipeline_run_id: int, asset_id: int) -> None:
    run_exists = connection.execute(
        text(
            """
            SELECT 1
            FROM pipeline_runs
            WHERE id = :pipeline_run_id AND asset_id = :asset_id
            """
        ),
        {"pipeline_run_id": pipeline_run_id, "asset_id": asset_id},
    ).fetchone()

    if run_exists is None:
        raise ValueError(
            f"pipeline_run_id={pipeline_run_id} does not belong to asset_id={asset_id}"
        )
