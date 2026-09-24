"""POST /vapi/webhook — Vapi server messages: tool calls and end-of-call reports.

Tool calls always get HTTP 200 with `{"results": [...]}`; each result is a short
string prefixed with a status token the LLM can branch on.
"""

import hmac
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import validators as v
from app.config import get_settings
from app.db import get_db
from app.schemas import PatientCreate, PatientUpdate, error_details, format_dob
from app.services import call_log_service, patient_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vapi", tags=["vapi"])

SYSTEM_ERROR = "SYSTEM_ERROR: The record could not be saved due to a technical problem."


class ToolInputError(ValueError):
    """Tool arguments failed validation; `details` is a list of (field, message)."""

    def __init__(self, details: list[tuple[str, str]]) -> None:
        super().__init__("; ".join(f"{f}: {m}" for f, m in details))


def _validation_error(exc: ValidationError) -> ToolInputError:
    return ToolInputError([(d["field"], d["message"]) for d in error_details(exc)])


def _link_call(db: Session, call_id: str | None, patient_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Best effort: a failed link must not turn a saved patient into an error (agent would retry)."""
    if not call_id:
        return
    try:
        call_log_service.link_patient(db, call_id, patient_id, payload)
    except Exception:
        db.rollback()
        logger.exception("call_link_failed", extra={"call_id": call_id})


def find_patient_by_phone(db: Session, args: dict[str, Any], _call_id: str | None) -> str:
    """Look up the most recently updated patient with this phone number."""
    try:
        phone = v.normalize_phone(args.get("phone_number"))
    except ValueError as exc:
        raise ToolInputError([("phone_number", str(exc))]) from None
    patient = patient_service.find_by_phone(db, phone)
    if patient is None:
        return "NOT_FOUND: No existing patient with that phone number."
    return (
        f"FOUND: patient_id={patient.patient_id}; first_name={patient.first_name}; "
        f"last_name={patient.last_name}; date_of_birth={format_dob(patient.date_of_birth)}"
    )


def create_patient(db: Session, args: dict[str, Any], call_id: str | None) -> str:
    """Register a new patient."""
    try:
        data = PatientCreate.model_validate(args)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    patient = patient_service.create_patient(db, data)
    _link_call(db, call_id, patient.patient_id, data.model_dump(mode="json"))
    return f"SUCCESS: Patient registered. patient_id={patient.patient_id}; first_name={patient.first_name}"


def update_patient(db: Session, args: dict[str, Any], call_id: str | None) -> str:
    """Partially update an existing patient."""
    fields = dict(args)
    raw_id = fields.pop("patient_id", None)
    try:
        patient_id = uuid.UUID(str(raw_id).strip())
    except ValueError:
        raise ToolInputError([("patient_id", "Patient ID is missing or not a valid ID.")]) from None
    try:
        data = PatientUpdate.model_validate(fields)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    try:
        patient = patient_service.update_patient(db, patient_id, data)
    except patient_service.EmptyUpdateError as exc:
        raise ToolInputError([("fields", str(exc))]) from None
    _link_call(db, call_id, patient.patient_id, data.model_dump(mode="json", exclude_unset=True))
    return f"SUCCESS: Patient updated. patient_id={patient.patient_id}; first_name={patient.first_name}"


TOOLS: dict[str, Callable[[Session, dict[str, Any], str | None], str]] = {
    "find_patient_by_phone": find_patient_by_phone,
    "create_patient": create_patient,
    "update_patient": update_patient,
}


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Tool calls may be under `toolCallList` or `toolWithToolCallList[].toolCall`."""
    calls = message.get("toolCallList")
    if isinstance(calls, list) and calls:
        return [c for c in calls if isinstance(c, dict)]
    wrapped = message.get("toolWithToolCallList") or []
    return [w["toolCall"] for w in wrapped if isinstance(w, dict) and isinstance(w.get("toolCall"), dict)]


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Arguments may arrive as a dict or a JSON string."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise ToolInputError([("arguments", "Tool arguments could not be read as JSON.")]) from None
    if not isinstance(raw, dict):
        raise ToolInputError([("arguments", "Tool arguments must be an object.")])
    return raw


def run_tool(db: Session, tool_call: dict[str, Any], call_id: str | None) -> dict[str, str]:
    """Execute one tool call; never raises."""
    tool_call_id = str(tool_call.get("id") or "")
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = function.get("name") or tool_call.get("name") or ""
    args: dict[str, Any] = {}
    try:
        args = _parse_arguments(function.get("arguments", tool_call.get("arguments")))
        handler = TOOLS.get(name)
        result = handler(db, args, call_id) if handler else f"SYSTEM_ERROR: Unknown tool '{name}'."
    except ToolInputError as exc:
        result = f"VALIDATION_ERROR: {exc}"
    except patient_service.PatientNotFoundError:
        result = "NOT_FOUND: No patient exists with that patient_id."
    except Exception:
        db.rollback()
        logger.exception("tool_call_failed", extra={"call_id": call_id, "tool": name})
        result = SYSTEM_ERROR
    logger.info(
        "tool_call",
        extra={"call_id": call_id, "tool": name, "arguments": args, "status": result.split(":", 1)[0]},
    )
    return {"toolCallId": tool_call_id, "result": result}


def _as_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else json.dumps(value, default=str)


def handle_end_of_call(db: Session, message: dict[str, Any], call_id: str | None) -> None:
    """Upsert the call log with transcript/summary/ended reason; never raises."""
    artifact = message.get("artifact") if isinstance(message.get("artifact"), dict) else {}
    analysis = message.get("analysis") if isinstance(message.get("analysis"), dict) else {}
    transcript = _as_text(artifact.get("transcript") or message.get("transcript"))
    summary = _as_text(analysis.get("summary") or message.get("summary"))
    ended_reason = _as_text(message.get("endedReason"))
    if not call_id:
        logger.warning("call_ended_without_id", extra={"ended_reason": ended_reason})
        return
    try:
        call = call_log_service.record_call_end(db, call_id, transcript, summary, ended_reason)
        logger.info(
            "call_ended",
            extra={
                "call_id": call_id,
                "ended_reason": ended_reason,
                "patient_id": str(call.patient_id) if call.patient_id else None,
                "final_payload": call.final_payload,
            },
        )
    except Exception:
        db.rollback()
        logger.exception("call_log_failed", extra={"call_id": call_id})


def verify_secret(x_vapi_secret: str | None = Header(default=None)) -> None:
    """If VAPI_SECRET is set, require a matching `x-vapi-secret` header."""
    secret = get_settings().vapi_secret
    if secret and not hmac.compare_digest((x_vapi_secret or "").encode(), secret.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing x-vapi-secret header.")


@router.post("/webhook", dependencies=[Depends(verify_secret)])
def vapi_webhook(body: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> JSONResponse:
    """Dispatch Vapi server messages by `message.type`."""
    message = body.get("message")
    if not isinstance(message, dict):
        return JSONResponse(content={})

    msg_type = message.get("type")
    call = message.get("call") if isinstance(message.get("call"), dict) else {}
    call_id = _as_text(call.get("id"))

    if msg_type == "tool-calls":
        results = [run_tool(db, tc, call_id) for tc in _extract_tool_calls(message)]
        return JSONResponse(content={"results": results})
    if msg_type == "end-of-call-report":
        handle_end_of_call(db, message, call_id)
        return JSONResponse(content={})
    logger.debug("vapi_event_ignored", extra={"type": msg_type, "call_id": call_id})
    return JSONResponse(content={})
