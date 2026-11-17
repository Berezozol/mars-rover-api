"""Domain errors and the HTTP error format.

Two formats live side by side:
- v1 (legacy, see docs/LEGACY-API.md): {"errors": "<message>"}
- v2 (see docs/API.md): {"error": {"code": ..., "message": ..., "details": ...}}

The format is chosen by the request path, so services raise the same exceptions
for both API versions.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

LEGACY_PREFIXES: tuple[str, ...] = ("/api/v1", "/mars-photos/api/v1")


class AppError(Exception):
    """Base class for errors that map to a client-facing HTTP response."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: Any = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


class SyncInProgress(ConflictError):
    """A sync of this rover is already running (the advisory lock is held)."""

    code = "sync_in_progress"

    def __init__(self, rover_id: str) -> None:
        super().__init__(f"Sync of rover {rover_id} is already in progress")
        self.rover_id = rover_id


class NoSourceForRover(ValidationAppError):
    """The rover has no upstream source that can be synced (e.g. Spirit, Opportunity)."""

    def __init__(self, rover_id: str) -> None:
        super().__init__(f"Rover {rover_id} has no upstream source to sync")
        self.rover_id = rover_id


def is_legacy_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in LEGACY_PREFIXES)


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    if is_legacy_path(request.url.path):
        return JSONResponse({"errors": message}, status_code=status_code)
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details}},
        status_code=status_code,
    )


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    details = []
    for error in exc.errors():
        # loc looks like ("query", "sol") or ("body", "rover"); drop the location kind.
        field = ".".join(str(part) for part in error.get("loc", ())[1:]) or "request"
        details.append({"field": field, "message": str(error.get("msg", "Invalid value"))})
    return details


async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def _handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        code, message = "not_found", "Not found"
    elif exc.status_code == 405:
        code, message = "method_not_allowed", "Method not allowed"
    else:
        code, message = "http_error", str(exc.detail)
    return error_response(request, status_code=exc.status_code, code=code, message=message)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = _validation_details(exc)
    if is_legacy_path(request.url.path):
        # The legacy API answered bad input with 400 and a short message.
        field = details[0]["field"] if details else "request"
        return JSONResponse({"errors": f"Invalid {field}"}, status_code=400)
    return error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=details,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error path=%s", request.url.path)
    return error_response(
        request,
        status_code=500,
        code="internal_error",
        message="Internal server error",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)
