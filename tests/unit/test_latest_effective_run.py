from snapgit.pipeline.orchestrator import latest_effective_run_status


def test_latest_effective_run_status_is_order_independent_uses_latest_metadata():
    runs = [
        {
            "status": "completed",
            "stage_name": "ingest",
            "finished_at": "2026-04-01T12:00:00",
        },
        {"status": "running", "stage_name": "ingest"},
        {
            "status": "failed",
            "stage_name": "ingest",
            "finished_at": "2026-04-01T10:00:00",
        },
    ]

    assert latest_effective_run_status(runs) == "completed"


def test_latest_effective_run_status_ignores_non_terminal_and_maintenance_only_runs():
    runs = [
        {"status": "running", "stage_name": "ingest"},
        {"status": "completed", "stage_name": "maintenance"},
        {"status": "failed", "stage_name": "ingest"},
    ]

    assert latest_effective_run_status(runs) == "failed"


def test_latest_effective_run_status_returns_none_when_no_effective_terminal_run():
    runs = [
        {"status": "running", "stage_name": "ingest"},
        {"status": "completed", "stage_name": "maintenance"},
    ]

    assert latest_effective_run_status(runs) is None


def test_latest_effective_run_status_ignores_trigger_type_maintenance():
    runs = [
        {"status": "failed", "stage_name": "ingest", "finished_at": "2026-04-01T10:00:00"},
        {
            "status": "completed",
            "stage_name": "ingest",
            "trigger_type": "maintenance",
            "finished_at": "2026-04-01T12:00:00",
        },
    ]

    assert latest_effective_run_status(runs) == "failed"


def test_latest_effective_run_status_ignores_maintenance_only_flag():
    runs = [
        {"status": "failed", "stage_name": "ingest", "finished_at": "2026-04-01T10:00:00"},
        {
            "status": "completed",
            "stage_name": "ingest",
            "maintenance_only": True,
            "finished_at": "2026-04-01T12:00:00",
        },
    ]

    assert latest_effective_run_status(runs) == "failed"
