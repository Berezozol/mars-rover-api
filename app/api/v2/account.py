"""The caller's own account, found by its API key (docs/API.md, "Ключи API (этап 20B)").

``router`` (view, add or revoke a key, delete the account) is mounted with API_KEYS_ENABLED;
``deletion_router`` (deletion by a mailed link) also needs the sign-up settings.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.apikeys import (
    IDENTITY_SCOPE_KEY,
    ApiKeyResolver,
    RateIdentity,
    extract_api_key,
    rate_identity,
)
from app.api.v2.bodies import body_schema, read_json_body
from app.api.v2.key_schemas import (
    ConfirmDeletionIn,
    MailRequestIn,
    MailRequestOut,
    NewKeyOut,
    OwnAccountOut,
    new_key_out,
    own_account_out,
)
from app.api.v2.keys import REQUEST_ERRORS, queue_mail, signup_service
from app.api.v2.schemas import ErrorResponse
from app.core.errors import AccountBlocked, ApiKeyRequired, ApiKeyRevoked, InvalidApiKey
from app.db.session import get_session
from app.services.api_keys import MAX_ID, ApiKeyService

router = APIRouter(prefix="/account", tags=["account"])
deletion_router = APIRouter(prefix="/account", tags=["account"])

Session = Annotated[AsyncSession, Depends(get_session)]

_KEY_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorResponse,
        "description": "api_key_required / invalid_api_key / api_key_revoked",
    },
    403: {"model": ErrorResponse, "description": "account_blocked"},
}


async def _resolve_without_limiter(request: Request) -> RateIdentity:
    """The rate-limit middleware looks keys up; without it (RATE_LIMIT_ENABLED=false) the
    endpoint does it itself."""
    value, _source = extract_api_key(request.scope, legacy=False)
    resolver: ApiKeyResolver = request.app.state.api_key_resolver
    if value is None:
        raise ApiKeyRequired()
    resolved = await resolver.resolve(value)
    if resolved.status == "ok" and resolved.account_id is not None:
        return RateIdentity(
            "account", f"a:{resolved.account_id}", resolved.account_id, resolved.key_id
        )
    if resolved.status == "revoked":
        raise ApiKeyRevoked()
    if resolved.status == "blocked":
        raise AccountBlocked()
    raise InvalidApiKey()


async def current_account(request: Request) -> RateIdentity:
    """The account of the request's key; ``ApiKeyRequired`` without one. Bad keys were already
    answered by the rate-limit middleware."""
    if IDENTITY_SCOPE_KEY not in request.scope:
        return await _resolve_without_limiter(request)
    identity = rate_identity(request.scope)
    if identity.kind != "account" or identity.account_id is None:
        raise ApiKeyRequired()
    return identity


Caller = Annotated[RateIdentity, Depends(current_account)]


def _keys(request: Request) -> ApiKeyService:
    service: ApiKeyService = request.app.state.api_key_service
    return service


def _account_id(caller: RateIdentity) -> int:
    if caller.account_id is None:
        raise ApiKeyRequired()
    return caller.account_id


@router.get("", response_model=OwnAccountOut, responses=_KEY_ERRORS)
async def get_account(request: Request, session: Session, caller: Caller) -> OwnAccountOut:
    """Your e-mail, keys, usage and limits."""
    own = await _keys(request).get_own_account(session, _account_id(caller))
    return own_account_out(own, request.app.state.settings)


@router.post(
    "/keys",
    status_code=201,
    response_model=NewKeyOut,
    responses={**_KEY_ERRORS, 409: {"model": ErrorResponse, "description": "key_limit_reached"}},
)
async def add_key(request: Request, session: Session, caller: Caller) -> NewKeyOut:
    """Add a key to your account; it is shown only in this response."""
    return new_key_out(await _keys(request).add_own_key(session, _account_id(caller)))


@router.delete(
    "/keys/{key_id}",
    status_code=204,
    response_class=Response,
    responses={**_KEY_ERRORS, 404: {"model": ErrorResponse, "description": "not_found"}},
)
async def revoke_key(
    request: Request,
    session: Session,
    caller: Caller,
    key_id: Annotated[int, Path(ge=1, le=MAX_ID)],
) -> Response:
    """Revoke one of your keys (also the one that makes this request)."""
    await _keys(request).revoke_own_key(session, _account_id(caller), key_id)
    return Response(status_code=204)


@router.delete("", status_code=204, response_class=Response, responses=_KEY_ERRORS)
async def delete_account(request: Request, session: Session, caller: Caller) -> Response:
    """Delete your account, keys, counters and mail requests for good."""
    await _keys(request).delete_account(session, _account_id(caller))
    return Response(status_code=204)


@deletion_router.post(
    "/deletion-requests",
    status_code=202,
    response_model=MailRequestOut,
    responses=REQUEST_ERRORS,
    openapi_extra=body_schema(MailRequestIn),
)
async def request_deletion(request: Request, session: Session) -> MailRequestOut:
    """Ask for a deletion link by e-mail (for those who lost the key); the answer is the same
    for every valid address."""
    return await queue_mail(request, session, "delete")


@deletion_router.post(
    "/deletion-confirm",
    status_code=204,
    response_class=Response,
    responses={
        400: {"model": ErrorResponse, "description": "invalid_token"},
        413: {"model": ErrorResponse, "description": "payload_too_large"},
        415: {"model": ErrorResponse, "description": "unsupported_media_type"},
        422: {"model": ErrorResponse, "description": "validation_error"},
    },
    openapi_extra=body_schema(ConfirmDeletionIn),
)
async def confirm_deletion(request: Request, session: Session) -> Response:
    """Spend the deletion link: your data is deleted."""
    body = await read_json_body(request, ConfirmDeletionIn)
    await signup_service(request).confirm_delete(session, body.token)
    return Response(status_code=204)
