"""Shared HTTP client for upstream feeds: timeouts, retries, rate limit, User-Agent.

Every request to a NASA/JPL source must go through ``UpstreamClient``.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Self

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
        self, max_rps: float, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep
    ):
        self._interval = 1.0 / max_rps if max_rps > 0 else 0.0
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
        raise UpstreamError(url, None, "host is not allowed")


async def _collect(url: str, response: httpx.Response, max_bytes: int) -> bytes:
    """The body of a streamed response, capped at ``max_bytes`` and checked for completeness."""
    expected = _content_length(response)
    if expected is not None and expected > max_bytes:
        raise UpstreamError(url, response.status_code, "response is too large")
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_raw():
        received += len(chunk)
        if received > max_bytes:
            raise UpstreamError(url, response.status_code, "response is too large")
        chunks.append(chunk)
    if expected is not None and received != expected:
        raise IncompleteResponse(
            url, response.status_code, f"got {received} bytes, Content-Length {expected}"
        )
    return b"".join(chunks)


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
    ) -> None:
        self._sleep = sleep
        self._max_retries = max(settings.upstream_max_retries, 0)
        self._retry_base = settings.upstream_retry_base_seconds
        self._limiter = RateLimiter(settings.upstream_max_rps, clock=clock, sleep=sleep)
        self._http = httpx.AsyncClient(
            timeout=settings.upstream_timeout_seconds,
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

    async def _with_retries[T](
        self,
        url: str,
        call: Callable[[], Awaitable[T]],
        *,
        retryable: Callable[[BaseException], bool] = _is_retryable,
    ) -> T:
        retrying = AsyncRetrying(
            sleep=self._sleep,
            stop=stop_after_attempt(self._max_retries + 1),
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

    async def _read_bytes(self, url: str, max_bytes: int, allowed_origin: str | None) -> bytes:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(url, current, allowed_origin)
            await self._limiter.acquire()
            headers = {"Accept-Encoding": "identity"}
            follow = allowed_origin is None
            async with self._http.stream(
                "GET", current, headers=headers, follow_redirects=follow
            ) as response:
                if not follow and response.is_redirect and "location" in response.headers:
                    current = str(httpx.URL(current).join(response.headers["location"]))
                    continue
                self._check_status(url, response)
                return await _collect(url, response, max_bytes)
        raise UpstreamError(url, None, "too many redirects")

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
