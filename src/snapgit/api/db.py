from __future__ import annotations

from sqlalchemy.engine import Engine

from snapgit.common.settings import Settings
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def build_engine() -> Engine:
    settings = Settings()
    return create_engine_with_sqlite_pragmas(settings.database_url)
