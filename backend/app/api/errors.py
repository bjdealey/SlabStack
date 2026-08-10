"""Error handling.

Every failure leaves through the same envelope, so the UI has exactly one shape
to parse and one ``code`` to branch on:

    {"error": {"code": "not_found", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """Raised by routes and services. Carries the HTTP status with it."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFoundError(ApiError):
    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(
            code="not_found",
            message=f"{resource} '{identifier}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"resource": resource, "id": identifier},
        )


class ConflictError(ApiError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("conflict", message, status.HTTP_409_CONFLICT, details)


class NotImplementedYetError(ApiError):
    """A documented endpoint whose engine has not been built yet.

    Returned instead of a 404 so that the API contract is honest about what is
    coming and the UI can show "arrives in Phase N" rather than "broken".
    """

    def __init__(self, message: str, phase: int, planned_in: str) -> None:
        super().__init__(
            code="not_implemented",
            message=message,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            details={"phase": phase, "planned_in": planned_in},
        )


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_envelope(exc.code, exc.message, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = {}
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"] if part != "body")
            fields[location or "body"] = error["msg"]
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "validation_error", "The request body is not valid.", {"fields": fields}
            ),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        message = "That change conflicts with existing data."
        text = str(getattr(exc, "orig", exc))
        if "UNIQUE" in text.upper():
            message = "A record with those unique values already exists."
        elif "FOREIGN KEY" in text.upper():
            message = "That change refers to a record that does not exist."
        elif "CHECK" in text.upper():
            message = "A value is outside the range the database allows."
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_envelope("conflict", message, {"database": text[:500]}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(codes.get(exc.status_code, "http_error"), str(exc.detail)),
        )
