"""Unit tests for the pure validation/normalization functions and schemas."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app import validators as v
from app.schemas import PatientCreate, PatientUpdate, error_details


@pytest.mark.parametrize(
    "raw",
    ["2125550143", "(212) 555-0143", "212-555-0143", "+1 212 555 0143", "12125550143", "212.555.0143"],
)
def test_phone_normalization(raw: str) -> None:
    assert v.normalize_phone(raw) == "2125550143"


@pytest.mark.parametrize("raw", ["555", "212555014", "212555014399", "0125550143", "2120550143", "abc"])
def test_invalid_phone_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="Phone number"):
        v.normalize_phone(raw)


def test_dob_formats() -> None:
    assert v.parse_dob("03/15/1985") == date(1985, 3, 15)
    assert v.parse_dob("1985-03-15") == date(1985, 3, 15)
    assert v.parse_dob("3/5/1985") == date(1985, 3, 5)


def test_dob_future_rejected() -> None:
    future = (date.today() + timedelta(days=1)).strftime("%m/%d/%Y")
    with pytest.raises(ValueError, match="future"):
        v.parse_dob(future)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [("02/30/1990", "real calendar date"), ("1899-12-31", "1900"), ("March 5 1990", "format"), ("", "required")],
)
def test_dob_invalid(raw: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        v.parse_dob(raw)


@pytest.mark.parametrize(("raw", "code"), [("ny", "NY"), ("New York", "NY"), ("  california ", "CA"),
                                           ("District of Columbia", "DC"), ("puerto rico", "PR")])
def test_state_normalization(raw: str, code: str) -> None:
    assert v.normalize_state(raw) == code


@pytest.mark.parametrize("raw", ["XX", "Narnia", "N"])
def test_invalid_state(raw: str) -> None:
    with pytest.raises(ValueError, match="State"):
        v.normalize_state(raw)


def test_zip_formats() -> None:
    assert v.validate_zip("10001") == "10001"
    assert v.validate_zip("10001-1234") == "10001-1234"
    assert v.validate_zip("100011234") == "10001-1234"
    with pytest.raises(ValueError, match="ZIP"):
        v.validate_zip("1000")


@pytest.mark.parametrize("raw", ["O'Brien", "Smith-Jones", "Mary Ann", "  jane  "])
def test_valid_names(raw: str) -> None:
    assert v.validate_name(raw, "First name") == " ".join(raw.split())


@pytest.mark.parametrize("raw", ["J4ne", "-Jane", "Jane!", "", "A" * 51])
def test_invalid_names(raw: str) -> None:
    with pytest.raises(ValueError, match="First name"):
        v.validate_name(raw, "First name")


def test_sex_aliases() -> None:
    assert v.normalize_sex("f") == "Female"
    assert v.normalize_sex("MALE") == "Male"
    assert v.normalize_sex("prefer not to say") == "Decline to Answer"
    with pytest.raises(ValueError, match="Sex"):
        v.normalize_sex("unknown")


def test_member_id_and_language() -> None:
    assert v.normalize_member_id("abc 123-45") == "ABC12345"
    assert v.normalize_member_id("") is None
    with pytest.raises(ValueError):
        v.normalize_member_id("ABC#123")
    assert v.normalize_language(None) == "English"
    assert v.normalize_language("spanish") == "Spanish"


def test_control_characters_rejected() -> None:
    with pytest.raises(ValueError, match="control"):
        v.require_text("Main\x00St", "City", 100)


VALID = {
    "first_name": "Jane", "last_name": "Doe", "date_of_birth": "1990-01-02", "sex": "female",
    "phone_number": "+1 (212) 555-0143", "email": " Jane.Doe@Example.COM ", "address_line_1": "1 Main St",
    "address_line_2": "", "city": "New York", "state": "new york", "zip_code": "10001",
}


def test_patient_create_normalizes() -> None:
    p = PatientCreate.model_validate(VALID)
    assert p.phone_number == "2125550143"
    assert p.email == "jane.doe@example.com"
    assert p.state == "NY"
    assert p.address_line_2 is None
    assert p.preferred_language == "English"
    assert p.sex.value == "Female"


def test_patient_create_errors_name_fields() -> None:
    data = {**VALID, "date_of_birth": "13/45/2000", "email": "nope"}
    del data["city"]
    with pytest.raises(ValidationError) as exc:
        PatientCreate.model_validate(data)
    fields = {d["field"]: d["message"] for d in error_details(exc.value)}
    assert fields["city"] == "City is required."
    assert "Date of birth" in fields["date_of_birth"]
    assert "Email" in fields["email"]


def test_patient_update_rejects_null_required() -> None:
    with pytest.raises(ValidationError):
        PatientUpdate.model_validate({"first_name": None})
    with pytest.raises(ValidationError):
        PatientUpdate.model_validate({"city": "  "})
    assert PatientUpdate.model_validate({"email": ""}).model_dump(exclude_unset=True) == {"email": None}
