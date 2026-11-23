"""`UpstreamClient.stream_into` and `probe_tail` (stage 21: AC-8, AC-14, AC-15, AC-18, AC-25).

Contract (docs/proposals/stage-21-rad.md, section 6): `stream_into` hands the raw body to a
fresh sink per attempt (`Accept-Encoding: identity`), checks `Content-Length`, caps the size,
checks the host, never writes to disk. `probe_tail` sends `Range: bytes=-n`; `206` gives the
full size from `Content-Range` and the tail; `200` gives the size from `Content-Length` (or
None), no tail, and the body is not read to the end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from app.sources.http import IncompleteResponse, UpstreamClient, UpstreamError
from tests.unit._upstream import USER_AGENT, FakeClock, make_settings

pytestmark = pytest.mark.unit

ORIGIN = "https://pds.example.test/"
URL = "https://pds.example.test/data/vol/DATA/SOL_00000_00089/RAD_RDR_2012_228_20_41_0010_V00.TXT"


def _client(fake_clock: FakeClock, **overrides: Any) -> UpstreamClient:
    return UpstreamClient(make_settings(**overrides), sleep=fake_clock.sleep, clock=fake_clock)


class Collector:
    """A `ChunkSink` that keeps the chunks; every instance is one attempt."""

    instances: list[Collector]

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        Collector.instances.append(self)

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def result(self) -> bytes:
        return b"".join(self.chunks)


@pytest.fixture(autouse=True)
def _reset_collector() -> None:
    Collector.instances = []


class Chunks:
    def __init__(self, count: int, size: int = 1000) -> None:
        self.count, self.size, self.pulled = count, size, 0

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        for _ in range(self.count):
            self.pulled += 1
            yield b"x" * self.size


async def test_stream_into_feeds_the_body_to_the_sink_and_returns_its_result(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = b"abc" * 5000
    route = upstream_router.get(URL).respond(200, content=body)

    async with _client(fake_clock) as client:
        result = await client.stream_into(URL, Collector, max_bytes=10**6, allowed_origin=ORIGIN)

    assert result == body
    request = route.calls.last.request
    assert request.headers["accept-encoding"] == "identity"
    assert request.headers["user-agent"] == USER_AGENT


async def test_stream_into_does_not_write_to_disk(
    upstream_router: respx.MockRouter,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    import builtins
    import io

    opened: list[Any] = []
    real_open = builtins.open

    def watching_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in str(mode) for flag in "wax+"):
            opened.append((file, mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watching_open)
    monkeypatch.setattr(io, "open", watching_open)
    upstream_router.get(URL).respond(200, content=b"z" * 4096)

    async with _client(fake_clock) as client:
        await client.stream_into(URL, Collector, max_bytes=10**6, allowed_origin=ORIGIN)

    assert opened == []
    assert list(tmp_path.iterdir()) == []


async def test_stream_into_short_body_is_retried_with_a_fresh_sink_ac18(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    full = b"y" * 300
    route = upstream_router.get(URL).mock(
        side_effect=[
            httpx.Response(200, content=full[:100], headers={"Content-Length": "300"}),
            httpx.Response(200, content=full, headers={"Content-Length": "300"}),
        ]
    )

    async with _client(fake_clock) as client:
        result = await client.stream_into(URL, Collector, max_bytes=1000, allowed_origin=ORIGIN)

    assert result == full  # the partial first attempt is not part of the result
    assert route.call_count == 2
    assert len(Collector.instances) == 2
    assert Collector.instances[0] is not Collector.instances[1]


async def test_stream_into_always_short_body_raises_incomplete_response_ac18(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(200, content=b"abc", headers={"Content-Length": "300"})

    async with _client(fake_clock, upstream_max_retries=2) as client:
        with pytest.raises(IncompleteResponse):
            await client.stream_into(URL, Collector, max_bytes=1000, allowed_origin=ORIGIN)

    assert route.call_count == 3


async def test_stream_into_body_over_the_limit_is_refused_and_not_retried_ac8(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=1000)
    route = upstream_router.get(URL).mock(return_value=httpx.Response(200, stream=body))

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError) as info:
            await client.stream_into(URL, Collector, max_bytes=5_000, allowed_origin=ORIGIN)

    assert not isinstance(info.value, IncompleteResponse)
    assert body.pulled <= 7
    assert route.call_count == 1


async def test_stream_into_declared_length_over_the_limit_is_refused_before_reading_ac8(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=50)
    upstream_router.get(URL).mock(
        return_value=httpx.Response(200, headers={"Content-Length": str(10**9)}, stream=body)
    )

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.stream_into(URL, Collector, max_bytes=10_000, allowed_origin=ORIGIN)

    assert body.pulled == 0


async def test_stream_into_server_error_is_retried_then_raised(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(503)

    async with _client(fake_clock, upstream_max_retries=2) as client:
        with pytest.raises(UpstreamError) as info:
            await client.stream_into(URL, Collector, max_bytes=1000, allowed_origin=ORIGIN)

    assert info.value.status == 503
    assert route.call_count == 3


async def test_stream_into_client_error_is_not_retried(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    route = upstream_router.get(URL).respond(404)

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError) as info:
            await client.stream_into(URL, Collector, max_bytes=1000, allowed_origin=ORIGIN)

    assert info.value.status == 404
    assert route.call_count == 1


async def test_stream_into_redirect_to_another_host_is_refused_ac25(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    upstream_router.get(URL).respond(302, headers={"Location": "https://evil.example.test/x"})
    evil = upstream_router.get("https://evil.example.test/x").respond(200, content=b"evil")

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.stream_into(URL, Collector, max_bytes=1000, allowed_origin=ORIGIN)

    assert evil.call_count == 0


async def test_stream_into_url_on_another_host_is_refused_without_a_request_ac25(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    other = "https://other.example.test/file.TXT"
    route = upstream_router.get(other).respond(200, content=b"x")

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.stream_into(other, Collector, max_bytes=1000, allowed_origin=ORIGIN)

    assert route.call_count == 0


async def test_stream_into_plain_http_is_refused_ac25(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    plain = URL.replace("https://", "http://")
    route = upstream_router.get(plain).respond(200, content=b"x")

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.stream_into(plain, Collector, max_bytes=1000, allowed_origin=ORIGIN)

    assert route.call_count == 0


# --- probe_tail -------------------------------------------------------------------------------


async def test_probe_tail_206_returns_total_size_and_tail_ac14(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    data = bytes(range(256)) * 1000  # 256 000 bytes
    route = upstream_router.get(URL).respond(
        206,
        content=data[-65536:],
        headers={
            "Content-Range": f"bytes {len(data) - 65536}-{len(data) - 1}/{len(data)}",
            "Content-Length": "65536",
        },
    )

    async with _client(fake_clock) as client:
        probe = await client.probe_tail(URL, 65536, allowed_origin=ORIGIN)

    assert (probe.status, probe.total_size, probe.tail) == (206, len(data), data[-65536:])
    request = route.calls.last.request
    assert request.headers["range"] == "bytes=-65536"
    assert request.headers["accept-encoding"] == "identity"
    assert request.headers["user-agent"] == USER_AGENT


async def test_probe_tail_206_for_a_file_smaller_than_n_returns_the_whole_file_ac14(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    data = b"small file"
    upstream_router.get(URL).respond(
        206,
        content=data,
        headers={"Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}"},
    )

    async with _client(fake_clock) as client:
        probe = await client.probe_tail(URL, 65536, allowed_origin=ORIGIN)

    assert (probe.total_size, probe.tail) == (len(data), data)


async def test_probe_tail_200_gives_the_content_length_and_no_tail_ac15(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=2000)  # 2 MB behind the response
    upstream_router.get(URL).mock(
        return_value=httpx.Response(200, headers={"Content-Length": "2000000"}, stream=body)
    )

    async with _client(fake_clock) as client:
        probe = await client.probe_tail(URL, 65536, allowed_origin=ORIGIN)

    assert (probe.status, probe.total_size, probe.tail) == (200, 2_000_000, None)
    assert body.pulled < 100  # the body is not read to the end


async def test_probe_tail_200_without_content_length_has_no_size_ac15(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    body = Chunks(count=2000)
    upstream_router.get(URL).mock(return_value=httpx.Response(200, stream=body))

    async with _client(fake_clock) as client:
        probe = await client.probe_tail(URL, 65536, allowed_origin=ORIGIN)

    assert (probe.status, probe.total_size, probe.tail) == (200, None, None)
    assert body.pulled < 100


async def test_probe_tail_on_another_host_is_refused_ac25(
    upstream_router: respx.MockRouter, fake_clock: FakeClock
) -> None:
    other = "https://other.example.test/file.TXT"
    route = upstream_router.get(other).respond(206, content=b"x")

    async with _client(fake_clock) as client:
        with pytest.raises(UpstreamError):
            await client.probe_tail(other, 10, allowed_origin=ORIGIN)

    assert route.call_count == 0
