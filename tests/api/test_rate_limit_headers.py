"""X-RateLimit-* headers (docs/API.md "Ограничение частоты", bullet "Заголовки лимита", 18D).

- Every response on a limited path (200, 404/400, 422, HEAD, 304, 429, admin) carries integer
  ``X-RateLimit-Limit``, ``X-RateLimit-Remaining``, ``X-RateLimit-Reset``.
- They describe the tighter window (less left after the request; tie -> frees later).
- Remaining >= 0; Reset is integer seconds >= 0 and equals Retry-After on a 429.
- No headers on exempt paths or with RATE_LIMIT_ENABLED=false; body and ETag do not depend on them.
- CORS exposes the three headers (and still ETag).

Time is moved through ``app.state.rate_limiter.clock``; nothing sleeps.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

V2_URL = "/api/v2/rovers"
V1_URL = "/api/v1/rovers"
V1_ALT_URL = "/mars-photos/api/v1/rovers"
ADMIN_RUNS = "/api/v2/admin/sync/runs"
ADMIN_TOKEN = "test-admin-token-18d"

LIMIT = "x-ratelimit-limit"
REMAINING = "x-ratelimit-remaining"
RESET = "x-ratelimit-reset"
HEADERS = (LIMIT, REMAINING, RESET)

IP_A = "203.0.113.31"


class Clock:
    def __init__(self, now: float = 90_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_settings(settings: Settings, per_minute: int, per_hour: int, **extra: object) -> Settings:
    return settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_per_minute": per_minute,
            "rate_limit_per_hour": per_hour,
            **extra,
        }
    )


def client_for(app: FastAPI, ip: str = IP_A) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


Build = Callable[..., Awaitable[tuple[FastAPI, Clock]]]


@pytest.fixture
async def build(settings: Settings) -> AsyncIterator[Build]:
    """Factory: an app inside its lifespan with the limiter on and a fake clock."""
    apps: list[tuple[FastAPI, object]] = []

    async def make(
        per_minute: int = 60, per_hour: int = 2000, **extra: object
    ) -> tuple[FastAPI, Clock]:
        app = create_app(make_settings(settings, per_minute, per_hour, **extra))
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        apps.append((app, context))
        clock = Clock()
        app.state.rate_limiter.clock = clock
        if getattr(app.state, "admin_auth_limiter", None) is not None:
            app.state.admin_auth_limiter.clock = clock
        return app, clock

    try:
        yield make
    finally:
        for app, context in reversed(apps):
            try:
                await context.__aexit__(None, None, None)  # type: ignore[attr-defined]
            finally:
                await app.state.engine.dispose()


def triple(response: httpx.Response) -> tuple[int, int, int]:
    """(limit, remaining, reset) as integers; fails when a header is missing or not an int."""
    values = []
    for name in HEADERS:
        raw = response.headers.get(name)
        assert raw is not None, f"missing {name}: {dict(response.headers)}"
        assert raw.isdigit(), f"{name} must be a non-negative integer, got {raw!r}"
        values.append(int(raw))
    return values[0], values[1], values[2]


def assert_no_headers(response: httpx.Response) -> None:
    present = [name for name in HEADERS if name in response.headers]
    assert not present, present


# --- present on every kind of response -------------------------------------------------------

CASES = [
    pytest.param("GET", V2_URL, 200, id="v2-200"),
    pytest.param("GET", V1_URL, 200, id="v1-200"),
    pytest.param("GET", V1_ALT_URL, 200, id="v1-mars-photos-200"),
    pytest.param("GET", "/api/v2/rovers/pluto", 404, id="v2-404"),
    pytest.param("GET", "/api/v1/rovers/pluto", 400, id="v1-error"),
    pytest.param("GET", "/mars-photos/api/v1/rovers/pluto", 400, id="v1-mars-photos-error"),
    pytest.param("GET", "/api/v2/photos/abc", 422, id="v2-422"),
    pytest.param("HEAD", V2_URL, 200, id="v2-head"),
    pytest.param("HEAD", V1_URL, 200, id="v1-head"),
    pytest.param("HEAD", V1_ALT_URL, 200, id="v1-mars-photos-head"),
]


@pytest.mark.parametrize(("method", "url", "status"), CASES)
async def test_rate_limit_headers_present_on_limited_path_response(
    build: Build, method: str, url: str, status: int
) -> None:
    app, _ = await build(per_minute=10, per_hour=100)
    async with client_for(app) as client:
        response = await client.request(method, url)

    assert response.status_code == status, response.text
    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (10, 9)
    assert 0 < reset <= 60


@pytest.mark.parametrize("url", [V2_URL, V1_URL, V1_ALT_URL], ids=["v2", "v1", "v1-mars-photos"])
async def test_rate_limit_headers_present_on_304(build: Build, url: str) -> None:
    app, _ = await build(per_minute=10, per_hour=100)
    async with client_for(app) as client:
        etag = (await client.get(url)).headers["etag"]
        response = await client.get(url, headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert triple(response)[:2] == (10, 8)


@pytest.mark.parametrize("url", [V2_URL, V1_URL, V1_ALT_URL], ids=["v2", "v1", "v1-mars-photos"])
async def test_rate_limit_headers_present_on_429(build: Build, url: str) -> None:
    app, _ = await build(per_minute=2, per_hour=100)
    async with client_for(app) as client:
        await client.get(url)
        await client.get(url)
        response = await client.get(url)

    assert response.status_code == 429
    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (2, 0)
    assert reset == int(response.headers["retry-after"])
    assert reset >= 1


async def test_rate_limit_headers_present_on_head_429(build: Build) -> None:
    app, _ = await build(per_minute=1, per_hour=100)
    async with client_for(app) as client:
        await client.head(V2_URL)
        response = await client.head(V2_URL)

    assert response.status_code == 429
    assert triple(response)[:2] == (1, 0)
    assert triple(response)[2] == int(response.headers["retry-after"])


async def test_rate_limit_headers_present_on_admin_responses(build: Build) -> None:
    app, _ = await build(
        per_minute=10,
        per_hour=100,
        admin_token=SecretStr(ADMIN_TOKEN),
        sync_enabled=False,
    )
    async with client_for(app) as client:
        ok = await client.get(ADMIN_RUNS, headers={"X-Admin-Token": ADMIN_TOKEN})
        unauthorized = await client.get(ADMIN_RUNS, headers={"X-Admin-Token": "wrong"})

    assert ok.status_code == 200
    assert unauthorized.status_code == 401
    assert triple(ok)[:2] == (10, 9)
    assert triple(unauthorized)[:2] == (10, 8)


async def test_rate_limit_headers_on_admin_bruteforce_429(build: Build) -> None:
    app, _ = await build(
        per_minute=100,
        per_hour=1000,
        admin_token=SecretStr(ADMIN_TOKEN),
        sync_enabled=False,
        admin_auth_max_failures=2,
        admin_auth_window_minutes=15,
    )
    async with client_for(app) as client:
        for _ in range(2):
            await client.get(ADMIN_RUNS, headers={"X-Admin-Token": "wrong"})
        response = await client.get(ADMIN_RUNS, headers={"X-Admin-Token": ADMIN_TOKEN})

    assert response.status_code == 429
    # The headers describe the general limiter (3 requests counted), not the admin one.
    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (100, 97)
    assert 0 < reset <= 60
    # Retry-After is the admin lockout wait and may differ from X-RateLimit-Reset.
    assert response.headers["retry-after"].isdigit()


# --- the tighter window ----------------------------------------------------------------------


async def test_rate_limit_headers_minute_window_tighter_decrement_by_one(build: Build) -> None:
    app, _ = await build(per_minute=4, per_hour=1000)
    async with client_for(app) as client:
        seen = [triple(await client.get(V2_URL)) for _ in range(4)]

    assert [(limit, remaining) for limit, remaining, _ in seen] == [
        (4, 3),
        (4, 2),
        (4, 1),
        (4, 0),
    ]
    assert all(0 <= reset <= 60 for _, _, reset in seen)


async def test_rate_limit_headers_hour_window_tighter_describes_hour(build: Build) -> None:
    app, _ = await build(per_minute=10, per_hour=4)
    async with client_for(app) as client:
        responses = [await client.get(V2_URL) for _ in range(4)]

    seen = [triple(r) for r in responses]
    assert [(limit, remaining) for limit, remaining, _ in seen] == [
        (4, 3),
        (4, 2),
        (4, 1),
        (4, 0),
    ]
    # Reset belongs to the hour window: the oldest hit leaves it only after ~3600 s.
    assert all(3590 <= reset <= 3600 for _, _, reset in seen)


async def test_rate_limit_headers_tie_with_remaining_left_reports_window_freeing_later(
    build: Build,
) -> None:
    app, _ = await build(per_minute=3, per_hour=3)
    async with client_for(app) as client:
        response = await client.get(V2_URL)

    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (3, 2)
    assert 3590 <= reset <= 3600, "equal Remaining: the hour window frees later"


async def test_rate_limit_headers_tie_with_different_limits_reports_larger_reset(
    build: Build,
) -> None:
    app, clock = await build(per_minute=3, per_hour=4)
    async with client_for(app) as client:
        await client.get(V2_URL)  # t=0
        clock.advance(70)
        response = await client.get(V2_URL)  # minute: 3-1=2 left, hour: 4-2=2 left

    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (4, 2)
    assert 3529 <= reset <= 3531


async def test_rate_limit_headers_429_with_hour_window_full_shows_hour_and_retry_after(
    build: Build,
) -> None:
    app, clock = await build(per_minute=3, per_hour=4)
    async with client_for(app) as client:
        for _ in range(3):
            await client.get(V2_URL)  # t=0: minute window full
        clock.advance(70)
        await client.get(V2_URL)  # hour window now full (4)
        response = await client.get(V2_URL)

    assert response.status_code == 429
    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (4, 0)
    assert reset == int(response.headers["retry-after"])
    assert 3529 <= reset <= 3531


async def test_rate_limit_headers_429_with_both_windows_full_reset_equals_retry_after(
    build: Build,
) -> None:
    app, _ = await build(per_minute=2, per_hour=2)
    async with client_for(app) as client:
        await client.get(V2_URL)
        await client.get(V2_URL)
        response = await client.get(V2_URL)

    assert response.status_code == 429
    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (2, 0)
    assert reset == int(response.headers["retry-after"])
    assert 3599 <= reset <= 3600


async def test_rate_limit_headers_absent_on_500(build: Build) -> None:
    app, _ = await build(per_minute=10, per_hour=100)

    async def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/api/v2/_boom", boom)
    async with client_for(app) as client:
        response = await client.get("/api/v2/_boom")

    assert response.status_code == 500
    assert_no_headers(response)


async def test_rate_limit_headers_switch_from_minute_to_hour_window(build: Build) -> None:
    app, clock = await build(per_minute=3, per_hour=5)
    async with client_for(app) as client:
        first = [triple(await client.get(V2_URL)) for _ in range(3)]  # minute tighter
        clock.advance(61)
        # Minute window is empty again (3 left), hour window has 2 left after the next hit.
        after = [triple(await client.get(V2_URL)) for _ in range(2)]

    assert [(limit, remaining) for limit, remaining, _ in first] == [(3, 2), (3, 1), (3, 0)]
    # Request 4: minute left 2, hour left 1 -> hour. Request 5: minute 1, hour 0 -> hour.
    assert [(limit, remaining) for limit, remaining, _ in after] == [(5, 1), (5, 0)]
    assert all(reset > 60 for _, _, reset in after)


async def test_rate_limit_headers_remaining_never_negative_and_zero_on_429s(build: Build) -> None:
    app, _ = await build(per_minute=2, per_hour=100)
    async with client_for(app) as client:
        responses = [await client.get(V2_URL) for _ in range(6)]

    assert [r.status_code for r in responses] == [200, 200, 429, 429, 429, 429]
    assert [triple(r)[1] for r in responses] == [1, 0, 0, 0, 0, 0]


async def test_rate_limit_headers_rejected_requests_do_not_decrement(build: Build) -> None:
    app, clock = await build(per_minute=2, per_hour=100)
    async with client_for(app) as client:
        await client.get(V2_URL)
        await client.get(V2_URL)
        for _ in range(5):
            assert (await client.get(V2_URL)).status_code == 429
        clock.advance(61)
        response = await client.get(V2_URL)

    assert response.status_code == 200
    assert triple(response)[:2] == (2, 1)


async def test_rate_limit_headers_remaining_counts_all_v1_v2_paths(build: Build) -> None:
    app, _ = await build(per_minute=5, per_hour=100)
    async with client_for(app) as client:
        remaining = [
            triple(await client.get(url))[1] for url in (V2_URL, V1_URL, V1_ALT_URL, V2_URL)
        ]

    assert remaining == [4, 3, 2, 1]


# --- reset -----------------------------------------------------------------------------------


async def test_rate_limit_headers_reset_counts_down_with_the_clock(build: Build) -> None:
    app, clock = await build(per_minute=5, per_hour=1000)
    async with client_for(app) as client:
        first = triple(await client.get(V2_URL))  # t=0
        clock.advance(20)
        second = triple(await client.get(V2_URL))

    assert 59 <= first[2] <= 60
    assert 39 <= second[2] <= 41  # the t=0 hit leaves the window 40 s from now


async def test_rate_limit_headers_reset_equals_retry_after_on_429(build: Build) -> None:
    app, clock = await build(per_minute=2, per_hour=1000)
    async with client_for(app) as client:
        await client.get(V2_URL)
        clock.advance(20)
        await client.get(V2_URL)
        clock.advance(10)
        response = await client.get(V2_URL)

    assert response.status_code == 429
    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (2, 0)
    assert reset == int(response.headers["retry-after"])
    assert 29 <= reset <= 31


async def test_rate_limit_headers_reset_equals_retry_after_when_hour_window_blocks(
    build: Build,
) -> None:
    app, clock = await build(per_minute=3, per_hour=4)
    async with client_for(app) as client:
        for _ in range(3):
            await client.get(V2_URL)
        clock.advance(61)
        await client.get(V2_URL)  # hour window now full
        response = await client.get(V2_URL)

    assert response.status_code == 429
    limit, remaining, reset = triple(response)
    assert (limit, remaining) == (4, 0)
    assert reset == int(response.headers["retry-after"])
    assert 3538 <= reset <= 3540


async def test_rate_limit_headers_window_rollover_restores_remaining(build: Build) -> None:
    app, clock = await build(per_minute=3, per_hour=1000)
    async with client_for(app) as client:
        for _ in range(3):
            await client.get(V2_URL)
        blocked = await client.get(V2_URL)
        clock.advance(int(blocked.headers["retry-after"]) + 1)
        response = await client.get(V2_URL)

    assert blocked.status_code == 429
    assert response.status_code == 200
    assert triple(response)[:2] == (3, 2)


async def test_rate_limit_headers_are_per_ip(build: Build) -> None:
    app, _ = await build(per_minute=3, per_hour=1000)
    async with client_for(app, "203.0.113.41") as a, client_for(app, "203.0.113.42") as b:
        await a.get(V2_URL)
        await a.get(V2_URL)
        first_b = await b.get(V2_URL)
        next_a = await a.get(V2_URL)

    assert triple(first_b)[:2] == (3, 2)
    assert triple(next_a)[:2] == (3, 0)


# --- absent ----------------------------------------------------------------------------------

EXEMPT = ["/", "/health", "/health/sync", "/docs", "/openapi.json", "/developers", "/llms.txt"]


@pytest.mark.parametrize("path", EXEMPT, ids=EXEMPT)
async def test_rate_limit_headers_absent_on_exempt_path(build: Build, path: str) -> None:
    app, _ = await build(per_minute=5, per_hour=100)
    async with client_for(app) as client:
        response = await client.get(path)

    assert response.status_code != 429  # /health/sync may legitimately answer 503 (stale)
    assert_no_headers(response)


async def test_rate_limit_headers_absent_on_exempt_path_when_api_is_limited(build: Build) -> None:
    app, _ = await build(per_minute=1, per_hour=100)
    async with client_for(app) as client:
        await client.get(V2_URL)
        assert (await client.get(V2_URL)).status_code == 429
        responses = [await client.get(path) for path in EXEMPT]

    for response in responses:
        assert response.status_code != 429
        assert_no_headers(response)


@pytest.mark.parametrize("url", [V2_URL, V1_URL, V1_ALT_URL, "/api/v2/rovers/pluto"])
async def test_rate_limit_headers_absent_when_limit_disabled(settings: Settings, url: str) -> None:
    app = create_app(make_settings(settings, 1, 1).model_copy(update={"rate_limit_enabled": False}))
    try:
        async with app.router.lifespan_context(app), client_for(app) as client:
            responses = [await client.get(url) for _ in range(3)]
    finally:
        await app.state.engine.dispose()

    for response in responses:
        assert response.status_code != 429
        assert_no_headers(response)


# --- CORS ------------------------------------------------------------------------------------


async def test_rate_limit_headers_exposed_by_cors_and_etag_kept(build: Build) -> None:
    app, _ = await build(per_minute=10, per_hour=100)
    async with client_for(app) as client:
        response = await client.get(V2_URL, headers={"Origin": "https://example.test"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in ("*", "https://example.test")
    exposed = [
        h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")
    ]
    for name in (*HEADERS, "etag"):
        assert name in exposed, exposed
    assert triple(response)[:2] == (10, 9)


async def test_rate_limit_headers_cors_exposed_on_429(build: Build) -> None:
    app, _ = await build(per_minute=1, per_hour=100)
    origin = {"Origin": "https://example.test"}
    async with client_for(app) as client:
        await client.get(V1_URL, headers=origin)
        response = await client.get(V1_URL, headers=origin)

    assert response.status_code == 429
    assert "access-control-allow-origin" in response.headers
    exposed = [
        h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")
    ]
    assert set(HEADERS) <= set(exposed)


async def test_rate_limit_headers_cors_preflight_is_not_broken(build: Build) -> None:
    app, _ = await build(per_minute=10, per_hour=100)
    async with client_for(app) as client:
        response = await client.options(
            V2_URL,
            headers={"Origin": "https://example.test", "Access-Control-Request-Method": "GET"},
        )

    assert response.status_code == 200
    assert "GET" in response.headers["access-control-allow-methods"]


# --- body and ETag are independent of the headers --------------------------------------------


@pytest.mark.parametrize("url", [V2_URL, V1_URL, V1_ALT_URL], ids=["v2", "v1", "v1-mars-photos"])
async def test_rate_limit_headers_do_not_change_body_or_etag(build: Build, url: str) -> None:
    app, _ = await build(per_minute=10, per_hour=100)
    async with client_for(app) as client:
        first = await client.get(url)
        second = await client.get(url)

    assert triple(first)[1] != triple(second)[1]
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]


async def test_rate_limit_headers_etag_same_as_without_limit(
    build: Build, settings: Settings
) -> None:
    app, _ = await build(per_minute=10, per_hour=100)
    plain = create_app(settings.model_copy(update={"rate_limit_enabled": False}))
    try:
        async with (
            client_for(app) as limited,
            plain.router.lifespan_context(plain),
            client_for(plain) as free,
        ):
            with_limit = await limited.get(V2_URL)
            without = await free.get(V2_URL)
    finally:
        await plain.state.engine.dispose()

    assert_no_headers(without)
    assert with_limit.content == without.content
    assert with_limit.headers["etag"] == without.headers["etag"]
