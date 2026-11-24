"""ETag, conditional GET and Cache-Control for v1 and v2 (docs/API.md, "Кэширование").

Pure ASGI middleware. For GET/HEAD under the API prefixes:
- HEAD is answered by the GET route with the same headers and no body;
- 200: ``ETag`` (weak, a hash of the body) and ``Cache-Control`` by route; a matching
  ``If-None-Match`` turns the response into a 304 without a body; when the route sets
  ``Last-Modified`` and the request has no ``If-None-Match``, an ``If-Modified-Since`` not
  earlier than it does the same (an unparsable date is ignored);
- 3xx: left as the route set it (the image redirect sends its own Cache-Control);
- 4xx/5xx: ``Cache-Control: no-store``.
Admin and key endpoint responses (any method) always get ``no-store`` and no ETag. Other
paths (/health, /docs) are untouched.

Compression: GZipMiddleware sits outside this middleware, so the ETag is computed on the
uncompressed body and is the same for gzip and identity responses (a weak validator: the
representations are semantically equal). ``If-None-Match`` matches either way and the 304
(no body) passes GZip untouched. ``VaryAcceptEncodingMiddleware`` (outermost) adds
``Vary: Accept-Encoding``. A HEAD response carries the identity ``Content-Length`` and no
``Content-Encoding`` (its empty body is not compressed).
"""

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_PREFIXES: tuple[str, ...] = ("/api/v1", "/mars-photos/api/v1", "/api/v2")
ADMIN_PREFIX = "/api/v2/admin"
# Never cached, any method and status: admin and the key endpoints (stage 20B).
NO_STORE_PREFIXES: tuple[str, ...] = (ADMIN_PREFIX, "/api/v2/keys", "/api/v2/account")
# Scope key of a dict the response-size middleware shares with this one: "raw" gets the length
# of the uncompressed body of a 200 (also for HEAD and 304).
RESPONSE_SIZE_SCOPE_KEY = "mars.response_size"

LIST_CACHE_CONTROL = "public, max-age=300"
DETAIL_CACHE_CONTROL = "public, max-age=3600"
IMAGE_CACHE_CONTROL = "public, max-age=86400"
# Rover positions change at most once a day (stage 11); same lifetime as a detail.
LOCATION_CACHE_CONTROL = "public, max-age=3600"
# RSS feed of new photos (stage 16): the sync runs every few hours, a sol is committed on its own.
FEED_CACHE_CONTROL = "public, max-age=900"
NO_STORE = "no-store"

_LOCATION_SEGMENTS = frozenset({"location", "traverse"})
_LIST_PHOTO_SEGMENTS = frozenset({"stats", "batch"})
_CACHED_METHODS = frozenset({"GET", "HEAD"})
# Headers that describe the body; a 304 does not carry them.
_BODY_HEADERS = ("content-length", "content-type", "content-encoding")


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _api_prefix(path: str) -> str | None:
    return next((prefix for prefix in API_PREFIXES if _under(path, prefix)), None)


def cache_control_for(path: str) -> str:
    """Cache-Control of a 200 response under an API prefix."""
    prefix = _api_prefix(path) or ""
    parts = path[len(prefix) :].split("/")
    # "/photos/{id}" -> ["", "photos", "{id}"]; "/photos/stats" and "/photos/batch" are lists.
    if len(parts) == 3 and parts[1] == "photos" and parts[2] in _LIST_PHOTO_SEGMENTS:
        return LIST_CACHE_CONTROL
    if len(parts) == 3 and parts[1] == "photos" and parts[2]:
        return DETAIL_CACHE_CONTROL
    # "/photos/{id}/image" answered 200: a converted image (the redirect is a 302).
    if len(parts) == 4 and parts[1] == "photos" and parts[2] and parts[3] == "image":
        return IMAGE_CACHE_CONTROL
    # "/rovers/{rover}/location" and "/rovers/{rover}/traverse"
    if len(parts) == 4 and parts[1] == "rovers" and parts[2] and parts[3] in _LOCATION_SEGMENTS:
        return LOCATION_CACHE_CONTROL
    # "/rovers/{rover}/samples" and "/rovers/{rover}/samples/{number}" (stage 13)
    if len(parts) in (4, 5) and parts[1] == "rovers" and parts[2] and parts[3] == "samples":
        return LOCATION_CACHE_CONTROL
    # "/rovers/{rover}/radiation" (stage 21): changes only when a PDS release is loaded
    if len(parts) == 4 and parts[1] == "rovers" and parts[2] and parts[3] == "radiation":
        return LOCATION_CACHE_CONTROL
    # "/rovers/{rover}/weather" and "/rovers/{rover}/weather/{sol}" (stage 17): change only when
    # a parse or an import is run
    if len(parts) in (4, 5) and parts[1] == "rovers" and parts[2] and parts[3] == "weather":
        return LOCATION_CACHE_CONTROL
    # "/rovers/{rover}/feed.xml" (stage 16)
    if len(parts) == 4 and parts[1] == "rovers" and parts[2] and parts[3] == "feed.xml":
        return FEED_CACHE_CONTROL
    return LIST_CACHE_CONTROL


def _http_date(value: str | None) -> datetime | None:
    """Parse an HTTP-date (UTC); None when absent or unparsable."""
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def not_modified_since(if_modified_since: str | None, last_modified: str | None) -> bool:
    """True when ``If-Modified-Since`` is not earlier than ``Last-Modified`` (to the second).

    HTTP dates have second granularity, so entries stored within the same second as the client's
    date can be missed; ``If-None-Match`` (ETag of the body) is the exact path.
    """
    since, modified = _http_date(if_modified_since), _http_date(last_modified)
    return since is not None and modified is not None and since >= modified


def make_etag(body: bytes) -> str:
    return f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'


def _opaque(tag: str) -> str:
    """Weak comparison: the tag without the W/ prefix."""
    tag = tag.strip()
    return tag[2:] if tag.startswith("W/") else tag


def if_none_match_matches(header_values: Iterable[str], etag: str) -> bool:
    """True if any If-None-Match value is "*" or weakly equals ``etag``."""
    wanted = _opaque(etag)
    for value in header_values:
        for candidate in value.split(","):
            candidate = candidate.strip()
            if candidate == "*" or (candidate and _opaque(candidate) == wanted):
                return True
    return False


class CachingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if any(_under(path, prefix) for prefix in NO_STORE_PREFIXES):
            # Every method, every status.
            await self.app(scope, receive, _no_store_sender(send))
            return
        if scope["method"] not in _CACHED_METHODS or _api_prefix(path) is None:
            await self.app(scope, receive, send)
            return
        await _CachedResponse(scope, send).run(self.app, receive)


class VaryAcceptEncodingMiddleware:
    """Outermost: every API response carries ``Vary: Accept-Encoding`` exactly once.

    GZipMiddleware adds it only to the bodies it compresses; 304s, HEAD responses and
    small bodies need it too, so shared caches keep gzip and identity copies apart.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _api_prefix(scope["path"]) is None:
            await self.app(scope, receive, send)
            return

        async def sender(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                tokens = {
                    token.strip().lower()
                    for value in headers.getlist("vary")
                    for token in value.split(",")
                }
                if "accept-encoding" not in tokens and "*" not in tokens:
                    headers.add_vary_header("Accept-Encoding")
            await send(message)

        await self.app(scope, receive, sender)


def _as_get(scope: Scope) -> Scope:
    """HEAD is served by the GET route (the routes declare GET only); the body is dropped."""
    return {**scope, "method": "GET"} if scope["method"] == "HEAD" else scope


def _no_store_sender(send: Send) -> Send:
    async def sender(message: Message) -> None:
        if message["type"] == "http.response.start":
            MutableHeaders(scope=message)["Cache-Control"] = NO_STORE
        await send(message)

    return sender


def _is_not_modified(request_headers: Headers, etag: str, last_modified: str | None) -> bool:
    """If-None-Match decides when present; otherwise If-Modified-Since (RFC 9110, 13.1.3)."""
    none_match = request_headers.getlist("if-none-match")
    if none_match:
        return if_none_match_matches(none_match, etag)
    return not_modified_since(request_headers.get("if-modified-since"), last_modified)


class _CachedResponse:
    """Buffers a 200 to hash it; other statuses stream through with their headers fixed.

    HEAD runs the GET route and gets the same status and headers (ETag and Content-Length
    of the GET body, 304 on a match) with an empty body.
    """

    def __init__(self, scope: Scope, send: Send) -> None:
        self._head = scope["method"] == "HEAD"
        self._scope = _as_get(scope)
        self._send = send
        self._start: Message | None = None
        self._buffering = False
        self._chunks: list[bytes] = []

    async def run(self, app: ASGIApp, receive: Receive) -> None:
        await app(self._scope, receive, self._on_message)

    async def _on_message(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            await self._on_start(message)
        elif message["type"] == "http.response.body" and self._buffering:
            self._chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                await self._flush()
        elif message["type"] == "http.response.body" and self._head:
            await self._send({**message, "body": b""})
        else:
            await self._send(message)

    async def _on_start(self, message: Message) -> None:
        status: int = message["status"]
        headers = MutableHeaders(scope=message)
        if status == 200:
            self._start = message
            self._buffering = True
            return
        if status >= 400:
            headers["Cache-Control"] = NO_STORE
        await self._send(message)

    async def _flush(self) -> None:
        if self._start is None:
            return
        body = b"".join(self._chunks)
        info = self._scope.get(RESPONSE_SIZE_SCOPE_KEY)
        if info is not None:
            # Uncompressed size, before a 304 or HEAD drops the body (charged by size).
            info["raw"] = len(body)
        etag = make_etag(body)
        headers = MutableHeaders(scope=self._start)
        headers["ETag"] = etag
        headers["Cache-Control"] = cache_control_for(self._scope["path"])
        request_headers = Headers(scope=self._scope)
        if _is_not_modified(request_headers, etag, headers.get("last-modified")):
            for name in _BODY_HEADERS:
                del headers[name]
            self._start["status"] = 304
            body = b""
        if self._head:
            body = b""
        await self._send(self._start)
        await self._send({"type": "http.response.body", "body": body, "more_body": False})
