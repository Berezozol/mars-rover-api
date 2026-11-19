"""`UpstreamClient.get_conditional(url, *, max_bytes, allowed_origin, etag, last_modified)`
(stage 11; conditional GET of an MMGIS file).

Contract: ``If-None-Match`` / ``If-Modified-Since`` are sent only when given; ``304`` ->
``ConditionalBody(not_modified=True, body=None, ...)`` without reading a body; ``200`` -> the
body and the ``ETag`` / ``Last-Modified`` headers; the same retries on 5xx and network errors,
User-Agent, size limit and host check (first URL and every redirect hop) as ``get_bytes``.
"""

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from app.sources.http import ConditionalBody, IncompleteResponse, UpstreamClient, UpstreamError
from tests.unit._upstream import USER_AGENT, FakeClock, make_settings

pytestmark = pytest.mark.unit

HOST = "mmgis.example.test"
ORIGIN = f"https://{HOST}"
URL = f"{ORIGIN}/M20/Layers/json/M20_waypoints.json"
ETAG = '"6e29cc7bd422e3da1a1e78e0c4855dc1"'
MODIFIED = "Wed, 28 Oct 2026 15:05:06 GMT"


def _client(fake_clock: FakeClock, **overrides: Any) -> UpstreamClient:
    return UpstreamClient(make_settings(**overrides), sleep=fake_clock.sleep, clock=fake_clock)


class Chunks:
    def __init__(self, count: int, size: int = 1000) -> None:
        self.count, self.size, self.pulled = count, size, 0

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        for _ in range(self.count):
            self.pulled += 1
            yield b"x" * self.size


def test_conditional_body_is_a_frozen_dataclass_with_the_agreed_fields() -> None:
    assert dataclasses.is_dataclass(ConditionalBody)
    assert [f.name for f in dataclasses.fields(ConditionalBody)] == [
        "not_modified",
        "body",
        "etag",
        "last_modified",
    ]
    value = ConditionalBody(not_modified=False, body=b"x", etag=None, last_modified=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.body = b"y"  # type: ignore[misc]


# --- request headers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({}, {}, id="none"),
        pytest.param({"etag": ETAG}, {"if-none-match": ETAG}, id="etag-only"),
        pytest.param(
            {"last_modified": MODIFIED}, {"if-modified-since": MODIFIED}, id="last-modified-only"
        ),
        pytest.param(
            {"etag": ETAG, "last_modified": MODIFIED},
            {"if-none-match": ETAG, "if-modified-since": MODIFIED},
            id="both",
        ),
    ],
)
async def test_get_conditional_sends_validators_only_when_given(
    upstream_router: respx.MockRouter,
    fake_clock: FakeClock,
    kwargs: dict[str, str],
    expected: dict[str, str],
) -> None:
    route = upstream_router.get(URL).respond(200, content=b"{}")

    async with _client(fake_clock) as client:
        await client.get_conditional(URL, max_bytes=1000, **kwargs)

    headers = route.calls.last.request.headers
    for name in ("if-none-match", "if-modified-since"):
        assert headers.get(name) == expected.get(name), name
    assert headers["user-agent"] == USER_AGENT


# --- responses ---------------------------------------------------------------------------------


async def test_get_conditional_200_returns_body_and_validators(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(
        200, content=b'{"a": 1}', headers={"ETag": ETAG, "Last-Modified": MODIFIED}
    )

    async with _client(fake_clock) as client:
        result = await client.get_conditional(URL, max_bytes=1000)

    assert result == ConditionalBody(
        not_modified=False, body=b'{"a": 1}', etag=ETAG, last_modified=MODIFIED
    )


async def test_get_conditional_200_without_validator_headers_gives_none(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(200, content=b"{}")

    async with _client(fake_clock) as client:
        result = await client.get_conditional(URL, max_bytes=1000)

    assert (result.not_modified, result.body, result.etag, result.last_modified) == (
        False,
        b"{}",
        None,
        None,
    )


async def test_get_conditional_304_is_not_modified_and_the_body_is_not_read(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=100)
    route = upstream_router.get(URL).mock(return_value=httpx.Response(304, stream=body))

    async with _client(fake_clock) as client:
        result = await client.get_conditional(URL, max_bytes=1000, etag=ETAG)

    assert result.not_modified is True
    assert result.body is None
    assert body.pulled == 0
    assert route.call_count == 1  # a 304 is not retried


async def test_get_conditional_404_is_an_error_without_retry(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(404)

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError) as info:
            await client.get_conditional(URL, max_bytes=1000)

    assert info.value.status == 404
    assert route.call_count == 1


# --- retries -----------------------------------------------------------------------------------


async def test_get_conditional_503_is_retried_then_succeeds(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=b"ok")]
    )

    async with _client(fake_clock) as client:
        result = await client.get_conditional(URL, max_bytes=1000)

    assert result.body == b"ok"
    assert route.call_count == 2
    assert len([s for s in fake_clock.sleeps if s > 0]) == 1


async def test_get_conditional_5xx_every_time_raises_after_the_configured_retries(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(500)

    async with _client(fake_clock, upstream_max_retries=2) as client:
        with pytest.raises(UpstreamError) as info:
            await client.get_conditional(URL, max_bytes=1000)

    assert info.value.status == 500
    assert route.call_count == 3


async def test_get_conditional_network_error_is_retried_then_an_upstream_error(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    async with _client(fake_clock, upstream_max_retries=2) as client:
        with pytest.raises(UpstreamError):
            await client.get_conditional(URL, max_bytes=1000)

    assert route.call_count == 3


# --- size limit --------------------------------------------------------------------------------


async def test_get_conditional_body_exactly_at_the_limit_is_accepted(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(200, content=b"x" * 500)

    async with _client(fake_clock) as client:
        result = await client.get_conditional(URL, max_bytes=500)

    assert result.body is not None and len(result.body) == 500


async def test_get_conditional_declared_length_over_limit_is_refused_before_reading(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=50)
    route = upstream_router.get(URL).mock(
        return_value=httpx.Response(200, headers={"Content-Length": str(10**9)}, stream=body)
    )

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError) as info:
            await client.get_conditional(URL, max_bytes=10_000)

    assert not isinstance(info.value, IncompleteResponse)
    assert body.pulled == 0
    assert route.call_count == 1


async def test_get_conditional_stream_without_length_over_limit_is_cut_off_not_retried(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=1000)
    route = upstream_router.get(URL).mock(return_value=httpx.Response(200, stream=body))

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.get_conditional(URL, max_bytes=5_000)

    assert body.pulled <= 7
    assert route.call_count == 1
    assert fake_clock.sleeps == []


# --- allowed_origin ----------------------------------------------------------------------------


async def test_get_conditional_foreign_host_is_refused_without_a_request(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    catch_all = upstream_router.route().mock(return_value=httpx.Response(200, content=b"x"))

    async with _client(fake_clock) as client:
        for bad in (
            "https://evil.example.test/M20_waypoints.json",
            "http://169.254.169.254/latest",
            f"https://{HOST}.evil.example.test/x.json",
            f"http://{HOST}/x.json",
            f"https://{HOST}:8443/x.json",
        ):
            with pytest.raises(UpstreamError):
                await client.get_conditional(bad, max_bytes=1000, allowed_origin=ORIGIN)

    assert catch_all.call_count == 0


async def test_get_conditional_redirect_to_a_foreign_host_is_refused(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(302, headers={"Location": "http://169.254.169.254/secret"})
    evil = upstream_router.get("http://169.254.169.254/secret").respond(200, content=b"secret")

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.get_conditional(URL, max_bytes=1000, allowed_origin=ORIGIN)

    assert evil.call_count == 0


async def test_get_conditional_redirect_within_the_allowed_origin_is_followed(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    other = f"{ORIGIN}/moved/M20_waypoints.json"
    upstream_router.get(URL).respond(301, headers={"Location": other})
    upstream_router.get(other).respond(200, content=b"moved", headers={"ETag": ETAG})

    async with _client(fake_clock) as client:
        result = await client.get_conditional(URL, max_bytes=1000, allowed_origin=ORIGIN)

    assert (result.body, result.etag) == (b"moved", ETAG)


@pytest.mark.parametrize(
    "target",
    [f"http://{HOST}/moved/x.json", f"https://{HOST}:8443/moved/x.json"],
    ids=["https-to-http", "other-port"],
)
async def test_get_conditional_redirect_changing_scheme_or_port_is_refused(
    upstream_router: respx.MockRouter, fake_clock: FakeClock, target: str
) -> None:
    upstream_router.get(URL).respond(302, headers={"Location": target})
    hop = upstream_router.get(target).respond(200, content=b"downgraded")

    async with _client(fake_clock, upstream_max_retries=0) as client:
        with pytest.raises(UpstreamError):
            await client.get_conditional(URL, max_bytes=1000, allowed_origin=ORIGIN)

    assert hop.call_count == 0
