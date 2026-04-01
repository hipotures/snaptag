from __future__ import annotations

from datetime import datetime, timezone


def is_lease_expired(
    lease_until: datetime | None, *, now: datetime | None = None
) -> bool:
    if lease_until is None:
        return False

    reference_time = now or datetime.now(timezone.utc)
    if lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    return lease_until < reference_time
