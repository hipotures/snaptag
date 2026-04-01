from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from snapgit.domain.models import Job
from snapgit.pipeline.leases import is_lease_expired
from snapgit.pipeline.retry import next_retry_attempt


def recover_expired_jobs(engine: Engine, *, now: datetime | None = None) -> int:
    reference_time = now or datetime.now(timezone.utc)
    recovered_count = 0

    with Session(engine) as session:
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
