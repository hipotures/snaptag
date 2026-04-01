from datetime import datetime, timezone

TERMINAL_RUN_STATUSES = {"completed", "failed"}
RUN_TIMESTAMP_FIELDS = ("finished_at", "started_at", "created_at", "updated_at")
RUN_ID_FIELDS = ("id", "run_id", "pipeline_run_id")


def _is_maintenance_only_run(run: dict) -> bool:
    if run.get("maintenance_only") is True:
        return True

    trigger_type = run.get("trigger_type")
    if isinstance(trigger_type, str) and trigger_type.lower() == "maintenance":
        return True

    stage_name = run.get("stage_name") or run.get("stage")
    return isinstance(stage_name, str) and stage_name.lower() == "maintenance"


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _run_time_key(run: dict) -> datetime:
    for field_name in RUN_TIMESTAMP_FIELDS:
        dt = _coerce_datetime(run.get(field_name))
        if dt is not None:
            return dt
    return datetime.min


def _run_id_key(run: dict) -> int:
    for field_name in RUN_ID_FIELDS:
        raw_value = run.get(field_name)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, str):
            try:
                return int(raw_value)
            except ValueError:
                continue
    return -1


def _run_fingerprint(run: dict) -> tuple[tuple[str, str], ...]:
    return tuple((str(key), str(value)) for key, value in sorted(run.items()))


def _run_sort_key(run: dict) -> tuple[datetime, int, tuple[tuple[str, str], ...]]:
    return (_run_time_key(run), _run_id_key(run), _run_fingerprint(run))


def latest_effective_run_status(runs: list[dict]) -> str | None:
    effective_terminal_runs = []
    for run in runs:
        status = run.get("status")
        if not isinstance(status, str):
            continue

        normalized_status = status.lower()
        if normalized_status not in TERMINAL_RUN_STATUSES:
            continue

        if _is_maintenance_only_run(run):
            continue

        effective_terminal_runs.append((run, normalized_status))

    if not effective_terminal_runs:
        return None

    return max(
        effective_terminal_runs,
        key=lambda entry: _run_sort_key(entry[0]),
    )[1]
