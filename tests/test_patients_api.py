"""REST API tests for /patients."""

import time
import uuid
from typing import Any

from fastapi.testclient import TestClient

PATIENT: dict[str, Any] = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "01/02/1990",
    "sex": "Female",
    "phone_number": "(212) 555-0143",
    "address_line_1": "1 Main St",
    "city": "New York",
    "state": "NY",
    "zip_code": "10001",
}


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    resp = client.post("/patients", json={**PATIENT, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_create_returns_201_envelope(client: TestClient) -> None:
    resp = client.post("/patients", json=PATIENT)
    assert resp.status_code == 201
    body = resp.json()
    assert body["error"] is None
    data = body["data"]
    uuid.UUID(data["patient_id"])
    assert data["phone_number"] == "2125550143"
    assert data["date_of_birth"] == "01/02/1990"
    assert data["preferred_language"] == "English"


def test_missing_required_returns_422_with_details(client: TestClient) -> None:
    payload = {k: v for k, v in PATIENT.items() if k not in ("last_name", "zip_code")}
    resp = client.post("/patients", json={**payload, "phone_number": "555"})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    fields = {d["field"] for d in error["details"]}
    assert fields == {"last_name", "zip_code", "phone_number"}


def test_malformed_json_returns_400(client: TestClient) -> None:
    resp = client.post("/patients", content="{not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


def test_get_by_id(client: TestClient) -> None:
    created = _create(client)
    resp = client.get(f"/patients/{created['patient_id']}")
    assert resp.status_code == 200
    assert resp.json()["data"]["first_name"] == "Jane"


def test_invalid_uuid_returns_400(client: TestClient) -> None:
    resp = client.get("/patients/not-a-uuid")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


def test_unknown_id_returns_404(client: TestClient) -> None:
    resp = client.get(f"/patients/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_filters(client: TestClient) -> None:
    _create(client)
    _create(client, first_name="John", last_name="Smith", phone_number="3125550188", date_of_birth="1975-06-30")

    def names(query: str) -> list[str]:
        resp = client.get(f"/patients?{query}")
        assert resp.status_code == 200
        return [p["first_name"] for p in resp.json()["data"]]

    assert names("") == ["John", "Jane"]  # newest first
    assert names("last_name=smith") == ["John"]
    assert names("date_of_birth=1990-01-02") == ["Jane"]
    assert names("date_of_birth=06/30/1975") == ["John"]
    assert names("phone_number=%2B1%20312-555-0188") == ["John"]
    assert client.get("/patients?date_of_birth=garbage").status_code == 400
    assert client.get("/patients?phone_number=123").status_code == 400


def test_partial_update_bumps_updated_at(client: TestClient) -> None:
    created = _create(client, email="jane@example.com")
    time.sleep(0.02)
    resp = client.put(f"/patients/{created['patient_id']}", json={"city": "Brooklyn", "state": "new york"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["city"] == "Brooklyn"
    assert data["email"] == "jane@example.com"  # untouched
    assert data["updated_at"] > created["updated_at"]


def test_update_validation_and_errors(client: TestClient) -> None:
    created = _create(client)
    pid = created["patient_id"]
    resp = client.put(f"/patients/{pid}", json={"first_name": None})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "first_name"
    assert client.put(f"/patients/{pid}", json={}).status_code == 400
    assert client.put(f"/patients/{uuid.uuid4()}", json={"city": "X"}).status_code == 404
    assert client.put("/patients/bad-id", json={"city": "X"}).status_code == 400


def test_soft_delete(client: TestClient) -> None:
    created = _create(client)
    pid = created["patient_id"]
    resp = client.delete(f"/patients/{pid}")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted_at"] is not None
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []
    assert client.delete(f"/patients/{pid}").status_code == 404


def test_patient_calls_empty(client: TestClient) -> None:
    created = _create(client)
    resp = client.get(f"/patients/{created['patient_id']}/calls")
    assert resp.status_code == 200
    assert resp.json()["data"] == []
    assert client.get(f"/patients/{uuid.uuid4()}/calls").status_code == 404
