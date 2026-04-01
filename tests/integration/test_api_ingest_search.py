from fastapi.testclient import TestClient

from snapgit.main import app


def test_runtime_app_exposes_ingest_and_search_routes():
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    ingest = client.post(
        "/ingest",
        json={"source_type": "android_upload", "path": '/tmp/a "b".png'},
    )
    assert ingest.status_code == 201
    assert ingest.json()["source_type"] == "android_upload"
    assert ingest.json()["path"] == '/tmp/a "b".png'

    search = client.get("/search", params={"q": "test"})
    assert search.status_code == 200
