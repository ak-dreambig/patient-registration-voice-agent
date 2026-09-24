"""Patient business logic. Shared by the REST API and the Vapi webhook."""

import logging
import re
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app import validators as v
from app.models import Patient, utcnow
from app.schemas import PatientCreate, PatientUpdate

logger = logging.getLogger(__name__)


class PatientNotFoundError(Exception):
    """No non-deleted patient exists with the given id."""

    def __init__(self, patient_id: uuid.UUID | str) -> None:
        super().__init__(f"No patient found with id {patient_id}.")
        self.patient_id = patient_id


class InvalidFilterError(ValueError):
    """A list filter value could not be parsed."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class EmptyUpdateError(ValueError):
    """An update request contained no fields."""


def _active() -> Select[tuple[Patient]]:
    return select(Patient).where(Patient.deleted_at.is_(None))


def create_patient(db: Session, data: PatientCreate) -> Patient:
    """Insert a new patient from validated data."""
    patient = Patient(**data.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    logger.info("patient_created", extra={"patient_id": str(patient.patient_id)})
    return patient


def get_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    """Return a non-deleted patient or raise `PatientNotFoundError`."""
    patient = db.scalar(_active().where(Patient.patient_id == patient_id))
    if patient is None:
        raise PatientNotFoundError(patient_id)
    return patient


def list_patients(
    db: Session,
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
) -> list[Patient]:
    """List non-deleted patients, newest first, with optional exact-match filters."""
    stmt = _active()
    if last_name and last_name.strip():
        stmt = stmt.where(func.lower(Patient.last_name) == last_name.strip().lower())
    if date_of_birth:
        try:
            stmt = stmt.where(Patient.date_of_birth == v.parse_dob(date_of_birth))
        except ValueError as exc:
            raise InvalidFilterError("date_of_birth", str(exc)) from None
    if phone_number:
        try:
            stmt = stmt.where(Patient.phone_number == v.normalize_phone(phone_number))
        except ValueError as exc:
            raise InvalidFilterError("phone_number", str(exc)) from None
    return list(db.scalars(stmt.order_by(Patient.created_at.desc())))


def search_patients(db: Session, query: str | None) -> list[Patient]:
    """Dashboard search: digits match phone (substring), otherwise last-name prefix. Loads calls."""
    stmt = _active().options(selectinload(Patient.calls))
    q = (query or "").strip()
    digits = re.sub(r"\D", "", q)
    if digits:
        stmt = stmt.where(Patient.phone_number.contains(digits[-10:]))
    elif q:
        stmt = stmt.where(func.lower(Patient.last_name).startswith(q.lower()))
    return list(db.scalars(stmt.order_by(Patient.created_at.desc())))


def count_patients(db: Session) -> int:
    """Number of non-deleted patients."""
    return db.scalar(select(func.count()).select_from(Patient).where(Patient.deleted_at.is_(None))) or 0


def update_patient(db: Session, patient_id: uuid.UUID, data: PatientUpdate) -> Patient:
    """Apply only the fields that were provided; bumps `updated_at`."""
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise EmptyUpdateError("No fields to update were provided.")
    patient = get_patient(db, patient_id)
    for field, value in changes.items():
        setattr(patient, field, value)
    patient.updated_at = utcnow()
    db.commit()
    db.refresh(patient)
    logger.info("patient_updated", extra={"patient_id": str(patient_id), "fields": sorted(changes)})
    return patient


def soft_delete_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    """Mark a patient deleted; it disappears from lists and lookups."""
    patient = get_patient(db, patient_id)
    patient.deleted_at = patient.updated_at = utcnow()
    db.commit()
    db.refresh(patient)
    logger.info("patient_deleted", extra={"patient_id": str(patient_id)})
    return patient


def find_by_phone(db: Session, phone_number: str) -> Patient | None:
    """Most recently updated non-deleted patient with this (normalized) phone number."""
    stmt = _active().where(Patient.phone_number == phone_number).order_by(Patient.updated_at.desc())
    return db.scalars(stmt).first()
