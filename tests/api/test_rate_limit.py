"""Per-IP rate limit on v1 and v2 (docs/API.md "Ограничение частоты"; LEGACY-API.md "лимит
частоты"; stage 9).

- Applies to /api/v1/*, /mars-photos/api/v1/* and /api/v2/*, HEAD and 304 included; not to
  /, /health, /health/sync, /docs, /openapi.json.
- Key = client IP (``scope["client"]``; emulated with ``httpx.ASGITransport(client=...)``).
- Two windows: RATE_LIMIT_PER_MINUTE and RATE_LIMIT_PER_HOUR; rejected calls are not counted.
- 429: v2 ``{"error": {"code": "rate_limited", "message": "Rate limit exceeded", "details":
  null}}``, v1 ``{"errors": "Rate limit exceeded"}``; ``Retry-After`` integer seconds >= 1;
  ``Cache-Control: no-store``.
- RATE_LIMIT_ENABLED=false disables it (the default for the rest of the suite).

Time is moved through ``app.state.rate_limiter.clock``; nothing sleeps. The endpoints used
only read the seed rovers, so the tests need the migrated database but no data fixtures.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

V2_URL = "/api/v2/rovers"
V1_URL = "/api/v1/rovers"
V1_ALT_URL = "/mars-photos/api/v1/rovers"

V2_BODY = {"error": {"code": "rate_limited", "message": "Rate limit exceeded", "details": None}}
V1_BODY = {"errors": "Rate limit exceeded"}

URLS = [
    pytest.param(V2_URL, V2_BODY, id="v2"),
    pytest.param(V1_URL, V1_BODY, id="v1"),
    pytest.param(V1_ALT_URL, V1_BODY, id="v1-mars-photos"),
]

IP_A = "203.0.113.10"
IP_B = "203.0.113.20"


class Clock:
    def __init__(self, now: float = 50_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def limited_settings(settings: Settings, per_minute: int = 3, per_hour: int = 1000) -> Settings:
    return settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_per_minute": per_minute,
            "rate_limit_per_hour": per_hour,
        }
    )


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[FastAPI]:
    try:
        async with app.router.lifespan_context(app):
            yield app
    finally:
        await app.state.engine.dispose()


def client_for(app: FastAPI, ip: str = IP_A) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


AppFactory = Callable[..., Awaitable[tuple[FastAPI, Clock]]]


@pytest.fixture
async def limited(settings: Settings) -> AsyncIterator[AppFactory]:
    """Factory: an app with the limit on, inside its lifespan, and its fake clock."""
    contexts = []

    async def build(per_minute: int = 3, per_hour: int = 1000) -> tuple[FastAPI, Clock]:
        app = create_app(limited_settings(settings, per_minute, per_hour))
        context = running(app)
        await context.__aenter__()
        contexts.append(context)
        clock = Clock()
        app.state.rate_limiter.clock = clock
        return app, clock

    try:
        yield build
    finally:
        for context in reversed(contexts):
            await context.__aexit__(None, None, None)


async def statuses(client: httpx.AsyncClient, url: str, n: int, method: str = "GET") -> list[int]:
    return [(await client.request(method, url)).status_code for _ in range(n)]


def retry_after(response: httpx.Response) -> int:
    value = response.headers.get("retry-after")
    assert value is not None, dict(response.headers)
    assert value.isdigit(), f"Retry-After must be integer seconds, got {value!r}"
    seconds = int(value)
    assert seconds >= 1
    return seconds


def assert_rate_limited(response: httpx.Response, body: dict[str, object] | None) -> int:
    assert response.status_code == 429, response.text
    assert response.headers.get("cache-control") == "no-store", dict(response.headers)
    assert "etag" not in response.headers
    if body is not None:
        assert response.json() == body
        assert list(response.json()) == list(body)
        if "error" in body:
            assert list(response.json()["error"]) == ["code", "message", "details"]
    return retry_after(response)


# --- the 429 response ----------------------------------------------------------------------


@pytest.mark.parametrize(("url", "body"), URLS)
async def test_rate_limit_over_minute_limit_returns_429_with_exact_body(
    limited: AppFactory, url: str, body: dict[str, object]
) -> None:
    app, _ = await limited(per_minute=3)
    async with client_for(app) as client:
        assert await statuses(client, url, 3) == [200, 200, 200]

        response = await client.get(url)

    seconds = assert_rate_limited(response, body)
    assert seconds <= 60


@pytest.mark.parametrize(("url", "body"), URLS)
async def test_rate_limit_retry_after_is_seconds_until_window_frees(
    limited: AppFactory, url: str, body: dict[str, object]
) -> None:
    app, clock = await limited(per_minute=2)
    async with client_for(app) as client:
        await statuses(client, url, 2)  # t=0
        clock.advance(20)

        response = await client.get(url)

    seconds = assert_rate_limited(response, body)
    assert 40 <= seconds <= 41


async def test_rate_limit_retry_after_under_one_second_is_one(limited: AppFactory) -> None:
    app, clock = await limited(per_minute=1)
    async with client_for(app) as client:
        await client.get(V2_URL)
        clock.advance(59.8)

        response = await client.get(V2_URL)

    assert assert_rate_limited(response, V2_BODY) == 1


async def test_rate_limit_after_clock_moves_past_window_returns_200(limited: AppFactory) -> None:
    app, clock = await limited(per_minute=2)
    async with client_for(app) as client:
        await statuses(client, V2_URL, 2)
        blocked = await client.get(V2_URL)
        clock.advance(retry_after(blocked) + 0.01)

        response = await client.get(V2_URL)

    assert response.status_code == 200, response.text
    assert response.json()["items"]


async def test_rate_limit_rejected_requests_do_not_extend_block(limited: AppFactory) -> None:
    app, clock = await limited(per_minute=2)
    async with client_for(app) as client:
        await statuses(client, V2_URL, 2)
        for _ in range(10):
            clock.advance(5)
            assert (await client.get(V2_URL)).status_code == 429
        clock.advance(10.01)  # 60 s after the two accepted requests

        assert await statuses(client, V2_URL, 2) == [200, 200]


# --- windows -------------------------------------------------------------------------------


async def test_rate_limit_hour_window_returns_429_after_minute_frees(limited: AppFactory) -> None:
    app, clock = await limited(per_minute=3, per_hour=5)
    async with client_for(app) as client:
        assert await statuses(client, V2_URL, 3) == [200, 200, 200]  # t=0
        assert (await client.get(V2_URL)).status_code == 429  # minute window
        clock.advance(61)
        assert await statuses(client, V2_URL, 2) == [200, 200]  # hour window now full

        response = await client.get(V2_URL)
        seconds = assert_rate_limited(response, V2_BODY)
        assert 3538 <= seconds <= 3540  # until the t=0 requests leave the hour

        clock.advance(3600 - 61 + 0.01)
        assert (await client.get(V2_URL)).status_code == 200


async def test_rate_limit_v1_and_v2_share_the_ip_counter(limited: AppFactory) -> None:
    app, _ = await limited(per_minute=3)
    async with client_for(app) as client:
        assert (await client.get(V2_URL)).status_code == 200
        assert (await client.get(V1_URL)).status_code == 200
        assert (await client.get(V1_ALT_URL)).status_code == 200

        v1 = await client.get(V1_URL)
        v2 = await client.get(V2_URL)

    assert_rate_limited(v1, V1_BODY)
    assert_rate_limited(v2, V2_BODY)


async def test_rate_limit_different_ips_are_independent(limited: AppFactory) -> None:
    app, _ = await limited(per_minute=2)
    async with client_for(app, IP_A) as client_a, client_for(app, IP_B) as client_b:
        assert await statuses(client_a, V2_URL, 3) == [200, 200, 429]

        assert await statuses(client_b, V2_URL, 2) == [200, 200]
        assert (await client_a.get(V2_URL)).status_code == 429


async def test_rate_limit_api_key_param_does_not_change_the_key(limited: AppFactory) -> None:
    app, _ = await limited(per_minute=2)
    async with client_for(app) as client:
        await client.get(V1_URL, params={"api_key": "DEMO_KEY"})
        await client.get(V1_URL, params={"api_key": "key-2"})

        response = await client.get(V1_URL, params={"api_key": "key-3"})

    assert_rate_limited(response, V1_BODY)


# --- HEAD, 304, errors ---------------------------------------------------------------------


@pytest.mark.parametrize("url", [V2_URL, V1_URL], ids=["v2", "v1"])
async def test_rate_limit_head_requests_are_counted(limited: AppFactory, url: str) -> None:
    app, _ = await limited(per_minute=2)
    async with client_for(app) as client:
        assert await statuses(client, url, 2, method="HEAD") == [200, 200]

        head = await client.head(url)
        get = await client.get(url)

    assert head.status_code == 429
    assert head.content == b""
    assert head.headers.get("cache-control") == "no-store"
    retry_after(head)
    assert get.status_code == 429


@pytest.mark.parametrize("url", [V2_URL, V1_URL], ids=["v2", "v1"])
async def test_rate_limit_not_modified_requests_are_counted(limited: AppFactory, url: str) -> None:
    app, _ = await limited(per_minute=3)
    async with client_for(app) as client:
        first = await client.get(url)
        etag = first.headers["etag"]
        conditional = {"If-None-Match": etag}
        assert (await client.get(url, headers=conditional)).status_code == 304
        assert (await client.get(url, headers=conditional)).status_code == 304

        response = await client.get(url, headers=conditional)

    assert response.status_code == 429, "a limited client gets 429, not 304"
    assert response.headers.get("cache-control") == "no-store"
    retry_after(response)


async def test_rate_limit_error_responses_are_counted(limited: AppFactory) -> None:
    app, _ = await limited(per_minute=2)
    async with client_for(app) as client:
        assert (await client.get("/api/v2/rovers/pluto")).status_code == 404
        assert (await client.get("/api/v1/rovers/pluto")).status_code == 400

        response = await client.get(V2_URL)

    assert_rate_limited(response, V2_BODY)


# --- exempt paths --------------------------------------------------------------------------

EXEMPT = ["/", "/health", "/health/sync", "/docs", "/openapi.json"]


@pytest.mark.parametrize("path", EXEMPT, ids=EXEMPT)
async def test_rate_limit_exempt_path_is_never_limited(limited: AppFactory, path: str) -> None:
    app, _ = await limited(per_minute=1)
    async with client_for(app) as client:
        codes = await statuses(client, path, 5)
        assert 429 not in codes, codes
        # Exempt requests do not use up the API budget either.
        assert (await client.get(V2_URL)).status_code == 200


async def test_rate_limit_exempt_paths_still_answer_when_api_is_limited(
    limited: AppFactory,
) -> None:
    app, _ = await limited(per_minute=1)
    async with client_for(app) as client:
        await client.get(V2_URL)
        assert (await client.get(V2_URL)).status_code == 429

        for path in EXEMPT:
            assert (await client.get(path)).status_code != 429, path


# --- switched off --------------------------------------------------------------------------


async def test_rate_limit_disabled_never_returns_429(settings: Settings) -> None:
    disabled = limited_settings(settings, per_minute=1, per_hour=1).model_copy(
        update={"rate_limit_enabled": False}
    )
    app = create_app(disabled)
    async with running(app), client_for(app) as client:
        assert getattr(app.state, "rate_limiter", None) is None
        codes = await statuses(client, V2_URL, 5) + await statuses(client, V1_URL, 5)

    assert codes == [200] * 10


async def test_rate_limit_enabled_app_uses_both_windows(settings: Settings) -> None:
    app = create_app(limited_settings(settings, per_minute=7, per_hour=11))
    try:
        limiter = app.state.rate_limiter
        clock = Clock()
        limiter.clock = clock
        assert [limiter.hit("k") for _ in range(7)] == [None] * 7
        assert limiter.hit("k") is not None
        clock.advance(61)
        assert [limiter.hit("k") for _ in range(4)] == [None] * 4
        assert limiter.hit("k") is not None
    finally:
        await app.state.engine.dispose()
