"""AC-18 (stage 18C): /photos/stats and /photos/batch are limited like every /api/v2 path.

Each request lowers `X-RateLimit-Remaining` by exactly one and carries the three headers; the
limit is shared with the other v2 paths; the request over the limit gets 429. Time is a fake
clock, nothing sleeps.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

V2 = "/api/v2"
STATS = f"{V2}/photos/stats"
BATCH = f"{V2}/photos/batch?ids=1,2,3"
IP = "203.0.113.58"
LIMIT = 10


class Clock:
    def __init__(self) -> None:
        self.now = 90_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def limited_app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(
        settings.model_copy(
            update={
                "rate_limit_enabled": True,
                "rate_limit_per_minute": LIMIT,
                "rate_limit_per_hour": 1000,
            }
        )
    )
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    app.state.rate_limiter.clock = Clock()
    try:
        yield app
    finally:
        try:
            await context.__aexit__(None, None, None)
        finally:
            await app.state.engine.dispose()


def client_for(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(IP, 40000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def remaining(response: httpx.Response) -> int:
    for name in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
        assert response.headers.get(name, "").isdigit(), (name, dict(response.headers))
    assert int(response.headers["x-ratelimit-limit"]) == LIMIT
    return int(response.headers["x-ratelimit-remaining"])


@pytest.mark.parametrize("url", [STATS, BATCH], ids=["stats", "batch"])
async def test_ac18_request_lowers_remaining_by_one_and_carries_the_headers(
    limited_app: FastAPI, url: str
) -> None:
    async with client_for(limited_app) as client:
        first = await client.get(url)
        second = await client.get(url)

    assert first.status_code == 200 and second.status_code == 200
    assert remaining(first) == LIMIT - 1
    assert remaining(second) == LIMIT - 2


async def test_ac18_stats_and_batch_share_one_counter(limited_app: FastAPI) -> None:
    async with client_for(limited_app) as client:
        a = await client.get(STATS)
        b = await client.get(BATCH)
        c = await client.get(f"{V2}/rovers")

    assert [remaining(r) for r in (a, b, c)] == [LIMIT - 1, LIMIT - 2, LIMIT - 3]


@pytest.mark.parametrize("url", [STATS, BATCH], ids=["stats", "batch"])
async def test_ac18_validation_error_and_head_count_too(limited_app: FastAPI, url: str) -> None:
    bad = f"{STATS}?group_by=sol" if url == STATS else f"{V2}/photos/batch?ids=1,,2"
    async with client_for(limited_app) as client:
        error = await client.get(bad)
        head = await client.head(url)

    assert error.status_code == 422
    assert remaining(error) == LIMIT - 1
    assert head.status_code == 200
    assert remaining(head) == LIMIT - 2


@pytest.mark.parametrize("url", [STATS, BATCH], ids=["stats", "batch"])
async def test_ac18_request_over_the_limit_gets_429_with_retry_after(
    limited_app: FastAPI, url: str
) -> None:
    async with client_for(limited_app) as client:
        for _ in range(LIMIT):
            assert (await client.get(url)).status_code == 200
        response = await client.get(url)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1
    assert remaining(response) == 0
