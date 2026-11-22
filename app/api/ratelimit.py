"""Request limits for v1 and v2 (docs/API.md, "Ограничение частоты").

Pure ASGI middleware. Every request under an API prefix (any method, including HEAD and
requests that would end in a 304) counts against a limiter; other paths (/, /health,
/health/sync, /docs, /openapi.json) are not limited. It sits inside the CORS middleware, so
browsers can read the 429.

Without an API key the limiter is ``app.state.rate_limiter`` and the key is the client
address (``scope["client"][0]``: uvicorn's ``--proxy-headers`` has already replaced it with
the X-Forwarded-For address when the request came from a trusted proxy). With a valid key
(stage 20, only when ``app.state.api_key_resolver`` exists) it is
``app.state.key_rate_limiter`` and the key is the account of the API key. The limiters are
read from ``app.state`` on every request (so tests may replace them or their clocks);
without ``rate_limiter``, nothing is limited.

ResponseSizeMiddleware additionally charges v1 responses the larger of: wire units (started
chunks of bytes sent to the client, after gzip) and raw units (started chunks of the
uncompressed body, reported by the caching layer via scope["mars.response_size"]), to the
same limiter and key as the request itself.
"""

import logging
import math
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.apikeys import (
    IDENTITY_SCOPE_KEY,
    UNKNOWN_CLIENT,
    ApiKeyResolver,
    RateIdentity,
    client_key,
    extract_api_key,
    rate_identity,
)
from app.api.caching import API_PREFIXES, RESPONSE_SIZE_SCOPE_KEY
from app.core.apikeys import is_our_key_format
from app.core.errors import (
    BUSY_RETRY_AFTER_SECONDS,
    DB_IO_ERRORS,
    LEGACY_SERVICE_BUSY_MESSAGE,
    NO_STORE,
    SERVICE_BUSY_MESSAGE,
    AccountBlocked,
    ApiKeyError,
    ApiKeyRevoked,
    InvalidApiKey,
    api_key_error_content,
    is_legacy_path,
    rate_limit_headers,
    rate_limited_content,
    rate_limited_headers,
)
from app.core.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

__all__ = [
    "CONCURRENT_RETRY_AFTER",
    "UNKNOWN_CLIENT",
    "RateLimitMiddleware",
    "ResponseSizeMiddleware",
    "client_key",
    "extra_hits",
    "is_limited_path",
    "response_cost",
]

# A failed key lookup that means database unavailable or busy (answered 503), not a bug.
_LOOKUP_UNAVAILABLE: tuple[type[BaseException], ...] = (
    DBAPIError,
    PoolTimeoutError,
    *DB_IO_ERRORS,
)

# Retry-After of a request rejected by the per-client concurrency limit.
CONCURRENT_RETRY_AFTER = 1.0

# Builds the answer to a request that is not served (a bad key); gets the X-RateLimit-*
# headers of the limit the request was counted against.
Rejection = Callable[[dict[str, str]], JSONResponse]


def is_limited_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)


class RateLimitMiddleware:
    """Counts every request against the limiter of whoever sent it.

    A request with a bad key is counted against the client address (a failed attempt costs
    the IP a request and one of its limited failures) and then answered with the error of
    its API version instead of being served. The identity is stored in
    ``scope["mars.rate_identity"]`` for the response size charge and the heavy-search gate.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        ip_limiter = _limiter(scope)
        if ip_limiter is None or not is_limited_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        ip = RateIdentity(kind="ip", key=client_key(scope))
        scope[IDENTITY_SCOPE_KEY] = ip
        # The address slot is taken before the key is looked up: a flood of requests with
        # unknown keys cannot start more lookups than the address may have requests in flight.
        if not ip_limiter.enter(ip.key):
            await self._too_many_in_flight(scope, receive, send, ip_limiter, ip)
            return
        held: tuple[RateLimiter, str] | None = (ip_limiter, ip.key)
        try:
            identity, rejection = await _identify(scope)
            if identity.kind == "account":
                # A valid key counts against its account: the address slot is given back.
                ip_limiter.leave(ip.key)
                held = None
                limiter = _pick(limiter_of(scope, identity), ip_limiter)
                scope[IDENTITY_SCOPE_KEY] = identity
                if not limiter.enter(identity.key):
                    await self._too_many_in_flight(scope, receive, send, limiter, identity)
                    return
                held = (limiter, identity.key)
            else:
                limiter = ip_limiter
            await self._serve(scope, receive, send, limiter, identity, rejection)
        finally:
            # The slot is held until the response is complete, also on errors and disconnects.
            if held is not None:
                held[0].leave(held[1])

    async def _too_many_in_flight(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        limiter: RateLimiter,
        identity: RateIdentity,
    ) -> None:
        """Too many requests in flight: rejected without counting in the windows."""
        state = limiter.peek(identity.key)
        extra = rate_limit_headers(state.limit, state.remaining, state.reset)
        logger.debug("Concurrency limited client=%s api=%s", identity.key, _api_label(scope))
        _count_usage(scope, identity, rejected=True)
        await _too_many_requests(scope, CONCURRENT_RETRY_AFTER, extra)(scope, receive, send)

    async def _serve(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        limiter: RateLimiter,
        identity: RateIdentity,
        rejection: Rejection | None,
    ) -> None:
        state = limiter.acquire(identity.key)
        extra = rate_limit_headers(state.limit, state.remaining, state.reset)
        if state.wait is not None:
            logger.debug("Rate limited client=%s api=%s", identity.key, _api_label(scope))
            _count_usage(scope, identity, rejected=True)
            await _too_many_requests(scope, state.wait, extra)(scope, receive, send)
        elif rejection is not None:
            await rejection(extra)(scope, receive, send)
        else:
            wrapped = _record_usage(scope, identity, _with_headers(send, extra))
            if _keys_active(scope):
                wrapped = _without_key_in_redirects(wrapped)
            await self.app(scope, receive, wrapped)


def limiter_of(scope: Scope, identity: RateIdentity) -> RateLimiter | None:
    """The limiter an identity counts against: the account limiter for a valid key, else the
    address limiter; None when the application has none."""
    state = scope["app"].state
    if identity.kind == "account":
        return getattr(state, "key_rate_limiter", None)
    return getattr(state, "rate_limiter", None)


def _pick(preferred: RateLimiter | None, fallback: RateLimiter) -> RateLimiter:
    """``preferred`` unless None (a limiter is falsy while it tracks no keys: no ``or``)."""
    return fallback if preferred is None else preferred


def _count_usage(scope: Scope, identity: RateIdentity, *, rejected: bool) -> None:
    usage = getattr(scope["app"].state, "api_key_usage", None)
    if usage is not None and identity.account_id is not None and identity.key_id is not None:
        usage.add(identity.account_id, identity.key_id, rejected=rejected)


def _record_usage(scope: Scope, identity: RateIdentity, send: Send) -> Send:
    """Wrap ``send`` so the answer is added to the usage counters of the key, once, when the
    response starts (a 429 produced further in counts as rejected)."""
    if identity.kind != "account":
        return send
    counted = False

    async def wrapped(message: Message) -> None:
        nonlocal counted
        if message["type"] == "http.response.start" and not counted:
            counted = True
            _count_usage(scope, identity, rejected=message["status"] == 429)
        await send(message)

    return wrapped


def _keys_active(scope: Scope) -> bool:
    state = scope["app"].state
    return (
        getattr(state, "api_key_resolver", None) is not None
        and getattr(state, "key_rate_limiter", None) is not None
    )


async def _identify(scope: Scope) -> tuple[RateIdentity, Rejection | None]:
    """Who the request counts as and, for a bad key, how to answer instead of serving it."""
    ip = RateIdentity(kind="ip", key=client_key(scope))
    if not _keys_active(scope):
        return ip, None
    state = scope["app"].state
    resolver: ApiKeyResolver = state.api_key_resolver
    value, _source = extract_api_key(scope, legacy=is_legacy_path(scope["path"]))
    if value is None:
        return ip, None
    # First of all: an address that failed too often is not served any key, whatever its
    # format, and the refusal is not counted as one more failure.
    wait = state.api_key_auth_limiter.check(ip.key)
    if wait is not None:
        return ip, lambda extra: _too_many_requests(scope, wait, extra)
    if not is_our_key_format(value):
        # Only a v2 header gets here (the extractor drops the other foreign values).
        return ip, _key_failure(scope, state, ip, InvalidApiKey())
    try:
        resolved = await resolver.resolve(value)
    except _LOOKUP_UNAVAILABLE as exc:
        logger.warning("API key lookup failed error=%s", type(exc).__name__)
        return ip, lambda extra: _service_busy(scope, extra)
    except Exception:
        logger.exception("API key lookup crashed")
        raise
    if resolved.status == "ok" and resolved.account_id is not None:
        key = f"a:{resolved.account_id}"
        return RateIdentity("account", key, resolved.account_id, resolved.key_id), None
    error: ApiKeyError = (
        ApiKeyRevoked()
        if resolved.status == "revoked"
        else AccountBlocked()
        if resolved.status == "blocked"
        else InvalidApiKey()
    )
    return ip, _key_failure(scope, state, ip, error)


def _key_failure(scope: Scope, state: Any, ip: RateIdentity, error: ApiKeyError) -> Rejection:
    """The answer to a bad key. Wrong and revoked keys also count as failures of the address
    (the key of a blocked account is genuine, so it does not)."""
    if not isinstance(error, AccountBlocked):
        _record_failure(state, ip.key)
    status, content = api_key_error_content(scope["path"], error)
    headers = {"Cache-Control": NO_STORE}
    return lambda extra: _json_response(scope, status, content, {**headers, **extra})


def _record_failure(state: Any, ip_key: str) -> None:
    """Count a failed key of the address; log once when it becomes locked out (the address
    only, never the key)."""
    limiter: RateLimiter = state.api_key_auth_limiter
    was_open = limiter.check(ip_key) is None
    limiter.record(ip_key)
    if was_open and limiter.check(ip_key) is not None:
        logger.warning(
            "API key failures locked out client=%s failures=%d",
            ip_key,
            state.settings.api_key_auth_max_failures,
        )


def _strip_api_key(location: str) -> str:
    """The location without any api_key query parameter (any case, encoded name too)."""
    base, question, rest = location.partition("?")
    if not question:
        return location
    query, hash_sign, fragment = rest.partition("#")
    kept = [p for p in query.split("&") if unquote(p.partition("=")[0]).lower() != "api_key"]
    return base + ("?" + "&".join(kept) if kept else "") + hash_sign + fragment


def _without_key_in_redirects(send: Send) -> Send:
    """Wrap send so a redirect (the router adds or removes a trailing slash and keeps the
    query) does not carry the api_key parameter in its Location."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start" and 300 <= message["status"] < 400:
            headers = MutableHeaders(scope=message)
            location = headers.get("location")
            if location is not None:
                headers["location"] = _strip_api_key(location)
        await send(message)

    return wrapped


def _service_busy(scope: Scope, extra: dict[str, str]) -> JSONResponse:
    """503 when the key could not be looked up (database busy or down)."""
    if is_legacy_path(scope["path"]):
        content: dict[str, Any] = {"errors": LEGACY_SERVICE_BUSY_MESSAGE}
    else:
        content = {
            "error": {"code": "service_busy", "message": SERVICE_BUSY_MESSAGE, "details": None}
        }
    headers = rate_limited_headers(BUSY_RETRY_AFTER_SECONDS)
    return _json_response(scope, 503, content, {**headers, **extra})


def _api_label(scope: Scope) -> str:
    """Log label for the API version; the raw client path is never logged."""
    return "v1" if is_legacy_path(scope["path"]) else "v2"


def _with_headers(send: Send, extra: dict[str, str]) -> Send:
    """Wrap ``send`` so the response start carries ``extra`` (added on the way out)."""

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            for name, value in extra.items():
                headers[name] = value
        await send(message)

    return wrapped


def _limiter(scope: Scope) -> RateLimiter | None:
    if scope["type"] != "http" or "app" not in scope:
        return None
    return getattr(scope["app"].state, "rate_limiter", None)


def _too_many_requests(scope: Scope, wait: float, extra: dict[str, str]) -> JSONResponse:
    headers = {**rate_limited_headers(wait), **extra}
    return _json_response(scope, 429, rate_limited_content(scope["path"]), headers)


def _json_response(
    scope: Scope, status: int, content: dict[str, Any], headers: dict[str, str]
) -> JSONResponse:
    response = JSONResponse(content, status_code=status, headers=headers)
    if scope["method"] == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response


def response_cost(wire: int, raw: int | None, unit: int, raw_unit: int) -> int:
    """Requests a v1 response costs: the larger of the started ``unit``-byte chunks on the wire
    and the started ``raw_unit``-byte chunks of the uncompressed body, at least one.
    ``raw`` is None when the uncompressed size is unknown (wire only)."""
    cost = max(1, math.ceil(wire / unit))
    if raw is not None:
        cost = max(cost, math.ceil(raw / raw_unit))
    return cost


def extra_hits(body_bytes: int, unit: int) -> int:
    """Requests to charge after the first one: started ``unit``-byte chunks minus one."""
    return response_cost(body_bytes, None, unit, unit) - 1


class ResponseSizeMiddleware:
    """Charges v1 responses by size (docs/API.md, "Размер ответов v1"): the larger of the wire
    units (compressed bytes sent) and the raw units (uncompressed body built by the server,
    reported by the caching layer via ``scope["mars.response_size"]``, also for HEAD and 304).

    Sits outside GZip. The units are read per request from ``app.state.v1_bytes_per_request`` and
    ``app.state.v1_raw_bytes_per_request`` (falling back to the settings). The extra hits are
    charged in ``finally``, also after a disconnect, with the bytes actually sent, to the
    limiter and key the request was counted against (``rate_identity``).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limiter = _limiter(scope)
        if limiter is None or not is_legacy_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        unit = _bytes_unit(scope)
        raw_unit = max(unit, _raw_bytes_unit(scope))
        # Shared with the caching middleware, which stores the uncompressed size under "raw".
        info: dict[str, int] = {}
        scope[RESPONSE_SIZE_SCOPE_KEY] = info
        sent = 0

        async def counting_send(message: Message) -> None:
            nonlocal sent
            # Counted before the send: bytes handed to the server are charged even if it fails.
            if message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, counting_send)
        finally:
            cost = response_cost(sent, info.get("raw"), unit, raw_unit)
            # Set by RateLimitMiddleware further in (the scope dict is shared).
            identity = rate_identity(scope)
            _pick(limiter_of(scope, identity), limiter).charge(identity.key, cost - 1)


def _raw_bytes_unit(scope: Scope) -> int:
    state = scope["app"].state
    unit = getattr(state, "v1_raw_bytes_per_request", None)
    if unit is None:
        unit = state.settings.rate_limit_v1_raw_bytes_per_request
    return max(1024, int(unit))


def _bytes_unit(scope: Scope) -> int:
    state = scope["app"].state
    unit = getattr(state, "v1_bytes_per_request", None)
    if unit is None:
        unit = state.settings.rate_limit_v1_bytes_per_request
    # Guarded so a bad value cannot raise inside ``finally`` and mask the original error.
    return max(1024, int(unit))
