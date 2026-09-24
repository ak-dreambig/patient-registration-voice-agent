"""Pure normalization/validation functions shared by the REST API and the voice webhook.

Every function normalizes first, then validates, and raises `ValueError` with a
plain-English sentence that names the field (the voice agent reads these aloud).
"""

import re
from datetime import date, datetime
from typing import Any

from email_validator import EmailNotValidError, validate_email

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' \-]*$")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9]{1,50}$")
_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
MIN_DOB = date(1900, 1, 1)

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
_SEX_ALIASES = {
    "male": "Male",
    "m": "Male",
    "female": "Female",
    "f": "Female",
    "other": "Other",
    "decline to answer": "Decline to Answer",
    "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer",
}

STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
_STATE_NAMES = {name.lower(): code for code, name in STATES.items()}
_STATE_NAMES.update({
    "washington dc": "DC",
    "washington d c": "DC",
    "us virgin islands": "VI",
    "u s virgin islands": "VI",
})


def clean_text(value: Any, label: str = "Value") -> str | None:
    """Coerce to a trimmed string; reject control characters; empty → None."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if _CONTROL_CHARS.search(value):
        raise ValueError(f"{label} contains invalid control characters.")
    return value or None


def require_text(value: Any, label: str, max_len: int) -> str:
    """Required free-text field: trimmed, non-empty, at most `max_len` characters."""
    cleaned = clean_text(value, label)
    if cleaned is None:
        raise ValueError(f"{label} is required and cannot be empty.")
    if len(cleaned) > max_len:
        raise ValueError(f"{label} must be at most {max_len} characters.")
    return cleaned


def optional_text(value: Any, label: str, max_len: int) -> str | None:
    """Optional free-text field: trimmed, at most `max_len` characters, empty → None."""
    cleaned = clean_text(value, label)
    if cleaned is not None and len(cleaned) > max_len:
        raise ValueError(f"{label} must be at most {max_len} characters.")
    return cleaned


def validate_name(value: Any, label: str = "Name") -> str:
    """1–50 chars; letters, apostrophes, hyphens and internal spaces; must start with a letter."""
    name = require_text(value, label, 50)
    name = re.sub(r"\s+", " ", name)
    if not _NAME_RE.match(name):
        raise ValueError(
            f"{label} may only contain letters, apostrophes, hyphens and spaces, "
            "and must start with a letter."
        )
    return name


def parse_dob(value: Any) -> date:
    """Parse MM/DD/YYYY or YYYY-MM-DD into a real, non-future date on/after 1900-01-01."""
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = require_text(value, "Date of birth", 50)
        us, iso = _US_DATE_RE.match(text), _ISO_DATE_RE.match(text)
        if us:
            month, day, year = (int(g) for g in us.groups())
        elif iso:
            year, month, day = (int(g) for g in iso.groups())
        else:
            raise ValueError("Date of birth must be in MM/DD/YYYY or YYYY-MM-DD format.")
        try:
            parsed = date(year, month, day)
        except ValueError:
            raise ValueError("Date of birth is not a real calendar date.") from None
    if parsed > date.today():
        raise ValueError("Date of birth cannot be in the future.")
    if parsed < MIN_DOB:
        raise ValueError("Date of birth cannot be before 01/01/1900.")
    return parsed


def normalize_sex(value: Any) -> str:
    """Case-insensitive match to the sex enum, with common aliases."""
    text = require_text(value, "Sex", 50)
    key = re.sub(r"\s+", " ", text.lower())
    if key not in _SEX_ALIASES:
        raise ValueError(f"Sex must be one of: {', '.join(SEX_VALUES)}.")
    return _SEX_ALIASES[key]


def normalize_phone(value: Any, label: str = "Phone number") -> str:
    """Strip non-digits, drop a leading US country code, require a valid 10-digit NANP number."""
    text = require_text(value, label, 30)
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"{label} must have exactly 10 digits, including the area code.")
    if digits[0] in "01":
        raise ValueError(f"{label} has an invalid area code; it cannot start with 0 or 1.")
    if digits[3] in "01":
        raise ValueError(f"{label} is not valid; the three digits after the area code cannot start with 0 or 1.")
    return digits


def normalize_optional_phone(value: Any, label: str) -> str | None:
    """Like `normalize_phone`, but empty → None."""
    if clean_text(value, label) is None:
        return None
    return normalize_phone(value, label)


def normalize_email(value: Any) -> str | None:
    """Optional email; lowercase; syntax-checked (no DNS lookup)."""
    email = optional_text(value, "Email", 254)
    if email is None:
        return None
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise ValueError("Email is not a valid email address.") from None
    return email.lower()


def normalize_state(value: Any) -> str:
    """Accept a 2-letter code or full name (case-insensitive); return the uppercase code."""
    text = require_text(value, "State", 50)
    if text.upper() in STATES:
        return text.upper()
    key = re.sub(r"[.\s]+", " ", text.lower()).strip()
    if key in _STATE_NAMES:
        return _STATE_NAMES[key]
    raise ValueError("State must be a valid US state or territory, such as NY or New York.")


def validate_zip(value: Any) -> str:
    """5-digit ZIP or ZIP+4; 9 bare digits are formatted as 12345-6789."""
    text = require_text(value, "ZIP code", 10)
    if re.fullmatch(r"\d{9}", text):
        text = f"{text[:5]}-{text[5:]}"
    if not _ZIP_RE.match(text):
        raise ValueError("ZIP code must be 5 digits, or 5 digits plus 4 (12345-6789).")
    return text


def normalize_member_id(value: Any) -> str | None:
    """Optional insurance member ID: remove spaces/hyphens, alphanumeric, uppercase, ≤50."""
    text = clean_text(value, "Insurance member ID")
    if text is None:
        return None
    text = re.sub(r"[\s\-]", "", text)
    if not _MEMBER_ID_RE.match(text):
        raise ValueError("Insurance member ID may only contain letters and numbers (up to 50).")
    return text.upper()


def normalize_language(value: Any) -> str:
    """Optional preferred language, default English, title-cased, ≤50 chars."""
    text = optional_text(value, "Preferred language", 50)
    return text.title() if text else "English"
