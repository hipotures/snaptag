from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import snapgit.api.db as api_db
from snapgit.main import app


def test_runtime_app_exposes_ingest_and_search_routes(tmp_path, monkeypatch):
    database_path = tmp_path / "api_ingest_search.db"
    _upgrade_db(database_path)
    db_url = f"sqlite:///{database_path}"

    def settings_factory():
        return type("SettingsOverride", (), {"database_url": db_url})()
    monkeypatch.setattr(api_db, "Settings", settings_factory)

    screenshot_path = tmp_path / 'a "b" sample.png'
    screenshot_path.write_text("open source transcription model", encoding="utf-8")

    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    ingest = client.post(
        "/ingest",
        json={"source_type": "android_upload", "path": str(screenshot_path)},
    )
    assert ingest.status_code == 201
    ingest_body = ingest.json()
    assert ingest_body["source_type"] == "android_upload"
    assert ingest_body["path"] == str(screenshot_path)
    assert isinstance(ingest_body["asset_id"], int)
    assert isinstance(ingest_body["pipeline_run_id"], int)
    assert ingest_body["status"] == "completed"

    search = client.get("/search", params={"q": "sample"})
    assert search.status_code == 200
    search_body = search.json()
    assert search_body["total"] == 1
    assert search_body["results"][0]["asset_id"] == ingest_body["asset_id"]

    screenshots = client.get("/screenshots")
    assert screenshots.status_code == 200
    screenshots_body = screenshots.json()
    assert screenshots_body["count"] >= 1
    assert screenshots_body["items"][0]["asset_id"] == ingest_body["asset_id"]

    reindex = client.post(
        "/actions/reindex",
        json={"asset_id": ingest_body["asset_id"], "title": "sample"},
    )
    assert reindex.status_code == 200
    reindex_body = reindex.json()
    assert reindex_body["status"] == "completed"
    assert reindex_body["asset_id"] == ingest_body["asset_id"]


def _upgrade_db(database_path: Path) -> None:
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "head")
