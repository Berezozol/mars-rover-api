"""Shared HTTP client for upstream feeds: timeouts, retries, rate limit, User-Agent.

Every request to a NASA/JPL source must go through ``UpstreamClient``.
"""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

import httpx
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt

from app.core.config import Settings

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]

MAX_BACKOFF_SECONDS = 30.0
RETRYABLE_STATUSES = frozenset({429})
RETRY_AFTER_STATUSES = frozenset({429, 503})
MAX_REDIRECTS = 5


class UpstreamError(Exception):
    """An upstream request failed for good (after retries or with a non-retryable error)."""

    def __init__(self, url: str, status: int | None = None, message: str | None = None) -> None:
        self.url = url
        self.status = status
        detail = message or (f"HTTP {status}" if status is not None else "request failed")
        super().__init__(f"Upstream error for {url}: {detail}")


class ResponseRejected(UpstreamError):
    """The response is refused for what it is, not for a failure of the server: a host or a
    redirect outside the allowed origin, too many redirects, a body over the size cap. Never
    worth a retry."""


class PageLimitExceeded(UpstreamError):
    """More pages remain after ``upstream_max_pages``; the incomplete sol is not yielded."""


class IncompleteResponse(UpstreamError):
    """The upstream returned fewer items than it announced; the incomplete sol is not yielded."""


class SolsFetchFailed(UpstreamError):
    """A refetch failed; ``sols`` may be incomplete even if they were yielded already."""

    def __init__(self, cause: UpstreamError, sols: Iterable[int]) -> None:
        self.sols = frozenset(sols)
        listed = ", ".join(str(sol) for sol in sorted(self.sols))
        super().__init__(cause.url, cause.status, f"refetch of sols {listed} failed: {cause}")


@dataclass
class RequestStats:
    """Counters of the HTTP requests a client sent (redirect hops and retries count too):
    ``gets`` full GETs, ``probes`` ``Range`` requests, ``conditional`` conditional listing
    requests; ``bytes_received`` is the size of the bodies read; ``slowest_wait`` the longest
    wait (seconds) from sending a request to its response headers."""

    gets: int = 0
    probes: int = 0
    conditional: int = 0
    bytes_received: int = 0
    slowest_wait: float = 0.0

    def snapshot(self) -> "RequestStats":
        return RequestStats(
            self.gets, self.probes, self.conditional, self.bytes_received, self.slowest_wait
        )


SLOW_HEADERS_SECONDS = 10.0


@dataclass(frozen=True)
class ConditionalBody:
    """Result of a conditional GET: ``304`` (no body) or ``200`` with the body and validators."""

    not_modified: bool
    body: bytes | None
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class TailProbe:
    """Result of ``probe_tail``: ``206`` carries the full size and the tail of the body;
    ``200`` (``Range`` not honoured) carries the size from ``Content-Length`` (or ``None``)
    and no tail."""

    status: int
    total_size: int | None
    tail: bytes | None


class ChunkSink[T](Protocol):
    """Receives a streamed body chunk by chunk; ``result()`` is called once, at the end."""

    def feed(self, chunk: bytes) -> None: ...

    def result(self) -> T: ...


class _RetryableStatusError(Exception):
    """Internal marker for a response status worth retrying (429, 5xx)."""

    def __init__(self, status: int, retry_after: float | None = None) -> None:
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}")


def _parse_retry_after(value: str | None) -> float | None:
    """Parse the seconds form of Retry-After; the HTTP-date form is ignored."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class RateLimiter:
    """Spaces requests at least ``1 / max_rps`` seconds apart.

    The spacing holds for the scheduled grant times: each acquisition is granted at
    ``max(now, previous grant + interval)``. A caller that resumes late after its grant
    (event-loop delay) may send closer to the next request than the interval.
    """

    def __init__(
        self,
        max_rps: float,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        min_interval: float | None = None,
    ):
        # With ``min_interval`` the pause runs from the end of a response (``release``).
        if min_interval is not None:
            self._interval = min_interval
        else:
            self._interval = 1.0 / max_rps if max_rps > 0 else 0.0
        self._from_end = min_interval is not None
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            now = self._clock()
            target = now if self._last is None else max(now, self._last + self._interval)
            if target > now:
                await self._sleep(target - now)
            self._last = target

    def release(self) -> None:
        """A request ended: with ``min_interval`` the next one waits the pause from now."""
        if self._from_end:
            self._last = self._clock()


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("Content-Length")
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    port = url.port or {"https": 443, "http": 80}.get(url.scheme)
    return url.scheme, (url.host or "").lower(), port


def _check_host(url: str, target: str, allowed_origin: str | None) -> None:
    """Only https, and only the host and effective port of ``allowed_origin``."""
    if allowed_origin is None:
        return
    scheme, host, port = _origin(httpx.URL(target))
    _, allowed_origin, allowed_port = _origin(httpx.URL(allowed_origin))
    if scheme != "https" or host != allowed_origin or port != allowed_port:
        raise ResponseRejected(url, None, "host is not allowed")


async def _collect(
    url: str, response: httpx.Response, max_bytes: int, stats: RequestStats | None = None
) -> bytes:
    """The body of a streamed response, capped at ``max_bytes`` and checked for completeness."""
    expected = _content_length(response)
    if expected is not None and expected > max_bytes:
        raise ResponseRejected(url, response.status_code, "response is too large")
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_raw():
        received += len(chunk)
        if stats is not None:
            stats.bytes_received += len(chunk)
        if received > max_bytes:
            raise ResponseRejected(url, response.status_code, "response is too large")
        chunks.append(chunk)
    if expected is not None and received != expected:
        raise IncompleteResponse(
            url, response.status_code, f"got {received} bytes, Content-Length {expected}"
        )
    return b"".join(chunks)


_CONTENT_RANGE_RE = re.compile(r"bytes\s+\d+-\d+/(\d+)\s*$")


def _content_range_total(value: str | None) -> int | None:
    """The full size from ``Content-Range: bytes a-b/total``; ``None`` if absent or ``*``."""
    match = _CONTENT_RANGE_RE.match(value.strip()) if value else None
    return int(match.group(1)) if match else None


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TransportError | _RetryableStatusError)


def _is_download_retryable(exc: BaseException) -> bool:
    """A truncated download is retried too (a JSON page short of its total is not)."""
    return _is_retryable(exc) or isinstance(exc, IncompleteResponse)


class UpstreamClient:
    """Async JSON client for upstream feeds; use as ``async with UpstreamClient(settings)``."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        max_retries: int | None = None,
        min_interval: float | None = None,
        timeout: float | None = None,
    ) -> None:
        """``max_retries``, ``min_interval`` (seconds from the end of a response to the next
        request, instead of ``UPSTREAM_MAX_RPS``) and ``timeout`` (httpx timeout, seconds)
        override the settings for this client; by default the settings apply."""
        self._sleep = sleep
        self._clock = clock
        self.stats = RequestStats()
        retries = settings.upstream_max_retries if max_retries is None else max_retries
        self._max_retries = max(retries, 0)
        self._retry_base = settings.upstream_retry_base_seconds
        self._limiter = RateLimiter(
            settings.upstream_max_rps, clock=clock, sleep=sleep, min_interval=min_interval
        )
        self._http = httpx.AsyncClient(
            timeout=settings.upstream_timeout_seconds if timeout is None else timeout,
            headers={"User-Agent": settings.upstream_user_agent},
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        """GET ``url`` and decode JSON; raises ``UpstreamError`` on any final failure."""
        response = await self._with_retries(url, lambda: self._send(url, params))
        return self._decode(url, response)

    async def download(self, url: str, dest: Path) -> int:
        """Stream ``url`` into ``dest`` and return the number of bytes written.

        The body goes to ``dest`` + ``.part`` first and is renamed only when complete; a body
        shorter or longer than its ``Content-Length`` is an ``IncompleteResponse``, retried
        like a transport error. Same retries, rate limit and User-Agent as ``get_json``;
        raises ``UpstreamError`` on any final failure (the ``.part`` file is then removed).
        """
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = await self._with_retries(
                url, lambda: self._stream_to(url, part), retryable=_is_download_retryable
            )
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(dest)
        return size

    async def get_bytes(
        self, url: str, *, max_bytes: int, allowed_origin: str | None = None
    ) -> bytes:
        """GET ``url`` into memory (at most ``max_bytes``); ``UpstreamError`` on final failure.

        Same retries, rate limit and User-Agent as ``get_json``; a body shorter or longer than
        its ``Content-Length`` is an ``IncompleteResponse`` (retried), one over ``max_bytes``
        a plain ``UpstreamError``. With ``allowed_origin`` (a URL) set, the first URL and every
        redirect target must be https on its host and port (redirects are followed here).
        """
        return await self._with_retries(
            url,
            lambda: self._read_bytes(url, max_bytes, allowed_origin),
            retryable=_is_download_retryable,
        )

    async def stream_into[T](
        self,
        url: str,
        sink_factory: Callable[[], ChunkSink[T]],
        *,
        max_bytes: int,
        allowed_origin: str | None = None,
        max_retries: int | None = None,
    ) -> T:
        """GET ``url`` and feed the body, chunk by chunk, to a sink; nothing is written to disk.

        Every attempt builds a fresh sink with ``sink_factory`` (a retry starts over) and the
        return value is ``sink.result()`` of the successful one. The body is capped at
        ``max_bytes`` (``UpstreamError``); a body shorter or longer than its ``Content-Length``
        is an ``IncompleteResponse``, retried like a transport error. Host check, retries
        (``max_retries`` overrides the client's), rate limit and User-Agent as in ``get_bytes``.
        Errors raised by the sink itself are not retried.
        """
        return await self._with_retries(
            url,
            lambda: self._stream_sink(url, sink_factory, max_bytes, allowed_origin),
            retryable=_is_download_retryable,
            max_retries=max_retries,
        )

    async def probe_tail(
        self,
        url: str,
        n: int,
        *,
        allowed_origin: str | None = None,
        max_retries: int | None = None,
    ) -> TailProbe:
        """Ask for the last ``n`` bytes (``Range: bytes=-n``) without downloading the file.

        ``206``: the full size from ``Content-Range`` and the tail (at most ``n`` bytes).
        ``200``: the server ignored ``Range``; the size comes from ``Content-Length`` (or is
        ``None``), the tail is ``None`` and the body is not read (the connection is closed).
        Other statuses, host check, retries and rate limit as in ``stream_into``.
        """
        return await self._with_retries(
            url,
            lambda: self._probe(url, n, allowed_origin),
            retryable=_is_download_retryable,
            max_retries=max_retries,
        )

    @asynccontextmanager
    async def _open(
        self,
        url: str,
        allowed_origin: str | None,
        headers: Mapping[str, str],
        kind: str = "gets",
    ) -> AsyncIterator[httpx.Response]:
        """A streamed GET with redirects checked against ``allowed_origin`` by hand; ``kind``
        is the ``RequestStats`` counter the request is counted in."""
        current = url
        try:
            for _ in range(MAX_REDIRECTS + 1):
                _check_host(url, current, allowed_origin)
                await self._limiter.acquire()
                follow = allowed_origin is None
                setattr(self.stats, kind, getattr(self.stats, kind) + 1)
                started = self._clock()
                async with self._http.stream(
                    "GET", current, headers=headers, follow_redirects=follow
                ) as response:
                    self._note_wait(current, self._clock() - started)
                    if not follow and response.is_redirect and "location" in response.headers:
                        current = str(httpx.URL(current).join(response.headers["location"]))
                        continue
                    yield response
                    return
            raise ResponseRejected(url, None, "too many redirects")
        finally:
            self._limiter.release()

    def _note_wait(self, url: str, waited: float) -> None:
        self.stats.slowest_wait = max(self.stats.slowest_wait, waited)
        if waited >= SLOW_HEADERS_SECONDS:
            logger.info("Slow response from %s: headers after %.1f s", url, waited)

    async def _stream_sink[T](
        self,
        url: str,
        sink_factory: Callable[[], ChunkSink[T]],
        max_bytes: int,
        allowed_origin: str | None,
    ) -> T:
        async with self._open(url, allowed_origin, {"Accept-Encoding": "identity"}) as response:
            self._check_status(url, response)
            if response.status_code != 200:
                raise UpstreamError(url, response.status_code, "unexpected status")
            expected = _content_length(response)
            if expected is not None and expected > max_bytes:
                raise ResponseRejected(url, response.status_code, "response is too large")
            sink = sink_factory()
            received = 0
            async for chunk in response.aiter_raw():
                received += len(chunk)
                self.stats.bytes_received += len(chunk)
                if received > max_bytes:
                    raise ResponseRejected(url, response.status_code, "response is too large")
                sink.feed(chunk)
            if expected is not None and received != expected:
                raise IncompleteResponse(
                    url, response.status_code, f"got {received} bytes, Content-Length {expected}"
                )
            return sink.result()

    async def _probe(self, url: str, n: int, allowed_origin: str | None) -> TailProbe:
        headers = {"Accept-Encoding": "identity", "Range": f"bytes=-{n}"}
        async with self._open(url, allowed_origin, headers, "probes") as response:
            self._check_status(url, response)
            if response.status_code == 200:
                return TailProbe(200, _content_length(response), None)
            if response.status_code != 206:
                raise UpstreamError(url, response.status_code, "unexpected status")
            total = _content_range_total(response.headers.get("Content-Range"))
            return TailProbe(206, total, await _collect(url, response, n, self.stats))

    async def _with_retries[T](
        self,
        url: str,
        call: Callable[[], Awaitable[T]],
        *,
        retryable: Callable[[BaseException], bool] = _is_retryable,
        max_retries: int | None = None,
    ) -> T:
        retries = self._max_retries if max_retries is None else max(max_retries, 0)
        retrying = AsyncRetrying(
            sleep=self._sleep,
            stop=stop_after_attempt(retries + 1),
            wait=self._backoff,
            retry=retry_if_exception(retryable),
            before_sleep=lambda state: self._log_retry(url, state),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    result = await call()
        except _RetryableStatusError as exc:
            raise UpstreamError(url, exc.status) from exc
        except httpx.HTTPError as exc:
            # Transport errors after the last retry, and non-retryable ones
            # such as TooManyRedirects or DecodingError.
            raise UpstreamError(url, None, f"{type(exc).__name__}: {exc}") from exc
        return result

    async def _send(self, url: str, params: Mapping[str, str | int] | None) -> httpx.Response:
        await self._limiter.acquire()
        response = await self._http.get(url, params=dict(params) if params else None)
        self._check_status(url, response)
        return response

    @staticmethod
    def _check_status(url: str, response: httpx.Response) -> None:
        status = response.status_code
        if status in RETRYABLE_STATUSES or status >= 500:
            retry_after = None
            if status in RETRY_AFTER_STATUSES:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise _RetryableStatusError(status, retry_after)
        if status >= 400:
            raise UpstreamError(url, status)

    async def _stream_to(self, url: str, part: Path) -> int:
        await self._limiter.acquire()
        # Identity encoding: the bytes on disk are the file itself and match Content-Length.
        headers = {"Accept-Encoding": "identity"}
        async with self._http.stream("GET", url, headers=headers) as response:
            self._check_status(url, response)
            expected = _content_length(response)
            written = 0
            with part.open("wb") as file:
                async for chunk in response.aiter_raw():
                    file.write(chunk)
                    written += len(chunk)
        if expected is not None and written != expected:
            raise IncompleteResponse(
                url, response.status_code, f"got {written} bytes, Content-Length {expected}"
            )
        return written

    async def get_conditional(
        self,
        url: str,
        *,
        max_bytes: int,
        allowed_origin: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> ConditionalBody:
        """Conditional GET into memory: ``If-None-Match`` / ``If-Modified-Since`` are sent only
        when given. ``304`` returns ``not_modified`` without reading a body; ``200`` returns
        the body and the response's ``ETag`` / ``Last-Modified``. Everything else (retries,
        rate limit, User-Agent, size cap, host check) is as in ``get_bytes``.
        """
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return await self._with_retries(
            url,
            lambda: self._read_response(url, max_bytes, allowed_origin, headers, "conditional"),
            retryable=_is_download_retryable,
        )

    async def _read_bytes(self, url: str, max_bytes: int, allowed_origin: str | None) -> bytes:
        result = await self._read_response(url, max_bytes, allowed_origin, {})
        if result.body is None:
            raise UpstreamError(url, 304, "unexpected 304 for an unconditional request")
        return result.body

    async def _read_response(
        self,
        url: str,
        max_bytes: int,
        allowed_origin: str | None,
        extra_headers: Mapping[str, str],
        kind: str = "gets",
    ) -> ConditionalBody:
        headers = {"Accept-Encoding": "identity", **extra_headers}
        async with self._open(url, allowed_origin, headers, kind) as response:
            self._check_status(url, response)
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
            if response.status_code == 304:
                return ConditionalBody(True, None, etag, last_modified)
            if response.status_code != 200:
                raise UpstreamError(url, response.status_code, "unexpected status")
            body = await _collect(url, response, max_bytes, self.stats)
            return ConditionalBody(False, body, etag, last_modified)

    def _backoff(self, state: RetryCallState) -> float:
        retry_index = state.attempt_number - 1
        wait = self._retry_base * 2**retry_index
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, _RetryableStatusError) and exc.retry_after is not None:
            wait = max(wait, exc.retry_after)
        return min(wait, MAX_BACKOFF_SECONDS)

    @staticmethod
    def _decode(url: str, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(url, response.status_code, "invalid JSON") from exc

    @staticmethod
    def _log_retry(url: str, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        wait = state.next_action.sleep if state.next_action else 0.0
        logger.warning(
            "Upstream request to %s failed (attempt %d): %r; retrying in %.1f s",
            url,
            state.attempt_number,
            exc,
            wait,
        )
