"""Helpers that build the standard `{"data", "error"}` response envelope."""

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def ok(data: Any, status: int = 200) -> JSONResponse:
    """Return a success envelope."""
    return JSONResponse(status_code=status, content={"data": jsonable_encoder(data), "error": None})


def fail(
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
    status: int = 400,
) -> JSONResponse:
    """Return an error envelope. `details` is a list of `{"field", "message"}` objects."""
    error = {"code": code, "message": message, "details": details or []}
    return JSONResponse(status_code=status, content={"data": None, "error": error})
