from sqlalchemy import inspect

from snapgit.domain.models import Base
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_initial_tables_exist():
    engine = create_engine_with_sqlite_pragmas("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    names = set(insp.get_table_names())
    assert {"blobs", "assets", "pipeline_runs", "stage_results", "jobs"} <= names
