from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from snapgit.common.settings import Settings
from snapgit.domain.models import Job
from snapgit.pipeline.leases import is_lease_expired
from snapgit.pipeline.retry import next_retry_attempt
from snapgit.storage.metadata_db import create_engine_with_sqlite_pragmas


def recover_expired_jobs(
    engine: Engine | None = None, *, now: datetime | None = None
) -> int:
    db_engine = engine or _default_engine()
    reference_time = now or datetime.now(timezone.utc)
    recovered_count = 0

    with Session(db_engine) as session:
        processing_jobs = session.scalars(
            select(Job).where(Job.status == "processing", Job.lease_until.is_not(None))
        ).all()

        for job in processing_jobs:
            if not is_lease_expired(job.lease_until, now=reference_time):
                continue

            job.status = next_retry_attempt(job.attempts, job.max_attempts)
            job.available_at = reference_time
            job.locked_at = None
            job.lease_until = None
            job.worker_id = None
            recovered_count += 1

        if recovered_count > 0:
            session.commit()

    return recovered_count


def _default_engine() -> Engine:
    settings = Settings()
    return create_engine_with_sqlite_pragmas(settings.database_url)
