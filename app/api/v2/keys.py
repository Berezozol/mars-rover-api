"""Key requests and link confirmation (docs/API.md, "Ключи API (этап 20B)").

Mounted by ``create_app`` only with API_KEYS_ENABLED, KEY_SIGNUP_ENABLED and SMTP_HOST.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.apikeys import client_key
from app.api.v2.bodies import body_schema, read_json_body
from app.api.v2.key_schemas import (
    ConfirmKeyIn,
    MailRequestIn,
    MailRequestOut,
    confirmed_key_out,
)
from app.api.v2.schemas import ErrorResponse, IssuedKeyOut
from app.core.errors import RateLimited
from app.db.session import get_session
from app.services.key_signup import KeySignupService

router = APIRouter(prefix="/keys", tags=["keys"])

Session = Annotated[AsyncSession, Depends(get_session)]

_COMMON: dict[int | str, dict[str, Any]] = {
    413: {"model": ErrorResponse, "description": "payload_too_large"},
    415: {"model": ErrorResponse, "description": "unsupported_media_type"},
    422: {"model": ErrorResponse, "description": "validation_error"},
}
REQUEST_ERRORS: dict[int | str, dict[str, Any]] = {
    **_COMMON,
    429: {"model": ErrorResponse, "description": "rate_limited"},
    503: {"model": ErrorResponse, "description": "mail_unavailable"},
}


def signup_service(request: Request) -> KeySignupService:
    service: KeySignupService = request.app.state.key_signup_service
    return service


async def queue_mail(request: Request, session: AsyncSession, purpose: str) -> MailRequestOut:
    """The shared flow of ``/keys/requests`` and ``/account/deletion-requests``: the answer
    does not depend on the address."""
    body = await read_json_body(request, MailRequestIn)
    wait = request.app.state.signup_limiter.hit(client_key(request.scope))
    if wait is not None:
        raise RateLimited(wait)
    outcome = await signup_service(request).request(session, body.email, purpose, body.website)
    outbox = request.app.state.key_mail_outbox
    if outcome.queued:
        outbox.wake()
    return MailRequestOut(mail_delayed=outbox.degraded)


@router.post(
    "/requests",
    status_code=202,
    response_model=MailRequestOut,
    responses=REQUEST_ERRORS,
    openapi_extra=body_schema(MailRequestIn),
)
async def request_key(request: Request, session: Session) -> MailRequestOut:
    """Ask for a key: a one-time link is mailed to the address (the answer is the same for
    every valid address)."""
    return await queue_mail(request, session, "key")


@router.post(
    "/confirm",
    status_code=201,
    response_model=IssuedKeyOut,
    responses={
        **_COMMON,
        400: {"model": ErrorResponse, "description": "invalid_token"},
        403: {"model": ErrorResponse, "description": "account_blocked"},
        409: {"model": ErrorResponse, "description": "key_limit_reached"},
    },
    openapi_extra=body_schema(ConfirmKeyIn),
)
async def confirm_key(request: Request, session: Session) -> IssuedKeyOut:
    """Spend the link and get the key; it is shown only in this response."""
    body = await read_json_body(request, ConfirmKeyIn)
    issued = await signup_service(request).confirm_key(session, body.token, body.revoke_existing)
    return confirmed_key_out(issued)
