"""Admin brute-force limit (docs/API.md "Ограничение частоты", admin paragraph; stage 9).

- After ADMIN_AUTH_MAX_FAILURES ``401`` responses from one IP within
  ADMIN_AUTH_WINDOW_MINUTES, every admin request from that IP gets ``429 rate_limited`` with
  ``Retry-After`` until the window ends; the token is not checked, even a valid one.
- A successful authorization does not reset the counter of its IP.
- Works whether or not RATE_LIMIT_ENABLED is on (``app.state.admin_auth_limiter`` exists
  whenever admin is enabled).

Time is moved through ``app.state.admin_auth_limiter.clock``. Only GET /admin/sync/runs is
used: it reads sync_runs and never touches the upstream.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from tests.api.v2.conftest import V2, assert_error

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

ADMIN_TOKEN = "test-admin-token-stage9"
GOOD = {"X-Admin-Token": ADMIN_TOKEN}
BAD = {"X-Admin-Token": "wrong-token"}
RUNS = f"{V2}/admin/sync/runs"
SYNC = f"{V2}/admin/sync"
MAX_FAILURES = 3
WINDOW_MINUTES = 15
WINDOW = WINDOW_MINUTES * 60

IP_A = "198.51.100.7"
IP_B = "198.51.100.8"


class Clock:
    def __init__(self, now: float = 70_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


Build = Callable[..., Awaitable[tuple[FastAPI, Clock]]]


@pytest.fixture
async def build(settings: Settings) -> AsyncIterator[Build]:
    """Factory: an admin-enabled app inside its lifespan, with a fake admin-limiter clock."""
    apps: list[tuple[FastAPI, object]] = []

    async def make(*, rate_limit: bool = False, per_minute: int = 1000) -> tuple[FastAPI, Clock]:
        app = create_app(
            settings.model_copy(
                update={
                    "admin_token": SecretStr(ADMIN_TOKEN),
                    "sync_enabled": False,
                    "rate_limit_enabled": rate_limit,
                    "rate_limit_per_minute": per_minute,
                    "rate_limit_per_hour": 100_000,
                    "admin_auth_max_failures": MAX_FAILURES,
                    "admin_auth_window_minutes": WINDOW_MINUTES,
                }
            )
        )
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        apps.append((app, context))
        clock = Clock()
        app.state.admin_auth_limiter.clock = clock
        if rate_limit:
            app.state.rate_limiter.clock = clock
        return app, clock

    try:
        yield make
    finally:
        for app, context in reversed(apps):
            try:
                await context.__aexit__(None, None, None)  # type: ignore[attr-defined]
            finally:
                await app.state.engine.dispose()


def client_for(app: FastAPI, ip: str = IP_A) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 50000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def fail(client: httpx.AsyncClient, n: int) -> None:
    for _ in range(n):
        assert_error(await client.get(RUNS, headers=BAD), 401, "unauthorized")


def assert_admin_limited(response: httpx.Response) -> int:
    error = assert_error(response, 429, "rate_limited")
    assert error == {"code": "rate_limited", "message": "Rate limit exceeded", "details": None}
    assert response.headers.get("cache-control") == "no-store"
    value = response.headers.get("retry-after", "")
    assert value.isdigit(), dict(response.headers)
    seconds = int(value)
    assert 1 <= seconds <= WINDOW
    return seconds


def test_admin_auth_settings_defaults() -> None:
    assert Settings.model_fields["admin_auth_max_failures"].default == 10
    assert Settings.model_fields["admin_auth_window_minutes"].default == 15


async def test_admin_failures_below_limit_keep_401_and_allow_valid_token(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES - 1)

        response = await client.get(RUNS, headers=GOOD)

    assert response.status_code == 200, response.text


async def test_admin_after_max_failures_valid_token_returns_429(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)

        response = await client.get(RUNS, headers=GOOD)

    seconds = assert_admin_limited(response)
    assert seconds >= WINDOW - 1  # the failures happened "now": almost the whole window


async def test_admin_after_max_failures_wrong_token_returns_429_not_401(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)

        response = await client.get(RUNS, headers=BAD)

    assert_admin_limited(response)


async def test_admin_blocked_sync_post_returns_429_before_body(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)

        response = await client.post(SYNC, headers=GOOD, json={"rover": "curiosity"})

    assert_admin_limited(response)
    assert not app.state.sync_tasks


async def test_admin_retry_after_counts_to_end_of_window(build: Build) -> None:
    app, clock = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)
        clock.advance(600)

        response = await client.get(RUNS, headers=GOOD)

    seconds = assert_admin_limited(response)
    assert WINDOW - 600 - 1 <= seconds <= WINDOW - 600 + 1


async def test_admin_after_window_valid_token_returns_200(build: Build) -> None:
    app, clock = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)
        blocked = await client.get(RUNS, headers=GOOD)
        clock.advance(assert_admin_limited(blocked) + 0.01)

        response = await client.get(RUNS, headers=GOOD)

    assert response.status_code == 200, response.text
    assert list(response.json()) == ["items", "page", "per_page", "total"]


async def test_admin_blocked_requests_do_not_extend_block(build: Build) -> None:
    app, clock = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)
        for _ in range(5):
            clock.advance(120)
            assert_admin_limited(await client.get(RUNS, headers=BAD))
        clock.advance(WINDOW - 600 + 0.01)

        response = await client.get(RUNS, headers=GOOD)

    assert response.status_code == 200, response.text


async def test_admin_success_does_not_reset_failure_counter(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES - 1)
        assert (await client.get(RUNS, headers=GOOD)).status_code == 200
        await fail(client, 1)

        response = await client.get(RUNS, headers=GOOD)

    assert_admin_limited(response)


async def test_admin_successes_alone_are_not_limited(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        codes = [(await client.get(RUNS, headers=GOOD)).status_code for _ in range(10)]

    assert codes == [200] * 10


async def test_admin_block_of_one_ip_does_not_affect_another(build: Build) -> None:
    app, _ = await build()
    async with client_for(app, IP_A) as client_a, client_for(app, IP_B) as client_b:
        await fail(client_a, MAX_FAILURES)
        assert_admin_limited(await client_a.get(RUNS, headers=GOOD))

        response = await client_b.get(RUNS, headers=GOOD)
        assert response.status_code == 200, response.text
        assert_error(await client_b.get(RUNS, headers=BAD), 401, "unauthorized")


async def test_admin_block_does_not_affect_public_endpoints(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)

        response = await client.get(f"{V2}/rovers")

    assert response.status_code == 200, response.text


async def test_admin_failure_limit_works_with_rate_limit_disabled(build: Build) -> None:
    app, _ = await build(rate_limit=False)
    assert getattr(app.state, "rate_limiter", None) is None
    async with client_for(app) as client:
        await fail(client, MAX_FAILURES)

        response = await client.get(RUNS, headers=GOOD)

    assert_admin_limited(response)


async def test_admin_requests_count_toward_general_rate_limit(build: Build) -> None:
    app, _ = await build(rate_limit=True, per_minute=2)
    async with client_for(app) as client:
        assert (await client.get(RUNS, headers=GOOD)).status_code == 200
        assert (await client.get(RUNS, headers=GOOD)).status_code == 200

        response = await client.get(RUNS, headers=GOOD)

    error = assert_error(response, 429, "rate_limited")
    assert error["message"] == "Rate limit exceeded"
    assert response.headers.get("retry-after", "").isdigit()
