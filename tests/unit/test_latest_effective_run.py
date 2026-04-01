from snapgit.pipeline.orchestrator import latest_effective_run_status


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
