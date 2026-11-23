"""`UpstreamClient.stats` (`RequestStats`) and the slow-response log (stage 21 follow-up)."""

import logging
from typing import Any

import httpx
import pytest
import respx

from app.sources.http import RequestStats, UpstreamClient
from tests.unit._upstream import FakeClock, make_settings

pytestmark = pytest.mark.unit

ORIGIN = "https://pds.example.test"
URL = f"{ORIGIN}/vol/FILE.TXT"
OTHER = f"{ORIGIN}/vol/MOVED.TXT"


class Sink:
    def __init__(self) -> None:
        self.data = b""

    def feed(self, chunk: bytes) -> None:
        self.data += chunk

    def result(self) -> bytes:
        return self.data


def _client(clock: FakeClock, **overrides: Any) -> UpstreamClient:
    return UpstreamClient(make_settings(**overrides), sleep=clock.sleep, clock=clock)


def test_request_stats_starts_at_zero() -> None:
    assert RequestStats() == RequestStats(0, 0, 0, 0, 0.0)


async def test_stats_count_full_probe_and_conditional_requests_separately(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).mock(
        side_effect=lambda r: (
            httpx.Response(206, headers={"Content-Range": "bytes 6-9/10"}, content=b"tail")
            if r.headers.get("range")
            else httpx.Response(304)
            if r.headers.get("if-none-match")
            else httpx.Response(200, content=b"0123456789")
        )
    )

    async with _client(fake_clock) as client:
        await client.stream_into(URL, Sink, max_bytes=100, allowed_origin=ORIGIN)
        await client.get_bytes(URL, max_bytes=100, allowed_origin=ORIGIN)
        await client.probe_tail(URL, 4, allowed_origin=ORIGIN)
        await client.get_conditional(URL, max_bytes=100, allowed_origin=ORIGIN, etag='"x"')
        stats = client.stats

    assert (stats.gets, stats.probes, stats.conditional) == (2, 1, 1)
    assert stats.bytes_received == 10 + 10 + 4  # the 304 has no body


async def test_stats_count_redirect_hops_and_retries(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(302, headers={"Location": OTHER})
    answers = iter([503, 200])
    upstream_router.get(OTHER).mock(
        side_effect=lambda r: httpx.Response(next(answers), content=b"abcde")
    )

    async with _client(fake_clock, upstream_max_retries=2) as client:
        body = await client.stream_into(URL, Sink, max_bytes=100, allowed_origin=ORIGIN)
        stats = client.stats

    assert body == b"abcde"
    # redirect + 503 + (redirect + 200): every hop and every attempt is a request
    assert stats.gets == 4
    assert stats.bytes_received == 5  # only the 200 body is read


async def test_stats_slowest_wait_is_the_longest_wait_for_headers(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    waits = iter([2.0, 7.5, 3.0])

    def respond(request: httpx.Request) -> httpx.Response:
        fake_clock.advance(next(waits))
        return httpx.Response(200, content=b"x")

    upstream_router.get(URL).mock(side_effect=respond)

    async with _client(fake_clock) as client:
        for _ in range(3):
            await client.get_bytes(URL, max_bytes=10, allowed_origin=ORIGIN)
        assert client.stats.slowest_wait == pytest.approx(7.5)


@pytest.mark.parametrize(
    ("wait", "logged"), [(9.9, False), (10.0, True), (42.0, True)], ids=["fast", "limit", "slow"]
)
async def test_slow_headers_are_logged_at_info_from_ten_seconds(
    upstream_router: respx.MockRouter,
    fake_clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
    wait: float,
    logged: bool,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        fake_clock.advance(wait)
        return httpx.Response(200, content=b"x")

    upstream_router.get(URL).mock(side_effect=respond)
    caplog.set_level(logging.INFO, logger="app.sources.http")

    async with _client(fake_clock) as client:
        await client.get_bytes(URL, max_bytes=10, allowed_origin=ORIGIN)

    slow = [
        r for r in caplog.records if r.levelno == logging.INFO and "Slow response" in r.getMessage()
    ]
    assert bool(slow) is logged
    if logged:
        message = slow[0].getMessage()
        assert message.startswith(f"Slow response from {URL}: headers after ")
        assert message.endswith(" s")
        assert f"{wait:.1f}" in message
