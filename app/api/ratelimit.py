"""Per-IP request limit for v1 and v2 (docs/API.md, "Ограничение частоты").

Pure ASGI middleware. Every request under an API prefix (any method, including HEAD and
requests that would end in a 304) counts against ``app.state.rate_limiter``; other paths
(/, /health, /health/sync, /docs, /openapi.json) are not limited. It sits inside the CORS
middleware, so browsers can read the 429.

The limiter is read from ``app.state.rate_limiter`` on every request (so tests may replace it
or its clock); without one, nothing is limited. The key is ``scope["client"][0]``: uvicorn's
``--proxy-headers`` has already replaced it with the X-Forwarded-For address when the request
came from a trusted proxy.
"""

import ipaddress
import logging

from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.caching import API_PREFIXES
from app.core.errors import rate_limit_headers, rate_limited_content, rate_limited_headers
from app.core.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

UNKNOWN_CLIENT = "unknown"
# Retry-After of a request rejected by the per-IP concurrency limit.
CONCURRENT_RETRY_AFTER = 1.0


def client_key(scope: Scope) -> str:
    """The client address; an IPv6 one is reduced to its /64 network (one subscriber owns a
    whole /64, so per-address keys would make the limit free to evade)."""
    client = scope.get("client")
    if not client:
        return UNKNOWN_CLIENT
    address = client[0]
    try:
        parsed = ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return address
    if parsed.version == 6:
        # IPv4-mapped addresses are IPv4 clients.
        mapped = parsed.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return address


def is_limited_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limiter = _limiter(scope)
        if limiter is None or not is_limited_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        key = client_key(scope)
        if not limiter.enter(key):
            # Too many requests in flight: rejected without counting in the windows.
            state = limiter.peek(key)
            extra = rate_limit_headers(state.limit, state.remaining, state.reset)
            logger.debug("Concurrency limited client=%s path=%s", key, scope["path"])
            await _too_many_requests(scope, CONCURRENT_RETRY_AFTER, extra)(scope, receive, send)
            return
        # The slot is held until the response is complete, also on errors and disconnects.
        try:
            state = limiter.acquire(key)
            extra = rate_limit_headers(state.limit, state.remaining, state.reset)
            if state.wait is None:
                await self.app(scope, receive, _with_headers(send, extra))
                return
            logger.debug("Rate limited client=%s path=%s", key, scope["path"])
            await _too_many_requests(scope, state.wait, extra)(scope, receive, send)
        finally:
            limiter.leave(key)


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
    response = JSONResponse(
        rate_limited_content(scope["path"]),
        status_code=429,
        headers={**rate_limited_headers(wait), **extra},
    )
    if scope["method"] == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response
