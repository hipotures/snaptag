from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_initial_tables_exist_after_alembic_upgrade(tmp_path):
    database_path = tmp_path / "schema.db"
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"

    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine_with_sqlite_pragmas(f"sqlite:///{database_path}")
    insp = inspect(engine)
    names = set(insp.get_table_names())

    assert {"blobs", "assets", "pipeline_runs", "stage_results", "jobs"} <= names

    blob_indexes = insp.get_indexes("blobs")
    assert all(index["name"] != "ix_blobs_sha256" for index in blob_indexes)
