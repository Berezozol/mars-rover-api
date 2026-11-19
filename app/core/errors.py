"""Domain errors and the HTTP error format.

Two formats live side by side:
- v1 (legacy, see docs/LEGACY-API.md): {"errors": "<message>"}
- v2 (see docs/API.md): {"error": {"code": ..., "message": ..., "details": ...}}

The format is chosen by the request path, so services raise the same exceptions
for both API versions.
"""

import logging
import math
from typing import Any

from asyncpg.exceptions import PostgresConnectionError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

LEGACY_PREFIXES: tuple[str, ...] = ("/api/v1", "/mars-photos/api/v1")

VALIDATION_MESSAGE = "Request validation failed"
RATE_LIMITED_MESSAGE = "Rate limit exceeded"
DB_UNAVAILABLE_MESSAGE = "Database unavailable"

# Echoed user input in error messages is cut to this length.
_MAX_ECHO = 64

# Errors meaning "the database cannot be reached", not "the query is wrong": SQLAlchemy's
# connectivity errors and server-side connection rejections. Handled globally.
_DB_UNAVAILABLE_TYPES: tuple[type[BaseException], ...] = (
    OperationalError,
    InterfaceError,
    PostgresConnectionError,
)

# Raw I/O errors asyncpg raises while connecting (refused / reset sockets, the connect
# timeout). They mean "database unavailable" only when raised by database code, so they are
# NOT handled globally: database call sites convert them with ``DatabaseUnavailable``
# (see app/db/session.py). Anywhere else they stay a 500.
DB_IO_ERRORS: tuple[type[BaseException], ...] = (
    OSError,  # includes ConnectionError
    TimeoutError,  # asyncio.TimeoutError is an alias since Python 3.11
)

NO_STORE = "no-store"


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


class DatabaseUnavailable(ServiceUnavailableError):
    """The database could not be reached (raised in place of a raw I/O error from DB code)."""

    code = "db_unavailable"

    def __init__(self) -> None:
        super().__init__(DB_UNAVAILABLE_MESSAGE)


class UpstreamUnavailable(AppError):
    """502: the source (NASA PDS) failed or returned data that cannot be converted."""

    status_code = 502
    code = "upstream_error"

    def __init__(self) -> None:
        super().__init__("Upstream source unavailable or returned unreadable data")


class UpstreamTimeout(AppError):
    """504: converting a source image took longer than MER_CONVERT_TIMEOUT_SECONDS."""

    status_code = 504
    code = "upstream_timeout"

    def __init__(self) -> None:
        super().__init__("Image conversion timed out")


class ConverterBusy(ServiceUnavailableError):
    """503: the image converter has no free slot and its queue is full."""

    code = "converter_busy"
    retry_after = 10

    def __init__(self) -> None:
        super().__init__("Image converter is busy, try again later")


class SyncInProgress(ConflictError):
    """A sync of this rover is already running (the advisory lock is held)."""

    code = "sync_in_progress"

    def __init__(self, rover_id: str) -> None:
        super().__init__(f"Sync of rover {rover_id} is already in progress")
        self.rover_id = rover_id


class NoSourceForRover(ValidationAppError):
    """The rover has no upstream source that can be synced (e.g. Spirit, Opportunity)."""

    def __init__(self, rover_id: str) -> None:
        message = f"Rover {rover_id[:_MAX_ECHO]} has no upstream source to sync"
        super().__init__(message, details=[{"field": "rover", "message": message}])
        self.rover_id = rover_id


class RateLimited(AppError):
    """429: too many requests (or failed admin logins) from one client IP."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after: float) -> None:
        super().__init__(RATE_LIMITED_MESSAGE)
        self.retry_after = retry_after


def retry_after_value(seconds: float) -> str:
    """``Retry-After`` header value: whole seconds, rounded up, at least 1."""
    return str(max(1, math.ceil(seconds)))


def rate_limited_headers(seconds: float) -> dict[str, str]:
    return {"Retry-After": retry_after_value(seconds), "Cache-Control": NO_STORE}


def rate_limited_content(path: str) -> dict[str, Any]:
    """The 429 body in the format of the path's API version."""
    if is_legacy_path(path):
        return {"errors": RATE_LIMITED_MESSAGE}
    return {"error": {"code": RateLimited.code, "message": RATE_LIMITED_MESSAGE, "details": None}}


class LegacyBadRequest(AppError):
    """v1 bad request: 400 with the message as the legacy "errors" text."""

    status_code = 400
    code = "bad_request"


class InvalidRoverName(LegacyBadRequest):
    def __init__(self) -> None:
        super().__init__("Invalid Rover Name")


class LegacyParamError(LegacyBadRequest):
    """A v1 query parameter has an invalid value: {"errors": "Invalid <param>"}."""

    def __init__(self, param: str) -> None:
        super().__init__(f"Invalid {param}")
        self.param = param


class PhotoNotFound(NotFoundError):
    code = "photo_not_found"

    def __init__(self) -> None:
        super().__init__("Photo not found")


class RoverNotFound(NotFoundError):
    """v2: unknown or malformed rover slug."""

    code = "rover_not_found"

    def __init__(self, slug: str) -> None:
        super().__init__(f"Rover '{slug[:_MAX_ECHO].lower()}' not found")
        self.slug = slug


class InvalidParam(ValidationAppError):
    """v2: a parameter failed a check made by a service (shaped like request validation)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(VALIDATION_MESSAGE, details=[{"field": field, "message": message}])
        self.field = field


def is_legacy_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in LEGACY_PREFIXES)


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    if is_legacy_path(request.url.path):
        return JSONResponse({"errors": message}, status_code=status_code, headers=headers)
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details}},
        status_code=status_code,
        headers=headers,
    )


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    details = []
    for error in exc.errors():
        # loc looks like ("query", "sol") or ("body", "rover"); drop the location kind.
        field = ".".join(str(part) for part in error.get("loc", ())[1:]) or "request"
        message = str(error.get("msg", "Invalid value"))
        if error.get("type") == "value_error":
            # Our validators raise ValueError; pydantic prefixes its message.
            message = message.removeprefix("Value error, ")
        details.append({"field": field, "message": message})
    return details


def is_db_unavailable(exc: BaseException) -> bool:
    """True for database connectivity failures; SQL, integrity and other errors are False."""
    if isinstance(exc, _DB_UNAVAILABLE_TYPES):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    headers = None
    if isinstance(exc, RateLimited | ConverterBusy):
        headers = rate_limited_headers(exc.retry_after)
    return error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=headers,
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
        message=VALIDATION_MESSAGE,
        details=details,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error path=%s", request.url.path)
    # Rendered by the outermost ServerErrorMiddleware, outside the caching middleware.
    return error_response(
        request,
        status_code=500,
        code="internal_error",
        message="Internal server error",
        headers={"Cache-Control": NO_STORE},
    )


async def _handle_db_error(request: Request, exc: Exception) -> JSONResponse:
    """503 db_unavailable for connectivity failures; any other DB error stays a 500."""
    if not is_db_unavailable(exc):
        return await _handle_unexpected_error(request, exc)
    logger.warning("Database unavailable path=%s error=%s", request.url.path, type(exc).__name__)
    return error_response(
        request,
        status_code=503,
        code="db_unavailable",
        message=DB_UNAVAILABLE_MESSAGE,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    # DBAPIError covers OperationalError/InterfaceError; the handler checks connection_invalidated.
    for exc_type in (DBAPIError, *_DB_UNAVAILABLE_TYPES):
        app.add_exception_handler(exc_type, _handle_db_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
