from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from snapgit.index.fts_projection import build_fts_document


def run_index_stage(
    engine: Engine,
    *,
    pipeline_run_id: int,
    asset_id: int,
    title: str | None = None,
    stage_version: str = "baseline-v1",
) -> None:
    with engine.begin() as connection:
        ocr_row = connection.execute(
            text(
                """
                SELECT ocr_text
                FROM asset_ocr_texts
                WHERE asset_id = :asset_id
                """
            ),
            {"asset_id": asset_id},
        ).fetchone()
        if ocr_row is None:
            raise ValueError(f"OCR output is missing for asset_id={asset_id}")

        document = build_fts_document(ocr_row[0], title=title)

        connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS search_docs_fts
                USING fts5(asset_id UNINDEXED, document)
                """
            )
        )
        connection.execute(
            text("DELETE FROM search_docs_fts WHERE rowid = :asset_id"),
            {"asset_id": asset_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO search_docs_fts (rowid, asset_id, document)
                VALUES (:asset_id, :asset_id, :document)
                """
            ),
            {"asset_id": asset_id, "document": document},
        )

        connection.execute(
            text(
                """
                UPDATE assets
                SET state = 'searchable', updated_at = CURRENT_TIMESTAMP
                WHERE id = :asset_id
                """
            ),
            {"asset_id": asset_id},
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
                    'index',
                    :stage_version,
                    'completed',
                    'search_docs_fts',
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
            },
        )
