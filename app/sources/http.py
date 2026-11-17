"""Shared HTTP client for upstream feeds: timeouts, retries, rate limit, User-Agent.

Every request to a NASA/JPL source must go through ``UpstreamClient``.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
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


class UpstreamError(Exception):
    """An upstream request failed for good (after retries or with a non-retryable error)."""

    def __init__(self, url: str, status: int | None = None, message: str | None = None) -> None:
        self.url = url
        self.status = status
        detail = message or (f"HTTP {status}" if status is not None else "request failed")
        super().__init__(f"Upstream error for {url}: {detail}")


class PageLimitExceeded(UpstreamError):
    """More pages remain after ``upstream_max_pages``; the incomplete sol is not yielded."""


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
    """Guarantees at least ``1 / max_rps`` seconds between consecutive acquisitions."""

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


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TransportError | _RetryableStatusError)


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
        retrying = AsyncRetrying(
            sleep=self._sleep,
            stop=stop_after_attempt(self._max_retries + 1),
            wait=self._backoff,
            retry=retry_if_exception(_is_retryable),
            before_sleep=lambda state: self._log_retry(url, state),
            reraise=True,
        )
        try:
            async for attempt in retrying:
                with attempt:
                    response = await self._send(url, params)
        except _RetryableStatusError as exc:
            raise UpstreamError(url, exc.status) from exc
        except httpx.HTTPError as exc:
            # Transport errors after the last retry, and non-retryable ones
            # such as TooManyRedirects or DecodingError.
            raise UpstreamError(url, None, f"{type(exc).__name__}: {exc}") from exc
        return self._decode(url, response)

    async def _send(self, url: str, params: Mapping[str, str | int] | None) -> httpx.Response:
        await self._limiter.acquire()
        response = await self._http.get(url, params=dict(params) if params else None)
        status = response.status_code
        if status in RETRYABLE_STATUSES or status >= 500:
            retry_after = None
            if status in RETRY_AFTER_STATUSES:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise _RetryableStatusError(status, retry_after)
        if status >= 400:
            raise UpstreamError(url, status)
        return response

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
