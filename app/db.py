"""Database engine, session factory and FastAPI dependency."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

SQLITE_FALLBACK_URL = "sqlite:///./local.db"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def normalize_database_url(url: str | None) -> str:
    """Map Railway/Heroku-style URLs to the psycopg 3 driver; fall back to local SQLite."""
    if not url:
        return SQLITE_FALLBACK_URL
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def build_engine(url: str) -> Engine:
    """Create an engine with sensible defaults for the given backend."""
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            # One shared connection so every session sees the same in-memory DB (tests).
            return create_engine(url, connect_args=connect_args, poolclass=StaticPool)
        return create_engine(url, connect_args=connect_args)
    # Short timeouts so a slow DB yields a fast SYSTEM_ERROR instead of a Vapi tool timeout.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )


engine = build_engine(normalize_database_url(get_settings().database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Yield a database session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
