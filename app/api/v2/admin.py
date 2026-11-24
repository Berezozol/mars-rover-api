"""v2 admin endpoints (docs/API.md, "Admin"); mounted only when ADMIN_TOKEN is set.

The token is checked before anything else, including the request body: a wrong token with
a malformed body is a 401. That is why the body is read and validated by hand.

Failed token checks are counted per client IP in ``app.state.admin_auth_limiter``; an IP over
the limit gets 429 without its token being compared (docs/API.md, "Ограничение частоты").
"""

import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.apikeys import client_key
from app.api.v2.key_schemas import KeyMailOut, key_mail_out
from app.api.v2.params import Pagination
from app.api.v2.schemas import (
    AdminAccountDetailOut,
    AdminAccountOut,
    AdminAccountsResponse,
    AdminKeyOut,
    BlockAccountIn,
    ErrorResponse,
    IssuedKeyOut,
    IssueKeyIn,
    RunsResponse,
    SyncRequestIn,
    SyncStartedOut,
    admin_account_detail_out,
    admin_account_out,
    admin_accounts_out,
    admin_key_out,
    issued_key_out,
    runs_out,
)
from app.core.config import Settings
from app.core.errors import RateLimited, UnauthorizedError
from app.core.ratelimit import RateLimiter
from app.db.session import get_session
from app.services import admin
from app.services.admin import SyncRequest
from app.services.api_keys import ApiKeyService
from app.services.key_mail import KeyMailOutbox

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
    # The same key as the rate limiter: IPv6 reduced to its /64.
    ip = client_key(request.scope)
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
    503: {"model": ErrorResponse, "description": "db_unavailable / service_busy"},
}

_SYNC_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": SyncRequestIn.model_json_schema()}},
    }
}


async def _read_body[B: BaseModel](
    request: Request, model: type[B], *, optional: bool = False
) -> B:
    """The validated JSON body; ``optional``: an empty body means ``{}``."""
    raw = await request.body()
    if optional and not raw.strip():
        raw = b"{}"
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        for error in errors:
            error["loc"] = ("body", *error["loc"])
        raise RequestValidationError(errors) from None


async def _read_sync_request(request: Request) -> SyncRequest:
    body = await _read_body(request, SyncRequestIn)
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


# Accounts and API keys (stage 20): mounted by create_app only with API_KEYS_ENABLED.
accounts_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

_ACCOUNT_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"model": ErrorResponse, "description": "not_found"},
}


def _body_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


def _service(request: Request) -> ApiKeyService:
    service: ApiKeyService = request.app.state.api_key_service
    return service


@accounts_router.get("/accounts", response_model=AdminAccountsResponse, responses=_ACCOUNT_ERRORS)
async def list_accounts(
    request: Request,
    session: Session,
    pagination: Pagination,
    q: Annotated[
        str | None,
        Query(
            max_length=254,
            description="Part of the e-mail (case-insensitive) or the prefix of a key (si_...).",
        ),
    ] = None,
    status: Annotated[Literal["active", "blocked"] | None, Query()] = None,
) -> AdminAccountsResponse:
    """Accounts, newest first."""
    return admin_accounts_out(await _service(request).list_accounts(session, q, status, pagination))


@accounts_router.get(
    "/accounts/{account_id}", response_model=AdminAccountDetailOut, responses=_ACCOUNT_ERRORS
)
async def get_account(request: Request, session: Session, account_id: int) -> AdminAccountDetailOut:
    """One account with its keys and the usage of the last 30 days."""
    return admin_account_detail_out(await _service(request).get_account(session, account_id))


@accounts_router.post(
    "/accounts",
    status_code=201,
    response_model=IssuedKeyOut,
    responses={
        **_ACCOUNT_ERRORS,
        409: {"model": ErrorResponse, "description": "key_limit_reached"},
    },
    openapi_extra=_body_schema(IssueKeyIn),
)
async def issue_key(request: Request, session: Session) -> IssuedKeyOut:
    """Issue a key by hand (no e-mail); the key is shown only in this response."""
    body = await _read_body(request, IssueKeyIn)
    return issued_key_out(await _service(request).issue_key(session, body.email, body.note))


@accounts_router.post(
    "/accounts/{account_id}/block",
    response_model=AdminAccountOut,
    responses=_ACCOUNT_ERRORS,
    openapi_extra=_body_schema(BlockAccountIn),
)
async def block_account(request: Request, session: Session, account_id: int) -> AdminAccountOut:
    """Block an account: all its keys answer 403 account_blocked."""
    body = await _read_body(request, BlockAccountIn, optional=True)
    return admin_account_out(await _service(request).block(session, account_id, body.reason))


@accounts_router.post(
    "/accounts/{account_id}/unblock", response_model=AdminAccountOut, responses=_ACCOUNT_ERRORS
)
async def unblock_account(request: Request, session: Session, account_id: int) -> AdminAccountOut:
    """Unblock an account."""
    return admin_account_out(await _service(request).unblock(session, account_id))


@accounts_router.post(
    "/keys/{key_id}/revoke", response_model=AdminKeyOut, responses=_ACCOUNT_ERRORS
)
async def revoke_key(request: Request, session: Session, key_id: int) -> AdminKeyOut:
    """Revoke a key; its requests answer 401 api_key_revoked at once."""
    return admin_key_out(await _service(request).revoke_key(session, key_id, "admin"))


@accounts_router.delete(
    "/accounts/{account_id}", status_code=204, response_class=Response, responses=_ACCOUNT_ERRORS
)
async def delete_account(request: Request, session: Session, account_id: int) -> Response:
    """Delete an account with its keys and counters for good."""
    await _service(request).delete_account(session, account_id)
    return Response(status_code=204)


@accounts_router.get("/key-mail", response_model=KeyMailOut, responses=_ERRORS)
async def key_mail_status(request: Request) -> KeyMailOut:
    """State of the key mail queue (no addresses)."""
    outbox: KeyMailOutbox = request.app.state.key_mail_outbox
    return key_mail_out(await outbox.status())
