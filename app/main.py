"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base, engine, get_db
from app.logging_config import configure_logging
from app.responses import fail, ok

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup (no migrations; see README trade-offs)."""
    Base.metadata.create_all(bind=engine)
    yield


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging(get_settings().log_level)
    app = FastAPI(title="Patient Registration Voice Agent", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

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
