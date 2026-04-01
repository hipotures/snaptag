from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def run_ocr_stage(
    engine: Engine,
    *,
    pipeline_run_id: int,
    asset_id: int,
    ocr_text: str,
    stage_version: str = "baseline-v1",
) -> None:
    with engine.begin() as connection:
        _validate_pipeline_run_asset(connection, pipeline_run_id, asset_id)

        connection.execute(
            text(
                """
                INSERT INTO asset_ocr_texts (asset_id, ocr_text)
                VALUES (:asset_id, :ocr_text)
                ON CONFLICT(asset_id) DO UPDATE SET
                    ocr_text = excluded.ocr_text
                """
            ),
            {"asset_id": asset_id, "ocr_text": ocr_text},
        )
        connection.execute(
            text(
                """
                INSERT INTO stage_results (
                    pipeline_run_id,
                    asset_id,
                    stage_name,
                    stage_version,
                    status,
                    artifact_ref,
                    finished_at
                )
                VALUES (
                    :pipeline_run_id,
                    :asset_id,
                    'ocr',
                    :stage_version,
                    'completed',
                    :artifact_ref,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT(pipeline_run_id, stage_name) DO UPDATE SET
                    stage_version = excluded.stage_version,
                    status = excluded.status,
                    artifact_ref = excluded.artifact_ref,
                    error = NULL,
                    finished_at = excluded.finished_at
                """
            ),
            {
                "pipeline_run_id": pipeline_run_id,
                "asset_id": asset_id,
                "stage_version": stage_version,
                "artifact_ref": f"asset_ocr_texts:{asset_id}",
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
