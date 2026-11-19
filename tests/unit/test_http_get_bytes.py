"""`UpstreamClient.get_bytes(url, max_bytes=...)` (stage 9a; downloads one PDS .img into memory).

Contract: same retries, rate limit and User-Agent as the other methods; a declared
Content-Length over ``max_bytes`` is refused before the body is read; a stream without
Content-Length that goes over the limit is cut off; a body that does not match its
Content-Length is an ``IncompleteResponse`` and is retried; exceeding the size limit is a
plain ``UpstreamError`` and is NOT retried. Sleeping and the clock are faked.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from app.sources.http import IncompleteResponse, UpstreamClient, UpstreamError
from tests.unit._upstream import USER_AGENT, FakeClock, make_settings

pytestmark = pytest.mark.unit

URL = "https://pds.example.test/img/data/mer/p1.img"


def _client(fake_clock: FakeClock, **overrides: Any) -> UpstreamClient:
    return UpstreamClient(make_settings(**overrides), sleep=fake_clock.sleep, clock=fake_clock)


class Chunks:
    """Async body that counts how many chunks were actually pulled by the client."""

    def __init__(self, count: int, size: int = 1000) -> None:
        self.count, self.size, self.pulled = count, size, 0

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        for _ in range(self.count):
            self.pulled += 1
            yield b"x" * self.size


async def test_get_bytes_returns_the_body_and_sends_user_agent_without_compression(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(200, content=b"PDS3 raw bytes")

    async with _client(fake_clock) as client:
        data = await client.get_bytes(URL, max_bytes=1000)

    assert data == b"PDS3 raw bytes"
    request = route.calls.last.request
    assert request.headers["user-agent"] == USER_AGENT
    assert request.headers["accept-encoding"] == "identity"


async def test_get_bytes_body_exactly_at_the_limit_is_accepted(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(200, content=b"x" * 500)

    async with _client(fake_clock) as client:
        assert len(await client.get_bytes(URL, max_bytes=500)) == 500


async def test_get_bytes_declared_length_over_limit_is_refused_before_reading_the_body(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=50)
    route = upstream_router.get(URL).mock(
        return_value=httpx.Response(200, headers={"Content-Length": str(10**9)}, stream=body)
    )

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError) as info:
            await client.get_bytes(URL, max_bytes=10_000)

    assert not isinstance(info.value, IncompleteResponse)
    assert body.pulled == 0
    assert route.call_count == 1  # too large is not retried
    assert fake_clock.sleeps == []


async def test_get_bytes_stream_without_length_over_limit_is_cut_off_and_not_retried(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=1000)  # 1 MB if read to the end
    route = upstream_router.get(URL).mock(return_value=httpx.Response(200, stream=body))

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError) as info:
            await client.get_bytes(URL, max_bytes=5_000)

    assert not isinstance(info.value, IncompleteResponse)
    assert body.pulled <= 7  # stopped soon after the limit, not at the end of the stream
    assert route.call_count == 1
    assert fake_clock.sleeps == []


async def test_get_bytes_short_body_is_incomplete_and_retried_until_success(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    full = b"y" * 300
    route = upstream_router.get(URL).mock(
        side_effect=[
            httpx.Response(200, content=full[:100], headers={"Content-Length": "300"}),
            httpx.Response(200, content=full[:10], headers={"Content-Length": "300"}),
            httpx.Response(200, content=full, headers={"Content-Length": "300"}),
        ]
    )

    async with _client(fake_clock) as client:
        data = await client.get_bytes(URL, max_bytes=1000)

    assert data == full
    assert route.call_count == 3
    assert len([s for s in fake_clock.sleeps if s > 0]) == 2


async def test_get_bytes_always_short_body_raises_incomplete_response_after_retries(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(200, content=b"abc", headers={"Content-Length": "300"})

    async with _client(fake_clock, upstream_max_retries=2) as client:
        with pytest.raises(IncompleteResponse):
            await client.get_bytes(URL, max_bytes=1000)

    assert route.call_count == 3


async def test_get_bytes_longer_body_than_declared_is_incomplete(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(200, content=b"x" * 400, headers={"Content-Length": "300"})

    async with _client(fake_clock, upstream_max_retries=0) as client:
        with pytest.raises(IncompleteResponse):
            await client.get_bytes(URL, max_bytes=1000)


async def test_get_bytes_404_is_an_error_without_retry(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(404)

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError) as info:
            await client.get_bytes(URL, max_bytes=1000)

    assert info.value.status == 404
    assert route.call_count == 1


async def test_get_bytes_503_is_retried_then_succeeds(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"ok")]
    )

    async with _client(fake_clock) as client:
        assert await client.get_bytes(URL, max_bytes=1000) == b"ok"

    assert route.call_count == 2


async def test_get_bytes_network_error_is_retried_and_then_an_upstream_error(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    async with _client(fake_clock, upstream_max_retries=2) as client:
        with pytest.raises(UpstreamError):
            await client.get_bytes(URL, max_bytes=1000)

    assert route.call_count == 3


# --- allowed_origin (SSRF guard): the first URL and every redirect hop ---------------------

HOST = "pds.example.test"
ORIGIN = f"https://{HOST}"


async def test_get_bytes_foreign_host_is_refused_without_a_request(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    catch_all = upstream_router.route().mock(return_value=httpx.Response(200, content=b"x"))

    async with _client(fake_clock) as client:
        for bad in (
            "https://evil.example.test/p1.img",
            "http://169.254.169.254/latest",
            f"https://{HOST}.evil.example.test/p1.img",
            f"https://{HOST}@evil.example.test/p1.img",
            f"http://{HOST}/p1.img",  # plain http: wrong scheme
            f"https://{HOST}:8443/p1.img",  # wrong port
        ):
            with pytest.raises(UpstreamError):
                await client.get_bytes(bad, max_bytes=1000, allowed_origin=ORIGIN)

    assert catch_all.call_count == 0


async def test_get_bytes_redirect_to_a_foreign_host_is_refused(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(302, headers={"Location": "http://169.254.169.254/secret"})
    evil = upstream_router.get("http://169.254.169.254/secret").respond(200, content=b"secret")

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.get_bytes(URL, max_bytes=1000, allowed_origin=ORIGIN)

    assert evil.call_count == 0


async def test_get_bytes_redirect_within_the_allowed_origin_is_followed(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    other = f"https://{HOST}/moved/p1.img"
    upstream_router.get(URL).respond(301, headers={"Location": other})
    upstream_router.get(other).respond(200, content=b"moved body")

    async with _client(fake_clock) as client:
        data = await client.get_bytes(URL, max_bytes=1000, allowed_origin=ORIGIN)

    assert data == b"moved body"


async def test_get_bytes_endless_redirects_end_in_an_error(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(302, headers={"Location": URL})

    async with _client(fake_clock, upstream_max_retries=0) as client:
        with pytest.raises(UpstreamError):
            await client.get_bytes(URL, max_bytes=1000, allowed_origin=ORIGIN)

    assert route.call_count <= 7


@pytest.mark.parametrize(
    "target",
    [f"http://{HOST}/moved/p1.img", f"https://{HOST}:8443/moved/p1.img"],
    ids=["https-to-http", "other-port"],
)
async def test_get_bytes_redirect_changing_scheme_or_port_on_the_same_host_is_refused(
    upstream_router: respx.MockRouter, fake_clock: FakeClock, target: str
) -> None:
    upstream_router.get(URL).respond(302, headers={"Location": target})
    hop = upstream_router.get(target).respond(200, content=b"downgraded")

    async with _client(fake_clock, upstream_max_retries=0) as client:
        with pytest.raises(UpstreamError):
            await client.get_bytes(URL, max_bytes=1000, allowed_origin=ORIGIN)

    assert hop.call_count == 0
