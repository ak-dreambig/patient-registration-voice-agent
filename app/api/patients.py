"""REST routes for patients. Thin HTTP layer over `patient_service`."""

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.responses import fail, ok
from app.schemas import CallLogOut, PatientCreate, PatientOut, PatientUpdate
from app.services import call_log_service, patient_service

router = APIRouter(prefix="/patients", tags=["patients"])


class InvalidIdError(ValueError):
    """Path id is not a UUID (mapped to HTTP 400)."""


def _parse_id(patient_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(patient_id)
    except ValueError:
        raise InvalidIdError("patient_id must be a valid UUID.") from None


def _out(patient: object) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


@router.get("")
def list_patients(
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """List non-deleted patients, newest first. Filters are exact matches."""
    patients = patient_service.list_patients(db, last_name, date_of_birth, phone_number)
    return ok([_out(p) for p in patients])


@router.get("/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Fetch one patient by id."""
    return ok(_out(patient_service.get_patient(db, _parse_id(patient_id))))


@router.post("", status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)) -> JSONResponse:
    """Register a new patient."""
    return ok(_out(patient_service.create_patient(db, payload)), status=201)


@router.put("/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)) -> JSONResponse:
    """Partially update a patient; only provided fields change."""
    return ok(_out(patient_service.update_patient(db, _parse_id(patient_id), payload)))


@router.delete("/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Soft-delete a patient and return the final record."""
    return ok(_out(patient_service.soft_delete_patient(db, _parse_id(patient_id))))


@router.get("/{patient_id}/calls")
def list_patient_calls(patient_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """Call logs linked to a patient, newest first."""
    patient = patient_service.get_patient(db, _parse_id(patient_id))
    calls = call_log_service.list_for_patient(db, patient.patient_id)
    return ok([CallLogOut.model_validate(c).model_dump(mode="json") for c in calls])


def invalid_id_response(exc: InvalidIdError) -> JSONResponse:
    """Envelope for an unparsable patient id."""
    return fail("bad_request", str(exc), [{"field": "patient_id", "message": str(exc)}], status=400)
