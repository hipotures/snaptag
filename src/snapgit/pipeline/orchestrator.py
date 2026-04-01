TERMINAL_RUN_STATUSES = {"completed", "failed"}


def _is_maintenance_only_run(run: dict) -> bool:
    if run.get("maintenance_only") is True:
        return True

    trigger_type = run.get("trigger_type")
    if isinstance(trigger_type, str) and trigger_type.lower() == "maintenance":
        return True

    stage_name = run.get("stage_name") or run.get("stage")
    return isinstance(stage_name, str) and stage_name.lower() == "maintenance"


def latest_effective_run_status(runs: list[dict]) -> str | None:
    for run in reversed(runs):
        status = run.get("status")
        if not isinstance(status, str):
            continue

        normalized_status = status.lower()
        if normalized_status not in TERMINAL_RUN_STATUSES:
            continue

        if _is_maintenance_only_run(run):
            continue

        return normalized_status

    return None
