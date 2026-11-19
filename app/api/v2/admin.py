"""v2 admin endpoints (docs/API.md, "Admin"); mounted only when ADMIN_TOKEN is set.

The token is checked before anything else, including the request body: a wrong token with
a malformed body is a 401. That is why the body is read and validated by hand.

Failed token checks are counted per client IP in ``app.state.admin_auth_limiter``; an IP over
the limit gets 429 without its token being compared (docs/API.md, "Ограничение частоты").
"""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyHeader
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.params import Pagination
from app.api.v2.schemas import (
    ErrorResponse,
    RunsResponse,
    SyncRequestIn,
    SyncStartedOut,
    runs_out,
)
from app.core.config import Settings
from app.core.errors import RateLimited, UnauthorizedError
from app.core.ratelimit import RateLimiter
from app.db.session import get_session
from app.services import admin
from app.services.admin import SyncRequest

ADMIN_TOKEN_HEADER = "X-Admin-Token"

_token_header = APIKeyHeader(
    name=ADMIN_TOKEN_HEADER,
    auto_error=False,
    description="Admin token (ADMIN_TOKEN setting).",
)


def admin_auth_limiter(settings: Settings) -> RateLimiter:
    """Failed admin logins per IP: ADMIN_AUTH_MAX_FAILURES per ADMIN_AUTH_WINDOW_MINUTES."""
    window = settings.admin_auth_window_minutes * 60
    return RateLimiter([(settings.admin_auth_max_failures, window)])


async def require_admin(
    request: Request, token: Annotated[str | None, Security(_token_header)]
) -> None:
    """429 for an IP with too many failures, else 401 unless the header equals ADMIN_TOKEN."""
    limiter: RateLimiter = request.app.state.admin_auth_limiter
    ip = request.client.host if request.client else "unknown"
    wait = limiter.check(ip)
    if wait is not None:
        raise RateLimited(wait)
    if not _token_matches(request.app.state.settings, token):
        limiter.record(ip)
        raise UnauthorizedError(f"Missing or invalid {ADMIN_TOKEN_HEADER}")


def _token_matches(settings: Settings, token: str | None) -> bool:
    """Constant-time comparison with ADMIN_TOKEN."""
    expected = settings.admin_token_value
    if not expected or not token:
        return False
    return secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

Session = Annotated[AsyncSession, Depends(get_session)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "unauthorized"},
    404: {"model": ErrorResponse, "description": "rover_not_found"},
    422: {"model": ErrorResponse, "description": "validation_error"},
    429: {"model": ErrorResponse, "description": "rate_limited"},
    503: {"model": ErrorResponse, "description": "db_unavailable"},
}

_SYNC_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": SyncRequestIn.model_json_schema()}},
    }
}


async def _read_sync_request(request: Request) -> SyncRequest:
    raw = await request.body()
    try:
        body = SyncRequestIn.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        for error in errors:
            error["loc"] = ("body", *error["loc"])
        raise RequestValidationError(errors) from None
    return SyncRequest(rover=body.rover, from_sol=body.from_sol, to_sol=body.to_sol)


@router.post(
    "/sync",
    status_code=202,
    response_model=SyncStartedOut,
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "sync_in_progress"}},
    openapi_extra=_SYNC_BODY,
)
async def start_sync(request: Request, session: Session) -> SyncStartedOut:
    """Start a sync of one rover; without from_sol/to_sol it is incremental."""
    body = await _read_sync_request(request)
    state = request.app.state
    handle = await admin.start_sync(
        session, state.sync_service, state.adapter_factory, state.sync_tasks, body
    )
    return SyncStartedOut(run_id=handle.run_id, status="running")


@router.get("/sync/runs", response_model=RunsResponse, responses=_ERRORS)
async def list_runs(
    session: Session,
    pagination: Pagination,
    rover: Annotated[str | None, Query(description="Rover slug, case-insensitive.")] = None,
) -> RunsResponse:
    """Sync runs, newest first."""
    return runs_out(await admin.list_runs(session, rover, pagination))
