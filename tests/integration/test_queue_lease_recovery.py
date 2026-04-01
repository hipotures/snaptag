from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from snapgit.domain.models import Job
from snapgit.pipeline.leases import is_lease_expired
from snapgit.pipeline.queue import recover_expired_jobs
from snapgit.pipeline.retry import next_retry_attempt
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def test_is_lease_expired_detects_expired_and_active_leases():
    now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    assert is_lease_expired(now - timedelta(seconds=1), now=now) is True
    assert is_lease_expired(now + timedelta(seconds=1), now=now) is False


def test_next_retry_attempt_transitions_to_terminal_at_max_attempts():
    assert next_retry_attempt(attempts=1, max_attempts=3) == "retry"
    assert next_retry_attempt(attempts=2, max_attempts=3) == "retry"
    assert next_retry_attempt(attempts=3, max_attempts=3) == "failed_terminal"


def test_expired_processing_job_returns_to_retry_queue(tmp_path):
    engine = _upgraded_engine(tmp_path)
    now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    lease_until = now - timedelta(minutes=5)

    with Session(engine) as session:
        job = Job(
            job_type="ingest",
            target_type="asset",
            target_id=1,
            payload_json=None,
            status="processing",
            priority=0,
            available_at=now - timedelta(hours=1),
            locked_at=now - timedelta(minutes=10),
            lease_until=lease_until,
            worker_id="worker-1",
            attempts=1,
            max_attempts=3,
            last_error=None,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    recovered_count = recover_expired_jobs(engine, now=now)
    assert recovered_count == 1

    with Session(engine) as session:
        recovered_job = session.get(Job, job_id)
        assert recovered_job is not None
        assert recovered_job.status == "retry"
        assert _to_utc(recovered_job.available_at) == now
        assert recovered_job.locked_at is None
        assert recovered_job.lease_until is None
        assert recovered_job.worker_id is None


def test_expired_processing_job_becomes_failed_terminal_when_attempts_reached(tmp_path):
    engine = _upgraded_engine(tmp_path)
    now = datetime(2026, 4, 1, 13, 0, tzinfo=timezone.utc)

    with Session(engine) as session:
        job = Job(
            job_type="ingest",
            target_type="asset",
            target_id=2,
            payload_json=None,
            status="processing",
            priority=0,
            available_at=now - timedelta(hours=1),
            locked_at=now - timedelta(minutes=10),
            lease_until=now - timedelta(seconds=1),
            worker_id="worker-2",
            attempts=3,
            max_attempts=3,
            last_error=None,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    recovered_count = recover_expired_jobs(engine, now=now)
    assert recovered_count == 1

    with Session(engine) as session:
        recovered_job = session.get(Job, job_id)
        assert recovered_job is not None
        assert recovered_job.status == "failed_terminal"
        assert _to_utc(recovered_job.available_at) == now
        assert recovered_job.locked_at is None
        assert recovered_job.lease_until is None
        assert recovered_job.worker_id is None


def _upgraded_engine(tmp_path):
    database_path = tmp_path / "queue_lease_recovery.db"
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
    return create_engine_with_sqlite_pragmas(f"sqlite:///{database_path}")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
