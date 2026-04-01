from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from snapgit.common.settings import Settings
from snapgit.index.fts_projection import build_fts_document
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def reindex_asset(
    asset_id: int,
    *,
    database_url: str | None = None,
    title: str | None = None,
) -> None:
    settings = Settings()
    engine = create_engine_with_sqlite_pragmas(database_url or settings.database_url)
    reindex_asset_with_engine(engine, asset_id=asset_id, title=title)


def reindex_asset_with_engine(
    engine: Engine,
    *,
    asset_id: int,
    title: str | None = None,
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
