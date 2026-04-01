from sqlalchemy import text

from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_sqlite_foreign_keys_are_enabled():
    engine = create_engine_with_sqlite_pragmas("sqlite:///:memory:")
    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar_one()
        assert fk == 1
