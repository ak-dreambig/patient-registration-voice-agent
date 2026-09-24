"""Call log persistence: one row per Vapi call, upserted by `vapi_call_id`."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CallLog


def get_or_create(db: Session, vapi_call_id: str) -> CallLog:
    """Return the call log for this Vapi call, creating it if needed (race-safe)."""
    stmt = select(CallLog).where(CallLog.vapi_call_id == vapi_call_id)
    call = db.scalar(stmt)
    if call is not None:
        return call
    call = CallLog(vapi_call_id=vapi_call_id)
    db.add(call)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent webhook created it first.
        db.rollback()
        return db.scalars(stmt).one()
    return call


def link_patient(db: Session, vapi_call_id: str, patient_id: uuid.UUID, payload: dict[str, Any]) -> CallLog:
    """Attach the saved patient and the last create/update payload to the call."""
    call = get_or_create(db, vapi_call_id)
    call.patient_id = patient_id
    call.final_payload = payload
    db.commit()
    return call


def record_call_end(
    db: Session,
    vapi_call_id: str,
    transcript: str | None,
    summary: str | None,
    ended_reason: str | None,
) -> CallLog:
    """Store end-of-call data; keeps existing values when a field is missing."""
    call = get_or_create(db, vapi_call_id)
    call.transcript = transcript or call.transcript
    call.summary = summary or call.summary
    call.ended_reason = (ended_reason or call.ended_reason or "")[:100] or None
    db.commit()
    return call


def list_for_patient(db: Session, patient_id: uuid.UUID) -> list[CallLog]:
    """Calls linked to a patient, newest first."""
    stmt = select(CallLog).where(CallLog.patient_id == patient_id).order_by(CallLog.created_at.desc())
    return list(db.scalars(stmt))
