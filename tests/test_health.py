"""Smoke test for the health endpoint."""

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"data": {"status": "ok", "db": "ok"}, "error": None}
