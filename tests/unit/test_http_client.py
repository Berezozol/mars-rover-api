"""Contract tests for app.sources.http: UpstreamClient, retries, errors and RateLimiter.

No real network: every request goes through a strict respx router; sleeping and the clock
are faked, so retries and rate limiting never wait for real.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from app.sources import http
from tests.unit._upstream import FakeClock, make_settings

pytestmark = pytest.mark.unit

URL = "https://feeds.example.test/api/items"


@pytest.fixture
def make_client(fake_clock: FakeClock) -> Callable[..., Any]:
    def factory(**overrides: Any) -> Any:
        return http.UpstreamClient(
            make_settings(**overrides), sleep=fake_clock.sleep, clock=fake_clock
        )

    return factory


async def _get(client: Any, url: str = URL, params: dict[str, Any] | None = None) -> Any:
    async with client as c:
        return await c.get_json(url, params)


def _positive(sleeps: list[float]) -> list[float]:
    return [s for s in sleeps if s > 0]


# --- request shape -----------------------------------------------------------------------


async def test_get_json_success_returns_parsed_body_and_sends_user_agent(
    upstream_router: respx.MockRouter, make_client: Callable[..., Any]
) -> None:
    route = upstream_router.get(URL).respond(200, json={"items": [1, 2], "more": False})

    body = await _get(make_client(), params={"page": 0, "condition_1": "msl:mission"})

    assert body == {"items": [1, 2], "more": False}
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["User-Agent"] == "mars-rover-api-tests/0.0 (unit tests)"
    assert request.url.params["page"] == "0"
    assert request.url.params["condition_1"] == "msl:mission"


async def test_get_json_timeout_comes_from_settings(
    upstream_router: respx.MockRouter, make_client: Callable[..., Any]
) -> None:
    route = upstream_router.get(URL).respond(200, json={})

    await _get(make_client(upstream_timeout_seconds=12.5))

    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout == {"connect": 12.5, "read": 12.5, "write": 12.5, "pool": 12.5}


async def test_get_json_redirect_is_followed(
    upstream_router: respx.MockRouter, make_client: Callable[..., Any]
) -> None:
    upstream_router.get(URL).respond(301, headers={"Location": URL + "/"})
    upstream_router.get(URL + "/").respond(200, json={"ok": True})

    assert await _get(make_client()) == {"ok": True}


# --- retries -----------------------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 429], ids=lambda s: f"http-{s}")
async def test_get_json_retryable_status_then_success_returns_body(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
    status: int,
) -> None:
    route = upstream_router.get(URL)
    route.side_effect = [
        httpx.Response(status),
        httpx.Response(status),
        httpx.Response(200, json={"ok": True}),
    ]

    body = await _get(make_client(upstream_retry_base_seconds=1.0))

    assert body == {"ok": True}
    assert route.call_count == 3
    assert _positive(fake_clock.sleeps) == [1.0, 2.0]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(httpx.ConnectError("connection refused"), id="connect-error"),
        pytest.param(httpx.ReadTimeout("read timed out"), id="read-timeout"),
        pytest.param(httpx.ConnectTimeout("connect timed out"), id="connect-timeout"),
        pytest.param(httpx.RemoteProtocolError("server disconnected"), id="protocol-error"),
    ],
)
async def test_get_json_transport_error_then_success_returns_body(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
    error: Exception,
) -> None:
    route = upstream_router.get(URL)
    route.side_effect = [error, httpx.Response(200, json={"ok": True})]

    assert await _get(make_client()) == {"ok": True}
    assert route.call_count == 2
    assert _positive(fake_clock.sleeps) == [1.0]


async def test_get_json_retry_logs_warning_with_url_and_attempt_number(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    upstream_router.get(URL).side_effect = [
        httpx.Response(503),
        httpx.Response(503),
        httpx.Response(200, json={}),
    ]

    await _get(make_client())

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, warnings
    for attempt, message in enumerate(warnings, start=1):
        assert URL in message
        # The attempt number appears as a standalone number outside the URL.
        assert re.search(rf"(?<!\d){attempt}(?!\d)", message.replace(URL, "")), message


@pytest.mark.parametrize(
    ("base", "retries", "expected_delays"),
    [
        pytest.param(1.0, 0, [], id="no-retries-single-attempt"),
        pytest.param(1.0, 4, [1.0, 2.0, 4.0, 8.0], id="base1-4retries"),
        pytest.param(0.5, 3, [0.5, 1.0, 2.0], id="base0.5-3retries"),
        pytest.param(10.0, 4, [10.0, 20.0, 30.0, 30.0], id="capped-at-30"),
    ],
)
async def test_get_json_retries_exhausted_raises_upstream_error_with_backoff(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
    base: float,
    retries: int,
    expected_delays: list[float],
) -> None:
    route = upstream_router.get(URL).respond(503)

    with pytest.raises(http.UpstreamError) as exc_info:
        await _get(make_client(upstream_retry_base_seconds=base, upstream_max_retries=retries))

    assert route.call_count == retries + 1
    assert _positive(fake_clock.sleeps) == expected_delays
    assert exc_info.value.status == 503
    assert exc_info.value.url.startswith(URL)


async def test_get_json_transport_errors_exhausted_raises_upstream_error_without_status(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
) -> None:
    route = upstream_router.get(URL)
    route.side_effect = httpx.ConnectError("connection refused")

    with pytest.raises(http.UpstreamError) as exc_info:
        await _get(make_client(upstream_max_retries=2))

    assert route.call_count == 3
    assert exc_info.value.status is None
    assert exc_info.value.url.startswith(URL)
    assert isinstance(exc_info.value.__cause__, httpx.TransportError)


@pytest.mark.parametrize(
    ("status", "retry_after", "base", "expected_delay"),
    [
        pytest.param(503, "5", 1.0, 5.0, id="503-retry-after-longer-than-backoff"),
        pytest.param(429, "5", 1.0, 5.0, id="429-retry-after-longer-than-backoff"),
        pytest.param(429, "1", 4.0, 4.0, id="429-backoff-longer-than-retry-after"),
        pytest.param(503, "0", 1.0, 1.0, id="503-retry-after-zero-uses-backoff"),
        pytest.param(503, "120", 1.0, 30.0, id="503-retry-after-capped-at-30"),
        pytest.param(429, "soon", 1.0, 1.0, id="429-unparsable-retry-after-uses-backoff"),
    ],
)
async def test_get_json_retry_after_header_is_honoured(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
    status: int,
    retry_after: str,
    base: float,
    expected_delay: float,
) -> None:
    route = upstream_router.get(URL)
    route.side_effect = [
        httpx.Response(status, headers={"Retry-After": retry_after}),
        httpx.Response(200, json={"ok": True}),
    ]

    assert await _get(make_client(upstream_retry_base_seconds=base)) == {"ok": True}
    assert route.call_count == 2
    assert _positive(fake_clock.sleeps) == [expected_delay]


# --- non-retryable errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(httpx.TooManyRedirects("redirect loop"), id="too-many-redirects"),
        pytest.param(httpx.DecodingError("bad gzip stream"), id="decoding-error"),
    ],
)
async def test_get_json_other_http_error_raises_upstream_error_without_retry(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
    error: Exception,
) -> None:
    route = upstream_router.get(URL)
    route.side_effect = error

    with pytest.raises(http.UpstreamError) as exc_info:
        await _get(make_client())

    assert route.call_count == 1
    assert _positive(fake_clock.sleeps) == []
    assert exc_info.value.url.startswith(URL)


async def test_get_json_redirect_loop_raises_upstream_error_without_retry(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
) -> None:
    upstream_router.get(URL).respond(302, headers={"Location": URL})

    with pytest.raises(http.UpstreamError):
        await _get(make_client())

    assert _positive(fake_clock.sleeps) == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410], ids=lambda s: f"http-{s}")
async def test_get_json_client_error_raises_immediately_without_retry(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
    status: int,
) -> None:
    route = upstream_router.get(URL).respond(status)

    with pytest.raises(http.UpstreamError) as exc_info:
        await _get(make_client())

    assert route.call_count == 1
    assert _positive(fake_clock.sleeps) == []
    assert exc_info.value.status == status


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"<html>maintenance</html>", id="html"),
        pytest.param(b"", id="empty-body"),
        pytest.param(b'{"items": [', id="truncated-json"),
    ],
)
async def test_get_json_invalid_json_raises_upstream_error_without_retry(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
    content: bytes,
) -> None:
    route = upstream_router.get(URL).respond(200, content=content)

    with pytest.raises(http.UpstreamError):
        await _get(make_client())

    assert route.call_count == 1
    assert _positive(fake_clock.sleeps) == []


def test_upstream_error_exposes_url_and_status() -> None:
    error = http.UpstreamError(URL, 503)
    assert error.url == URL
    assert error.status == 503
    assert isinstance(error, Exception)


# --- rate limiting -----------------------------------------------------------------------


async def test_client_requests_pass_through_rate_limiter(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
) -> None:
    upstream_router.get(URL).respond(200, json={})

    async with make_client(upstream_max_rps=1.0) as client:
        for _ in range(3):
            await client.get_json(URL)

    # Clock never moves except through sleep: 3 requests at 1 rps need >= 2 s of waiting.
    assert sum(fake_clock.sleeps) == pytest.approx(2.0)


async def test_client_retry_attempts_pass_through_rate_limiter(
    upstream_router: respx.MockRouter,
    make_client: Callable[..., Any],
    fake_clock: FakeClock,
) -> None:
    """Backoff 0.25 s is shorter than the 1 s limiter interval: the limiter must still wait."""
    upstream_router.get(URL).side_effect = [httpx.Response(503), httpx.Response(200, json={})]

    await _get(make_client(upstream_max_rps=1.0, upstream_retry_base_seconds=0.25))

    assert sum(fake_clock.sleeps) == pytest.approx(1.0)


async def _acquire_times(limiter: Any, clock: FakeClock, count: int) -> list[float]:
    times = []
    for _ in range(count):
        await limiter.acquire()
        times.append(clock())
    return times


@pytest.mark.parametrize(
    ("max_rps", "interval"),
    [pytest.param(1.0, 1.0, id="1rps"), pytest.param(2.0, 0.5, id="2rps")],
)
async def test_rate_limiter_back_to_back_acquires_are_spaced(
    fake_clock: FakeClock, max_rps: float, interval: float
) -> None:
    limiter = http.RateLimiter(max_rps, clock=fake_clock, sleep=fake_clock.sleep)

    times = await _acquire_times(limiter, fake_clock, 4)

    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(gap >= interval - 1e-9 for gap in gaps), gaps
    # No over-waiting: the first acquire is immediate, then exactly one interval each.
    assert times[0] == pytest.approx(1000.0)
    assert sum(fake_clock.sleeps) == pytest.approx(3 * interval)


async def test_rate_limiter_after_idle_period_does_not_sleep(fake_clock: FakeClock) -> None:
    limiter = http.RateLimiter(1.0, clock=fake_clock, sleep=fake_clock.sleep)

    await limiter.acquire()
    fake_clock.advance(5.0)
    await limiter.acquire()

    assert _positive(fake_clock.sleeps) == []


async def test_rate_limiter_partial_idle_sleeps_only_the_remainder(fake_clock: FakeClock) -> None:
    limiter = http.RateLimiter(1.0, clock=fake_clock, sleep=fake_clock.sleep)

    await limiter.acquire()
    fake_clock.advance(0.75)
    await limiter.acquire()

    assert sum(fake_clock.sleeps) == pytest.approx(0.25)


@pytest.mark.parametrize("max_rps", [0, 0.0, -1.0], ids=["zero", "zero-float", "negative"])
async def test_rate_limiter_non_positive_rps_never_sleeps(
    fake_clock: FakeClock, max_rps: float
) -> None:
    limiter = http.RateLimiter(max_rps, clock=fake_clock, sleep=fake_clock.sleep)

    await _acquire_times(limiter, fake_clock, 10)

    assert _positive(fake_clock.sleeps) == []


async def test_rate_limiter_concurrent_acquires_are_serialized_in_order(
    fake_clock: FakeClock,
) -> None:
    limiter = http.RateLimiter(1.0, clock=fake_clock, sleep=fake_clock.sleep)
    acquired: list[tuple[int, float]] = []

    async def worker(index: int) -> None:
        await limiter.acquire()
        acquired.append((index, fake_clock()))

    await asyncio.gather(*(worker(i) for i in range(5)))

    assert acquired == [(0, 1000.0), (1, 1001.0), (2, 1002.0), (3, 1003.0), (4, 1004.0)]
