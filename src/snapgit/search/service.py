from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def fts_query_sql() -> str:
    return "SELECT rowid FROM search_docs_fts WHERE search_docs_fts MATCH :query ORDER BY rowid"


def search_asset_ids(engine: Engine, query: str) -> list[int]:
    with engine.begin() as connection:
        rows = connection.execute(text(fts_query_sql()), {"query": query}).fetchall()
    return [int(row[0]) for row in rows]
