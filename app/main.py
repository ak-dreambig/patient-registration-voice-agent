"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.api import patients, vapi
from app.config import get_settings
from app.db import Base, engine, get_db
from app.logging_config import configure_logging
from app.responses import fail, ok
from app.schemas import error_details
from app.services.patient_service import EmptyUpdateError, InvalidFilterError, PatientNotFoundError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup (no migrations; see README trade-offs)."""
    Base.metadata.create_all(bind=engine)
    yield


def register_exception_handlers(app: FastAPI) -> None:
    """Map every error to the standard envelope."""

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        if any(e.get("type") == "json_invalid" for e in errors):
            return fail("bad_request", "Request body is not valid JSON.", status=400)
        if any(tuple(e.get("loc", ())) == ("body",) for e in errors):
            return fail("bad_request", "Request body must be a JSON object.", status=400)
        return fail("validation_error", "One or more fields are invalid.", error_details(exc), status=422)

    @app.exception_handler(PatientNotFoundError)
    async def _not_found(_: Request, exc: PatientNotFoundError) -> JSONResponse:
        return fail("not_found", str(exc), status=404)

    @app.exception_handler(InvalidFilterError)
    async def _bad_filter(_: Request, exc: InvalidFilterError) -> JSONResponse:
        return fail("bad_request", str(exc), [{"field": exc.field, "message": str(exc)}], status=400)

    @app.exception_handler(EmptyUpdateError)
    async def _empty_update(_: Request, exc: EmptyUpdateError) -> JSONResponse:
        return fail("bad_request", str(exc), status=400)

    @app.exception_handler(patients.InvalidIdError)
    async def _bad_id(_: Request, exc: patients.InvalidIdError) -> JSONResponse:
        return patients.invalid_id_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "bad_request"
        return fail(code, str(exc.detail), status=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", exc_info=exc, extra={"path": request.url.path})
        return fail("internal_error", "An unexpected error occurred.", status=500)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging(get_settings().log_level)
    app = FastAPI(title="Patient Registration Voice Agent", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
    register_exception_handlers(app)
    app.include_router(patients.router)
    app.include_router(vapi.router)

    @app.get("/health", tags=["health"])
    def health(db: Session = Depends(get_db)) -> JSONResponse:
        """Liveness + database connectivity check."""
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            logger.exception("health_check_failed")
            return fail("internal_error", "Database unavailable.", status=500)
        return ok({"status": "ok", "db": "ok"})

    return app


app = create_app()
