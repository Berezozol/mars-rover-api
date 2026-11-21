"""Rate-limit key for IPv6 (docs/API.md "Ограничение частоты", stage 18B).

IPv6 addresses are counted per /64: addresses of one /64 share the windows and the concurrency
limit, different /64 networks do not; IPv4 stays per address. Behind a trusted proxy the
client address is replaced by X-Forwarded-For before the limiter (uvicorn), so the tests set
the ASGI client address directly. Time is the app's fake clock; nothing sleeps.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

URL = "/api/v2/rovers"
V1_URL = "/api/v1/rovers"

NET_A = [
    "2001:db8:1:2::1",
    "2001:db8:1:2::2",
    "2001:DB8:1:2:0:0:0:3",
    "2001:db8:1:2:ffff:ffff:ffff:ffff",
]
NET_B = "2001:db8:1:3::1"  # the next /64


class Clock:
    now = 80_000.0

    def __call__(self) -> float:
        return self.now


def client_for(app: FastAPI, ip: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


Build = Callable[..., Awaitable[tuple[FastAPI, asyncio.Event, list[int]]]]


@pytest.fixture
async def build(settings: Settings) -> AsyncIterator[Build]:
    started: list[tuple[FastAPI, object]] = []

    async def make(
        per_minute: int = 3, concurrent: int = 4
    ) -> tuple[FastAPI, asyncio.Event, list[int]]:
        app = create_app(
            settings.model_copy(
                update={
                    "rate_limit_enabled": True,
                    "rate_limit_per_minute": per_minute,
                    "rate_limit_per_hour": 1000,
                    "rate_limit_concurrent": concurrent,
                }
            )
        )
        release = asyncio.Event()
        entered: list[int] = []

        async def held() -> dict[str, bool]:
            entered.append(1)
            await release.wait()
            return {"ok": True}

        app.add_api_route("/api/v2/__hold", held, methods=["GET"])
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        started.append((app, context))
        app.state.rate_limiter.clock = Clock()
        return app, release, entered

    try:
        yield make
    finally:
        for app, context in reversed(started):
            try:
                await context.__aexit__(None, None, None)  # type: ignore[attr-defined]
            finally:
                await app.state.engine.dispose()


async def test_addresses_of_one_slash_64_share_the_window(build: Build) -> None:
    app, _, _ = await build(per_minute=3)

    statuses = []
    for ip in NET_A:
        async with client_for(app, ip) as client:
            statuses.append((await client.get(URL)).status_code)

    assert statuses == [200, 200, 200, 429]


async def test_rate_limit_headers_count_down_across_addresses_of_one_slash_64(
    build: Build,
) -> None:
    app, _, _ = await build(per_minute=5)

    remaining = []
    for ip in NET_A[:3]:
        async with client_for(app, ip) as client:
            remaining.append((await client.get(URL)).headers["x-ratelimit-remaining"])

    assert remaining == ["4", "3", "2"]


async def test_a_different_slash_64_has_its_own_window(build: Build) -> None:
    app, _, _ = await build(per_minute=3)
    for ip in NET_A[:3]:
        async with client_for(app, ip) as client:
            assert (await client.get(URL)).status_code == 200

    async with client_for(app, NET_B) as client:
        response = await client.get(URL)

    assert response.status_code == 200
    assert response.headers["x-ratelimit-remaining"] == "2"


async def test_ipv4_addresses_stay_separate_even_in_one_subnet(build: Build) -> None:
    app, _, _ = await build(per_minute=1)

    codes = []
    for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.1"):
        async with client_for(app, ip) as client:
            codes.append((await client.get(V1_URL)).status_code)

    assert codes == [200, 200, 429]


async def test_concurrency_cap_is_shared_by_addresses_of_one_slash_64(build: Build) -> None:
    app, release, entered = await build(per_minute=100, concurrent=2)
    clients = [client_for(app, ip) for ip in (*NET_A[:3], NET_B)]
    held = [asyncio.ensure_future(c.get("/api/v2/__hold")) for c in clients[:2]]
    for _ in range(100_000):
        if len(entered) >= 2:
            break
        await asyncio.sleep(0)
    try:
        same_net = await clients[2].get(URL)
        other_net = await clients[3].get(URL)
    finally:
        release.set()
        await asyncio.gather(*held)
        for c in clients:
            await c.aclose()

    assert same_net.status_code == 429
    assert same_net.headers["retry-after"] == "1"
    assert other_net.status_code == 200
