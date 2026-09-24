"""Pydantic request/response models. All normalization rules live in `app.validators`."""

import uuid
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, ValidationError, field_serializer, field_validator

from app import validators as v
from app.models import Sex

_REQUIRED_TEXT = {"address_line_1": ("Address line 1", 200), "city": ("City", 100)}
_OPTIONAL_TEXT = {
    "address_line_2": ("Address line 2", 100),
    "insurance_provider": ("Insurance provider", 100),
    "emergency_contact_name": ("Emergency contact name", 100),
}


class _PatientValidators(BaseModel):
    """Field validators shared by create and update models (run before type checks)."""

    model_config = ConfigDict(extra="ignore")

    @field_validator("first_name", "last_name", mode="before", check_fields=False)
    @classmethod
    def _name(cls, value: Any, info: Any) -> str:
        return v.validate_name(value, _label(info.field_name))

    @field_validator("date_of_birth", mode="before", check_fields=False)
    @classmethod
    def _dob(cls, value: Any) -> date:
        return v.parse_dob(value)

    @field_validator("sex", mode="before", check_fields=False)
    @classmethod
    def _sex(cls, value: Any) -> str:
        return v.normalize_sex(value)

    @field_validator("phone_number", mode="before", check_fields=False)
    @classmethod
    def _phone(cls, value: Any) -> str:
        return v.normalize_phone(value)

    @field_validator("emergency_contact_phone", mode="before", check_fields=False)
    @classmethod
    def _emergency_phone(cls, value: Any) -> str | None:
        return v.normalize_optional_phone(value, "Emergency contact phone")

    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _email(cls, value: Any) -> str | None:
        return v.normalize_email(value)

    @field_validator("address_line_1", "city", mode="before", check_fields=False)
    @classmethod
    def _required_text(cls, value: Any, info: Any) -> str:
        label, max_len = _REQUIRED_TEXT[info.field_name]
        return v.require_text(value, label, max_len)

    @field_validator(
        "address_line_2", "insurance_provider", "emergency_contact_name", mode="before", check_fields=False
    )
    @classmethod
    def _optional_text(cls, value: Any, info: Any) -> str | None:
        label, max_len = _OPTIONAL_TEXT[info.field_name]
        return v.optional_text(value, label, max_len)

    @field_validator("state", mode="before", check_fields=False)
    @classmethod
    def _state(cls, value: Any) -> str:
        return v.normalize_state(value)

    @field_validator("zip_code", mode="before", check_fields=False)
    @classmethod
    def _zip(cls, value: Any) -> str:
        return v.validate_zip(value)

    @field_validator("insurance_member_id", mode="before", check_fields=False)
    @classmethod
    def _member_id(cls, value: Any) -> str | None:
        return v.normalize_member_id(value)

    @field_validator("preferred_language", mode="before", check_fields=False)
    @classmethod
    def _language(cls, value: Any) -> str:
        return v.normalize_language(value)


class PatientCreate(_PatientValidators):
    """Payload for registering a new patient."""

    first_name: str
    last_name: str
    date_of_birth: date
    sex: Sex
    phone_number: str
    email: EmailStr | None = None
    address_line_1: str
    address_line_2: str | None = None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str = "English"
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


class PatientUpdate(_PatientValidators):
    """Partial update: every field optional, but provided fields obey the create rules.

    Required fields sent as null/empty are rejected by their validators.
    """

    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: Sex | None = None
    phone_number: str | None = None
    email: EmailStr | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


class PatientOut(BaseModel):
    """API representation of a patient. DOB is rendered as MM/DD/YYYY."""

    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: Sex
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_serializer("date_of_birth")
    def _dob_out(self, value: date) -> str:
        return format_dob(value)

    @field_serializer("created_at", "updated_at", "deleted_at")
    def _ts_out(self, value: datetime | None) -> str | None:
        return as_utc(value).isoformat() if value else None


class CallLogOut(BaseModel):
    """API representation of a call log."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vapi_call_id: str
    patient_id: uuid.UUID | None
    transcript: str | None
    summary: str | None
    ended_reason: str | None
    final_payload: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ts_out(self, value: datetime) -> str:
        return as_utc(value).isoformat()


def format_dob(value: date) -> str:
    """Render a date as MM/DD/YYYY."""
    return value.strftime("%m/%d/%Y")


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _label(field: str) -> str:
    return field.replace("_", " ").capitalize()


def error_details(exc: ValidationError | Any) -> list[dict[str, str]]:
    """Convert pydantic errors into `[{"field", "message"}]` with plain-English messages."""
    details = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p != "body"]
        field = ".".join(loc) or "body"
        if err.get("type") == "missing":
            message = f"{_label(loc[-1]) if loc else 'Value'} is required."
        else:
            message = str(err.get("msg", "Invalid value.")).removeprefix("Value error, ")
        details.append({"field": field, "message": message})
    return details
