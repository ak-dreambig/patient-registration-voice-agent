# CLAUDE.md — Build Spec: Voice AI Patient Registration System

You are building the backend for a take-home assessment under a hard time limit.
Priorities, in order: (1) it works end-to-end, (2) clean separation of concerns,
(3) strict server-side validation, (4) clear docs. Do NOT over-engineer.
A simple system that never crashes beats an ambitious one.

## 1. What the system does

A caller dials a US phone number handled by **Vapi** (telephony + STT + LLM + TTS).
The Vapi assistant collects patient demographics conversationally and calls
**tools** (webhooks) on this backend to look up, create, or update patients.
The backend persists to **PostgreSQL** (Railway) and exposes a **REST API**
and a simple **HTML dashboard**.

```
Caller ⇄ Vapi (phone, STT, GPT-4o-mini, TTS)
            │  POST /vapi/webhook  (tool-calls, end-of-call-report)
            ▼
   FastAPI app  ── api/ (HTTP layer) ── services/ (business logic) ── models/db
            │
            ├─ /patients      REST CRUD (soft delete)
            ├─ /dashboard     HTML table of patients + call transcripts
            └─ /health
            ▼
   PostgreSQL (Railway)   | SQLite file fallback for local dev only
```

The voice layer and the REST API MUST call the same service layer
(`app/services/patient_service.py`). No business logic in route handlers.

## 2. Tech stack

- Python 3.11+, FastAPI, Uvicorn
- SQLAlchemy 2.0 (typed ORM), psycopg 3 (`psycopg[binary]`)
- Pydantic v2 + `pydantic-settings` + `email-validator`
- pytest + httpx (FastAPI TestClient)
- No Alembic: tables created with `Base.metadata.create_all()` on startup (documented trade-off).

## 3. Repository structure

```
app/
  __init__.py
  main.py              # app factory, routers, exception handlers, logging setup, startup create_all
  config.py            # Settings via pydantic-settings (env vars)
  db.py                # engine, SessionLocal, get_db dependency, URL normalization
  models.py            # SQLAlchemy models: Patient, CallLog
  schemas.py           # Pydantic request/response models (PatientCreate, PatientUpdate, PatientOut)
  validators.py        # pure functions: normalize_phone, normalize_state, validate_name, parse_dob, validate_zip
  responses.py         # envelope helpers: ok(data, status), fail(code, message, details, status)
  logging_config.py    # stdout logging, one JSON object per line
  services/
    __init__.py
    patient_service.py # create, get, list(filters), update(partial), soft_delete, find_by_phone
    call_log_service.py# upsert call log by vapi_call_id, link patient to call
  api/
    __init__.py
    patients.py        # REST routes
    vapi.py            # POST /vapi/webhook — tool-call dispatcher + end-of-call-report
    dashboard.py       # GET /dashboard (server-rendered HTML)
  templates/
    dashboard.html
agent/
  system_prompt.md     # PLACEHOLDER ONLY — will be written separately. Create file with a TODO line.
  tools.json           # PLACEHOLDER ONLY — will be written separately.
docs/
  architecture.md      # PLACEHOLDER — filled at the end
scripts/
  seed.py              # inserts 2 fictional demo patients if table empty
tests/
  conftest.py          # SQLite in-memory/test DB, TestClient fixture
  test_validators.py
  test_patients_api.py
  test_vapi_webhook.py
requirements.txt
Procfile               # web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
.env.example
README.md              # skeleton now; finalized at the end
```

## 4. Configuration (env vars)

| Var | Required | Notes |
|---|---|---|
| `DATABASE_URL` | prod yes | Railway gives `postgresql://...` → normalize to `postgresql+psycopg://...`. If unset, fall back to `sqlite:///./local.db` (local dev only). |
| `VAPI_SECRET` | optional | If set, `/vapi/webhook` requires header `x-vapi-secret` to match, else 401. |
| `LOG_LEVEL` | optional | default `INFO` |
| `SEED_DEMO_DATA` | optional | `true` → run seed on startup if table empty |

Never hardcode secrets. Provide `.env.example` with placeholders only.

## 5. Data model

### `patients`

| Column | Type | Constraints |
|---|---|---|
| patient_id | UUID (PG native; `Uuid` type in SQLAlchemy) | PK, default uuid4 |
| first_name | String(50) | NOT NULL |
| last_name | String(50) | NOT NULL, indexed |
| date_of_birth | Date | NOT NULL, indexed |
| sex | Enum(`Male`,`Female`,`Other`,`Decline to Answer`) named `sex_enum` | NOT NULL |
| phone_number | String(10) | NOT NULL, indexed, CHECK length = 10 |
| email | String(254) | nullable |
| address_line_1 | String(200) | NOT NULL |
| address_line_2 | String(100) | nullable |
| city | String(100) | NOT NULL |
| state | String(2) | NOT NULL, CHECK length = 2 |
| zip_code | String(10) | NOT NULL |
| insurance_provider | String(100) | nullable |
| insurance_member_id | String(50) | nullable |
| preferred_language | String(50) | NOT NULL, default `English` |
| emergency_contact_name | String(100) | nullable |
| emergency_contact_phone | String(10) | nullable |
| created_at | timestamptz | NOT NULL, default now() UTC |
| updated_at | timestamptz | NOT NULL, default now() UTC, set on every update |
| deleted_at | timestamptz | nullable (soft delete) |

Only use DB constraints that work on BOTH Postgres and SQLite (length checks,
NOT NULL, enum). Regex-level validation lives in Pydantic/validators.
`phone_number` is NOT unique (family members may share a phone); duplicate
detection is handled conversationally. Document this.

### `call_logs`

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| vapi_call_id | String(100) | unique, indexed |
| patient_id | UUID FK → patients, nullable | linked when a create/update tool succeeds in that call |
| transcript | Text | nullable |
| summary | Text | nullable |
| ended_reason | String(100) | nullable |
| final_payload | JSON | last payload sent to create/update |
| created_at / updated_at | timestamptz | |

## 6. Validation rules (in `validators.py`, reused by schemas)

Normalize first, then validate. Every error must name the field and say
exactly what is wrong in plain English (the voice agent reads these aloud).

- **first_name / last_name**: trim; 1–50 chars; regex `^[A-Za-z][A-Za-z' \-]*$` (letters, hyphens, apostrophes; internal spaces allowed for names like "Mary Ann" — documented deviation).
- **date_of_birth**: accept `MM/DD/YYYY` and `YYYY-MM-DD`; must be a real date; not in the future; not before 1900-01-01. API output format: `MM/DD/YYYY`.
- **sex**: case-insensitive match to enum; also map `m`→Male, `f`→Female, `prefer not to say`/`decline`→`Decline to Answer`.
- **phone_number / emergency_contact_phone**: strip non-digits; if 11 digits starting with `1`, drop the `1`; must be exactly 10 digits; area code and exchange must not start with 0 or 1 (NANP). Store 10 digits.
- **email**: optional; `EmailStr`; lowercase.
- **address_line_1**: trim, 1–200 chars. **address_line_2**: optional, ≤100.
- **city**: trim, 1–100 chars.
- **state**: accept 2-letter code OR full state name (case-insensitive) → store uppercase code. Valid set: 50 states + DC + PR, GU, VI, AS, MP.
- **zip_code**: `^\d{5}(-\d{4})?$`; also accept 9 bare digits → format as `12345-6789`.
- **insurance_member_id**: optional; remove spaces and hyphens; `^[A-Za-z0-9]{1,50}$`; uppercase.
- **preferred_language**: optional, default `English`, ≤50 chars, title-case.
- Empty strings for optional fields → `None`.

`PatientCreate`: all required fields required. `PatientUpdate`: every field
optional, but any provided field is validated with the same rules; required
fields cannot be set to null/empty.

## 7. REST API

All responses use the envelope: `{"data": <object|list|null>, "error": <null|object>}`.
Error object: `{"code": "validation_error"|"not_found"|"bad_request"|"internal_error", "message": str, "details": [{"field": str, "message": str}]}`.

| Method | Path | Behavior | Status |
|---|---|---|---|
| GET | `/health` | `{"data":{"status":"ok","db":"ok"}}` (runs `SELECT 1`) | 200 / 500 |
| GET | `/patients` | list non-deleted, newest first; filters `?last_name=` (case-insensitive exact), `?date_of_birth=` (either format), `?phone_number=` (normalized) | 200, 400 bad filter |
| GET | `/patients/{id}` | by UUID; deleted → 404 | 200, 400 invalid UUID, 404 |
| POST | `/patients` | create; returns full record with `patient_id` | 201, 422 |
| PUT | `/patients/{id}` | partial update; bumps `updated_at` | 200, 400, 404, 422 |
| DELETE | `/patients/{id}` | soft delete (set `deleted_at`); returns record | 200, 404 |
| GET | `/patients/{id}/calls` | call logs linked to the patient | 200, 404 |

- Override FastAPI's default 422 handler so validation errors use the envelope with per-field `details`.
- Malformed JSON → 400. Unhandled exception → 500 with envelope, logged with traceback, no stack trace in response.
- Basic sanitization: trim all strings, reject control characters, cap lengths.
- Enable CORS for GET (dashboard/demo convenience).

## 8. Vapi webhook — `POST /vapi/webhook`

Vapi sends `{"message": {...}}`. Dispatch on `message.type`:

### 8a. `tool-calls`
Payload shape (be defensive — handle both list keys):
```json
{"message": {
  "type": "tool-calls",
  "call": {"id": "call_abc"},
  "toolCallList": [
    {"id": "tc_1", "type": "function",
     "function": {"name": "create_patient", "arguments": {"first_name": "Jane"}}}
  ]
}}
```
- Tool calls may be under `toolCallList` or `toolWithToolCallList[].toolCall`. `arguments` may be a dict OR a JSON string — handle both.
- Respond **HTTP 200 always** (even on errors) with:
```json
{"results": [{"toolCallId": "tc_1", "result": "<string>"}]}
```
- `result` is a short plain-English string for the LLM, prefixed with a status token:
  - `SUCCESS: ...`, `FOUND: ...`, `NOT_FOUND: ...`, `VALIDATION_ERROR: <field>: <message>; <field>: <message>`, `SYSTEM_ERROR: The record could not be saved due to a technical problem.`
- Wrap each tool execution in try/except; a DB failure must produce `SYSTEM_ERROR`, never a 500 or a timeout. Keep handlers fast (<2s).

Tools to implement (names must match exactly):

1. **`find_patient_by_phone`** `{phone_number}` → search non-deleted patients by normalized phone.
   - Found: `FOUND: patient_id=<uuid>; first_name=<>; last_name=<>; date_of_birth=<MM/DD/YYYY>` (DOB is for the agent to verify identity; the agent must not read it aloud). If multiple, return the most recently updated.
   - Not found: `NOT_FOUND: No existing patient with that phone number.`
   - Invalid phone: `VALIDATION_ERROR: phone_number: ...`
2. **`create_patient`** `{all patient fields}` → `patient_service.create`.
   - `SUCCESS: Patient registered. patient_id=<uuid>; first_name=<>`
3. **`update_patient`** `{patient_id, ...partial fields}` → `patient_service.update`.
   - `SUCCESS: Patient updated. patient_id=<uuid>; first_name=<>`; unknown id → `NOT_FOUND: ...`

On create/update SUCCESS: link `patient_id` to the call log for `call.id` and store the payload as `final_payload`.
Log every tool call and result at INFO (one JSON line: call_id, tool, args, result status).

### 8b. `end-of-call-report`
Extract defensively (any may be missing): `message.call.id`, `message.endedReason`,
`message.artifact.transcript` (fallback `message.transcript`),
`message.analysis.summary` (fallback `message.summary`). Upsert into `call_logs`.
Log the final collected payload + ended reason to stdout. Return `{}` 200.
If a call ends before any successful save (dropped call), the log still records
the transcript with `patient_id = null` — this documents partial calls.

### 8c. Any other type (`status-update`, `speech-update`, etc.)
Return `{}` 200. Log at DEBUG.

### Auth
If `VAPI_SECRET` is set, compare header `x-vapi-secret` with `hmac.compare_digest`; mismatch → 401.

## 9. Dashboard — `GET /dashboard`

Single server-rendered HTML page (Jinja2 template, inline CSS, no JS framework):
- Header with system name, patient count, link to `/docs` (FastAPI Swagger).
- Table: name, DOB, sex, phone (formatted `(555) 123-4567`), city/state, insurance provider, created_at (UTC).
- Each row expands (`<details>`) to show all fields and linked call summaries/transcripts.
- Simple search form (`?q=`) filtering by last name or phone.
- `<meta http-equiv="refresh" content="15">` so reviewers see new calls appear.
- Clean, professional, readable; light theme; responsive.

## 10. Logging

`logging_config.py`: root logger → stdout, each record a single JSON line
(`ts`, `level`, `event`, plus extra fields). Events: `tool_call`, `patient_created`,
`patient_updated`, `patient_deleted`, `call_ended` (with final payload), `unhandled_error`.

## 11. Seed data

`scripts/seed.py` (also callable on startup when `SEED_DEMO_DATA=true`):
two clearly fictional patients (e.g. "Jane Doe", phone `2125550143`, and
"John Smith", phone `3125550188`). Only inserts if the table is empty.

## 12. Tests (keep fast, SQLite)

- `test_validators.py`: phone normalization (+1, dashes, 11-digit), 3-digit phone rejected, future DOB rejected, both DOB formats, state full name → code, invalid state, ZIP+4, 9-digit ZIP, name with apostrophe/hyphen, name with digits rejected.
- `test_patients_api.py`: create 201 + envelope; missing required 422 with field details; get by id; invalid UUID 400; filters work; partial update bumps updated_at; soft delete hides from list and returns 404 on get.
- `test_vapi_webhook.py`: create_patient via tool-call returns SUCCESS; invalid DOB returns VALIDATION_ERROR naming the field; find_patient_by_phone FOUND/NOT_FOUND; arguments as JSON string works; DB exception (monkeypatch service) returns SYSTEM_ERROR with HTTP 200; end-of-call-report stored; wrong secret → 401.

## 13. Build order (commit after each step)

1. Skeleton: structure, config, db, logging, `/health`, requirements, Procfile, `.env.example`.
2. Models + validators + schemas + `test_validators.py` passing.
3. Patient service + REST API + envelope/error handlers + `test_patients_api.py` passing.
4. Vapi webhook + call logs + `test_vapi_webhook.py` passing.
5. Dashboard + seed.
6. README skeleton (sections: Overview, Live Demo [TBD], Architecture, Tech Stack & Justification, Setup, Env Vars, API Reference, Voice Agent Design [TBD], Edge Cases Handled, Known Limitations & Trade-offs, Next Steps).

After each step, run `pytest -q` and show results. Keep code typed, small functions,
docstrings on public functions. Do not add features not listed here.
