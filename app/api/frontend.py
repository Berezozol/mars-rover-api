"""Serving the Sol Index web interface build (docs/API.md, "Веб-интерфейс Sol Index").

``mount_frontend(app, dist_dir)`` hooks into the router's *not found* handler, so every real
route (API, health, docs, ``/developers``) keeps priority and keeps its own 405s. A request
that matches no route gets, in this order:

- ``/assets/<file>`` -> the build file (immutable cache) or the usual 404;
- a top-level file of ``dist`` (``favicon.svg``, ``robots.txt``) -> the file (1 hour);
- GET/HEAD of any other path outside the reserved prefixes -> ``index.html`` (no-cache);
- anything else (reserved prefixes such as unknown ``/api/...``, other methods, websockets)
  -> the router's original not-found handler: the JSON 404 of the path's API version.

Other methods on interface paths therefore get that JSON 404 (not 405). The middleware stack
is unchanged: caching/rate limiting only touch API prefixes, GZip compresses text types.
"""

import hashlib
import logging
import mimetypes
from pathlib import Path

import anyio
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

INDEX_NAME = "index.html"
ASSETS_PREFIX = "/assets/"
INDEX_CACHE_CONTROL = "no-cache"
ASSETS_CACHE_CONTROL = "public, max-age=31536000, immutable"
STATIC_CACHE_CONTROL = "public, max-age=3600"

# Paths owned by the API, health and docs: never answered with the interface.
RESERVED_PREFIXES: tuple[str, ...] = (
    "/api",
    "/mars-photos",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/developers",
)

# Do not depend on the OS registry (Windows may map .js to text/plain).
_CONTENT_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".map": "application/json",
    ".txt": "text/plain",
    ".woff2": "font/woff2",
}

_METHODS = frozenset({"GET", "HEAD"})


def is_reserved(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in RESERVED_PREFIXES)


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _CONTENT_TYPES:
        kind = _CONTENT_TYPES[suffix]
    else:
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"{kind}; charset=utf-8" if kind.startswith("text/") else kind


def _read_file(base: Path, relative: str) -> tuple[Path, bytes] | None:
    """Blocking: the file ``base/relative`` and its bytes if it is a regular file inside
    ``base``; None if it is missing, outside ``base`` or unreadable (e.g. a rebuild is running).
    """
    try:
        candidate = (base / relative).resolve()
        if candidate.is_relative_to(base) and candidate.is_file():
            return candidate, candidate.read_bytes()
    except (OSError, ValueError):
        pass
    return None


def _etag(body: bytes) -> str:
    return f'"{hashlib.sha256(body).hexdigest()[:32]}"'


def _not_modified(scope: Scope, etag: str) -> bool:
    values = Headers(scope=scope).getlist("if-none-match")
    return any(c.strip() in ("*", etag) for v in values for c in v.split(","))


def _build_response(scope: Scope, file: Path, body: bytes, cache_control: str) -> Response:
    # Plain bytes (not FileResponse) so that GZipMiddleware compresses it like any body.
    headers = {"Cache-Control": cache_control}
    if file.name.lower() == INDEX_NAME:
        # no-cache without a validator would refetch the page every time.
        headers["ETag"] = etag = _etag(body)
        if _not_modified(scope, etag):
            return Response(status_code=304, headers=headers)
    response = Response(body, media_type=_content_type(file), headers=headers)
    if scope["method"] == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response


async def _serve(scope: Scope, base: Path, relative: str, cache_control: str) -> Response | None:
    found = await anyio.to_thread.run_sync(_read_file, base, relative)
    if found is None:
        return None
    return _build_response(scope, found[0], found[1], cache_control)


class FrontendFallback:
    """ASGI app used as the router's ``default`` (see the module docstring)."""

    def __init__(self, dist_dir: Path, fallback: ASGIApp) -> None:
        self._dist = dist_dir.resolve()
        self._fallback = fallback

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = await self._respond(scope) if scope["type"] == "http" else None
        if response is None:
            await self._fallback(scope, receive, send)
            return
        await response(scope, receive, send)

    async def _respond(self, scope: Scope) -> Response | None:
        path: str = scope["path"]
        if scope["method"] not in _METHODS or is_reserved(path):
            return None
        if path.startswith(ASSETS_PREFIX):
            relative = path[len(ASSETS_PREFIX) :]
            return await _serve(scope, self._dist / "assets", relative, ASSETS_CACHE_CONTROL)
        top_level = path.lstrip("/")
        # Case-insensitive: on Windows /INDEX.HTML is the same file as index.html.
        if top_level and "/" not in top_level and top_level.lower() != INDEX_NAME:
            response = await _serve(scope, self._dist, top_level, STATIC_CACHE_CONTROL)
            if response is not None:
                return response
        response = await _serve(scope, self._dist, INDEX_NAME, INDEX_CACHE_CONTROL)
        if response is None:
            logger.warning("Web interface index.html is gone from %s", self._dist)
        return response


def has_frontend(dist_dir: Path) -> bool:
    return (dist_dir / INDEX_NAME).is_file()


def mount_frontend(app: FastAPI, dist_dir: Path) -> bool:
    """Serve the build in ``dist_dir`` at ``/``; False (and one log line) if there is none."""
    if not has_frontend(dist_dir):
        logger.info("Web interface build not found in %s: / serves the developer page", dist_dir)
        return False
    app.router.default = FrontendFallback(dist_dir, app.router.default)
    logger.info("Serving the web interface from %s", dist_dir)
    return True
