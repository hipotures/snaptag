from fastapi.testclient import TestClient

from snapgit.api.app import app


def test_ingest_then_search_returns_asset():
    client = TestClient(app)

    ingest = client.post("/ingest", json={"source_type": "android_upload"})
    assert ingest.status_code == 201

    search = client.get("/search", params={"q": "test"})
    assert search.status_code == 200
