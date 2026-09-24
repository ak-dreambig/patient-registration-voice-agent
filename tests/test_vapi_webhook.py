"""Tests for POST /vapi/webhook."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import patient_service

ARGS: dict[str, Any] = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "1990-01-02",
    "sex": "female",
    "phone_number": "+1 212-555-0143",
    "address_line_1": "1 Main St",
    "city": "New York",
    "state": "New York",
    "zip_code": "10001",
}


def tool_call(name: str, arguments: Any, call_id: str = "call_abc", tc_id: str = "tc_1") -> dict[str, Any]:
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCallList": [{"id": tc_id, "type": "function", "function": {"name": name, "arguments": arguments}}],
        }
    }


def run(client: TestClient, payload: dict[str, Any], **kwargs: Any) -> str:
    resp = client.post("/vapi/webhook", json=payload, **kwargs)
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert results[0]["toolCallId"] == "tc_1"
    return results[0]["result"]


def test_create_patient_success_links_call(client: TestClient) -> None:
    result = run(client, tool_call("create_patient", ARGS))
    assert result.startswith("SUCCESS: Patient registered. patient_id=")
    patient_id = result.split("patient_id=")[1].split(";")[0]
    patient = client.get(f"/patients/{patient_id}").json()["data"]
    assert patient["state"] == "NY"
    calls = client.get(f"/patients/{patient_id}/calls").json()["data"]
    assert calls[0]["vapi_call_id"] == "call_abc"
    assert calls[0]["final_payload"]["phone_number"] == "2125550143"


def test_invalid_dob_returns_validation_error(client: TestClient) -> None:
    result = run(client, tool_call("create_patient", {**ARGS, "date_of_birth": "02/30/1990"}))
    assert result.startswith("VALIDATION_ERROR: date_of_birth: ")
    assert "real calendar date" in result


def test_find_patient_by_phone(client: TestClient) -> None:
    assert run(client, tool_call("find_patient_by_phone", {"phone_number": "2125550143"})).startswith("NOT_FOUND")
    run(client, tool_call("create_patient", ARGS))
    result = run(client, tool_call("find_patient_by_phone", {"phone_number": "(212) 555-0143"}))
    assert result.startswith("FOUND: patient_id=")
    assert "first_name=Jane; last_name=Doe; date_of_birth=01/02/1990" in result
    bad = run(client, tool_call("find_patient_by_phone", {"phone_number": "555"}))
    assert bad.startswith("VALIDATION_ERROR: phone_number: ")


def test_arguments_as_json_string(client: TestClient) -> None:
    assert run(client, tool_call("create_patient", json.dumps(ARGS))).startswith("SUCCESS")


def test_tool_with_tool_call_list_shape(client: TestClient) -> None:
    payload = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call_x"},
            "toolWithToolCallList": [
                {"name": "create_patient", "toolCall": {"id": "tc_1", "function": {"name": "create_patient", "arguments": ARGS}}}
            ],
        }
    }
    assert run(client, payload).startswith("SUCCESS")


def test_update_patient(client: TestClient) -> None:
    created = run(client, tool_call("create_patient", ARGS))
    patient_id = created.split("patient_id=")[1].split(";")[0]
    result = run(client, tool_call("update_patient", {"patient_id": patient_id, "city": "Brooklyn"}))
    assert result == f"SUCCESS: Patient updated. patient_id={patient_id}; first_name=Jane"
    unknown = run(client, tool_call("update_patient", {"patient_id": "00000000-0000-0000-0000-000000000000", "city": "X"}))
    assert unknown.startswith("NOT_FOUND")
    bad_id = run(client, tool_call("update_patient", {"patient_id": "nope", "city": "X"}))
    assert bad_id.startswith("VALIDATION_ERROR: patient_id: ")


def test_db_failure_returns_system_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("database is down")

    monkeypatch.setattr(patient_service, "create_patient", boom)
    resp = client.post("/vapi/webhook", json=tool_call("create_patient", ARGS))
    assert resp.status_code == 200
    assert resp.json()["results"][0]["result"] == (
        "SYSTEM_ERROR: The record could not be saved due to a technical problem."
    )


def test_end_of_call_report_stored(client: TestClient) -> None:
    created = run(client, tool_call("create_patient", ARGS, call_id="call_eoc"))
    patient_id = created.split("patient_id=")[1].split(";")[0]
    report = {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "call_eoc"},
            "endedReason": "customer-ended-call",
            "artifact": {"transcript": "AI: Hello\nUser: Hi"},
            "analysis": {"summary": "Registered Jane Doe."},
        }
    }
    resp = client.post("/vapi/webhook", json=report)
    assert resp.status_code == 200 and resp.json() == {}
    call = client.get(f"/patients/{patient_id}/calls").json()["data"][0]
    assert call["transcript"] == "AI: Hello\nUser: Hi"
    assert call["summary"] == "Registered Jane Doe."
    assert call["ended_reason"] == "customer-ended-call"


def test_dropped_call_logged_without_patient(client: TestClient) -> None:
    report = {"message": {"type": "end-of-call-report", "call": {"id": "call_drop"}, "transcript": "AI: Hello"}}
    assert client.post("/vapi/webhook", json=report).status_code == 200


def test_other_message_types_ignored(client: TestClient) -> None:
    resp = client.post("/vapi/webhook", json={"message": {"type": "status-update", "status": "in-progress"}})
    assert resp.status_code == 200 and resp.json() == {}


def test_wrong_secret_returns_401(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "vapi_secret", "s3cret")
    payload = tool_call("find_patient_by_phone", {"phone_number": "2125550143"})
    wrong = client.post("/vapi/webhook", json=payload, headers={"x-vapi-secret": "wrong"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "unauthorized"
    assert client.post("/vapi/webhook", json=payload).status_code == 401
    assert client.post("/vapi/webhook", json=payload, headers={"x-vapi-secret": "s3cret"}).status_code == 200
