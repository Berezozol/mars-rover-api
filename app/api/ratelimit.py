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

import logging

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.caching import API_PREFIXES
from app.core.errors import rate_limited_content, rate_limited_headers
from app.core.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

UNKNOWN_CLIENT = "unknown"


def client_key(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else UNKNOWN_CLIENT


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
        wait = limiter.hit(key)
        if wait is None:
            await self.app(scope, receive, send)
            return
        logger.debug("Rate limited client=%s path=%s", key, scope["path"])
        response = _too_many_requests(scope, wait)
        await response(scope, receive, send)


def _limiter(scope: Scope) -> RateLimiter | None:
    if scope["type"] != "http" or "app" not in scope:
        return None
    return getattr(scope["app"].state, "rate_limiter", None)


def _too_many_requests(scope: Scope, wait: float) -> JSONResponse:
    response = JSONResponse(
        rate_limited_content(scope["path"]),
        status_code=429,
        headers=rate_limited_headers(wait),
    )
    if scope["method"] == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response
