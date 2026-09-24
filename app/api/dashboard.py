"""GET /dashboard — server-rendered HTML view of patients and their calls."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import as_utc, format_dob
from app.services import patient_service

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def format_phone(value: str | None) -> str:
    """Render 10 digits as (555) 123-4567."""
    if not value or len(value) != 10:
        return value or ""
    return f"({value[:3]}) {value[3:6]}-{value[6:]}"


templates.env.filters["phone"] = format_phone
templates.env.filters["dob"] = format_dob
templates.env.filters["utc"] = lambda dt: as_utc(dt).strftime("%Y-%m-%d %H:%M UTC") if dt else ""


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request, q: str | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    """Patient table with expandable details and linked call transcripts."""
    patients = patient_service.search_patients(db, q)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"patients": patients, "total": patient_service.count_patients(db), "q": q or ""},
    )
