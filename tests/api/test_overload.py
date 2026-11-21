"""Overload protection (docs/API.md "Ограничение частоты": bullets "Одновременные запросы" and
"Перегрузка", error `service_busy`; docs/LEGACY-API.md error table; stage 18B).

(a) A database statement over DB_STATEMENT_TIMEOUT_SECONDS or an exhausted connection pool
    -> 503, `Retry-After: 5`, `Cache-Control: no-store`; v2 `service_busy`, v1
    `{"errors": "Server is busy"}`. A plain connection failure stays `db_unavailable`.
(b) Per-IP concurrency (RATE_LIMIT_CONCURRENT): the request over the cap gets the usual 429
    with `Retry-After: 1` and X-RateLimit-* headers and is not counted in the windows; other
    IPs are unaffected; slots are freed on completion, exception and client disconnect;
    unlimited paths and RATE_LIMIT_ENABLED=false are not affected.

Requests are held open by test-only routes waiting on an asyncio.Event; time is the app's
fake clock; nothing sleeps.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from asyncpg.exceptions import DiskFullError, OutOfMemoryError, QueryCanceledError
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.config import Settings
from app.main import create_app
from tests.api.test_db_unavailable import CONNECTION_ERRORS, _FailingFactory

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

V2_URL = "/api/v2/rovers"
V1_URL = "/api/v1/rovers"
V2_BUSY = {
    "error": {"code": "service_busy", "message": "Server is busy, retry later", "details": None}
}
V1_BUSY = {"errors": "Server is busy"}
V2_LIMITED = {"error": {"code": "rate_limited", "message": "Rate limit exceeded", "details": None}}
V1_LIMITED = {"errors": "Rate limit exceeded"}

IP_A = "203.0.113.41"
IP_B = "203.0.113.42"


def client_for(app: FastAPI, ip: str = IP_A) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class Clock:
    def __init__(self) -> None:
        self.now = 70_000.0

    def __call__(self) -> float:
        return self.now


# --- (a) database overload ---------------------------------------------------------------------

_TIMEOUT_MSG = "canceling statement due to statement timeout"
BUSY_ERRORS = [
    PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached, connection timed out"),
    DBAPIError("SELECT 1", {}, QueryCanceledError(_TIMEOUT_MSG)),
    DBAPIError("SELECT 1", {}, DiskFullError("could not write to temporary file: no space")),
    DBAPIError("SELECT 1", {}, OutOfMemoryError("out of memory")),
]
BUSY_IDS = ["pool-exhausted", "statement-timeout", "disk-full", "out-of-memory"]


@pytest.fixture
async def plain_app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    try:
        yield app
    finally:
        await app.state.engine.dispose()


def assert_busy(response: httpx.Response, body: dict[str, Any]) -> None:
    assert response.status_code == 503, response.text
    assert response.json() == body
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


@pytest.mark.parametrize("error", BUSY_ERRORS, ids=BUSY_IDS)
@pytest.mark.parametrize(
    "url", [V2_URL, "/api/v2/rovers/curiosity/photos?sol=1"], ids=["rovers", "photos"]
)
async def test_v2_database_overload_returns_503_service_busy(
    plain_app: FastAPI, url: str, error: BaseException
) -> None:
    plain_app.state.session_factory = _FailingFactory(error)

    async with client_for(plain_app) as client:
        response = await client.get(url)

    assert_busy(response, V2_BUSY)
    assert list(response.json()["error"]) == ["code", "message", "details"]


@pytest.mark.parametrize("error", BUSY_ERRORS, ids=BUSY_IDS)
@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_v1_database_overload_returns_503_server_is_busy(
    plain_app: FastAPI, prefix: str, error: BaseException
) -> None:
    plain_app.state.session_factory = _FailingFactory(error)

    async with client_for(plain_app) as client:
        response = await client.get(f"{prefix}/rovers")

    assert_busy(response, V1_BUSY)


@pytest.mark.parametrize(
    "error", CONNECTION_ERRORS[:2], ids=["operational-error", "connection-refused"]
)
async def test_plain_connection_failure_stays_db_unavailable_not_busy(
    plain_app: FastAPI, error: BaseException
) -> None:
    plain_app.state.session_factory = _FailingFactory(error)

    async with client_for(plain_app) as client:
        v2 = await client.get(V2_URL)
        v1 = await client.get(V1_URL)

    assert v2.status_code == 503 and v2.json()["error"]["code"] == "db_unavailable"
    assert v1.status_code == 503 and v1.json() == {"errors": "Database unavailable"}


async def test_operational_error_without_cancellation_is_not_busy(plain_app: FastAPI) -> None:
    plain_app.state.session_factory = _FailingFactory(
        OperationalError("SELECT 1", {}, ConnectionResetError("reset by peer"))
    )

    async with client_for(plain_app) as client:
        response = await client.get(V2_URL)

    assert response.json()["error"]["code"] == "db_unavailable"


@pytest.fixture
async def timeout_app(settings: Settings) -> AsyncIterator[FastAPI]:
    """App whose statements may run 0.2 s; test-only routes run a 3 s `pg_sleep` on it."""
    app = create_app(settings.model_copy(update={"db_statement_timeout_seconds": 0.2}))

    async def sleepy() -> dict[str, bool]:
        async with app.state.session_factory() as session:
            await session.execute(text("SELECT pg_sleep(3)"))
        return {"ok": True}

    for path in ("/api/v2/__sleep", "/api/v1/__sleep"):
        app.add_api_route(path, sleepy, methods=["GET"])
    try:
        yield app
    finally:
        await app.state.engine.dispose()


async def test_real_statement_timeout_on_the_api_path_returns_v2_service_busy(
    timeout_app: FastAPI,
) -> None:
    async with client_for(timeout_app) as client:
        response = await client.get("/api/v2/__sleep")

    assert_busy(response, V2_BUSY)


async def test_real_statement_timeout_on_the_api_path_returns_v1_server_is_busy(
    timeout_app: FastAPI,
) -> None:
    async with client_for(timeout_app) as client:
        response = await client.get("/api/v1/__sleep")

    assert_busy(response, V1_BUSY)


async def test_normal_queries_still_work_under_a_short_statement_timeout(
    timeout_app: FastAPI,
) -> None:
    async with client_for(timeout_app) as client:
        response = await client.get(V2_URL)

    assert response.status_code == 200


# --- (b) per-IP concurrency ----------------------------------------------------------------------


class Hold:
    """Test-only routes: `/__hold` waits for `release`, `/__boom` raises."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = 0

    async def wait_entered(self, n: int) -> None:
        for _ in range(100_000):
            if self.entered >= n:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"only {self.entered} requests reached the handler, wanted {n}")


@pytest.fixture
async def make_app(
    settings: Settings,
) -> AsyncIterator[Callable[..., Awaitable[tuple[FastAPI, Hold]]]]:
    started: list[Any] = []

    async def build(
        concurrent: int = 2, enabled: bool = True, per_minute: int = 10
    ) -> tuple[FastAPI, Hold]:
        app = create_app(
            settings.model_copy(
                update={
                    "rate_limit_enabled": enabled,
                    "rate_limit_per_minute": per_minute,
                    "rate_limit_per_hour": 1000,
                    "rate_limit_concurrent": concurrent,
                }
            )
        )
        hold = Hold()

        async def held() -> dict[str, bool]:
            hold.entered += 1
            await hold.release.wait()
            return {"ok": True}

        async def boom() -> None:
            raise ValueError("boom")

        async def busy() -> None:
            raise PoolTimeoutError("pool exhausted")

        for prefix in ("/api/v2", "/api/v1"):
            app.add_api_route(f"{prefix}/__hold", held, methods=["GET"])
            app.add_api_route(f"{prefix}/__boom", boom, methods=["GET"])
            app.add_api_route(f"{prefix}/__busy", busy, methods=["GET"])
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        started.append((app, context))
        if enabled:
            app.state.rate_limiter.clock = Clock()
        return app, hold

    try:
        yield build
    finally:
        for app, context in reversed(started):
            try:
                await context.__aexit__(None, None, None)
            finally:
                await app.state.engine.dispose()


async def open_requests(
    app: FastAPI, hold: Hold, n: int, path: str = "/api/v2/__hold", ip: str = IP_A
) -> tuple[list["asyncio.Task[httpx.Response]"], httpx.AsyncClient]:
    client = client_for(app, ip)
    tasks = [asyncio.ensure_future(client.get(path)) for _ in range(n)]
    await hold.wait_entered(hold.entered + n if hold.entered else n)
    return tasks, client


async def finish(hold: Hold, tasks: list["asyncio.Task[httpx.Response]"]) -> list[httpx.Response]:
    hold.release.set()
    return list(await asyncio.gather(*tasks))


@pytest.mark.parametrize(
    ("path", "other", "body"),
    [
        ("/api/v2/__hold", V2_URL, V2_LIMITED),
        ("/api/v1/__hold", V1_URL, V1_LIMITED),
    ],
    ids=["v2", "v1"],
)
async def test_request_over_the_concurrency_cap_gets_429_with_retry_after_1(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
    path: str,
    other: str,
    body: dict[str, Any],
) -> None:
    app, hold = await make_app(concurrent=2)
    tasks, client = await open_requests(app, hold, 2, path)
    try:
        same_path = await client.get(path)
        other_path = await client.get(other)  # the cap is per IP, not per path
    finally:
        await finish(hold, tasks)
        await client.aclose()

    for response in (same_path, other_path):
        assert response.status_code == 429, response.text
        assert response.json() == body
        assert response.headers["retry-after"] == "1"
        assert response.headers["cache-control"] == "no-store"
        for name in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
            assert response.headers[name].isdigit(), (name, dict(response.headers))


async def test_rejected_request_is_not_counted_in_the_windows(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
) -> None:
    app, hold = await make_app(concurrent=2, per_minute=10)
    tasks, client = await open_requests(app, hold, 2)
    try:
        rejected = [await client.get(V2_URL) for _ in range(3)]
    finally:
        done = await finish(hold, tasks)
    after = await client.get(V2_URL)
    await client.aclose()

    assert [r.status_code for r in done] == [200, 200]
    assert {r.status_code for r in rejected} == {429}
    # Two admitted requests used 2 of 10; the three rejected ones used nothing.
    assert {r.headers["x-ratelimit-remaining"] for r in rejected} == {"8"}
    assert rejected[0].headers["x-ratelimit-limit"] == "10"
    assert after.status_code == 200
    assert after.headers["x-ratelimit-remaining"] == "7"


async def test_another_ip_is_not_limited_by_a_busy_ip(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
) -> None:
    app, hold = await make_app(concurrent=2)
    tasks, client_a = await open_requests(app, hold, 2)
    try:
        async with client_for(app, IP_B) as client_b:
            response_b = await client_b.get(V2_URL)
        response_a = await client_a.get(V2_URL)
    finally:
        await finish(hold, tasks)
        await client_a.aclose()

    assert response_b.status_code == 200
    assert response_a.status_code == 429


async def test_next_request_passes_after_the_held_ones_finish(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
) -> None:
    app, hold = await make_app(concurrent=2)
    tasks, client = await open_requests(app, hold, 2)
    assert (await client.get(V2_URL)).status_code == 429

    done = await finish(hold, tasks)
    after = [await client.get(V2_URL) for _ in range(3)]
    await client.aclose()

    assert [r.status_code for r in done] == [200, 200]
    assert [r.status_code for r in after] == [200, 200, 200]


async def test_one_finished_request_frees_exactly_one_slot(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
) -> None:
    app, hold = await make_app(concurrent=2)
    tasks, client = await open_requests(app, hold, 2)
    tasks[0].cancel()
    await asyncio.gather(tasks[0], return_exceptions=True)
    for _ in range(20):
        await asyncio.sleep(0)

    probe = asyncio.ensure_future(client.get("/api/v2/__hold"))
    await hold.wait_entered(3)
    over = await client.get(V2_URL)
    await finish(hold, [tasks[1], probe])
    await client.aclose()

    assert over.status_code == 429


@pytest.mark.parametrize("path", ["boom", "busy"])
@pytest.mark.parametrize("prefix", ["/api/v2", "/api/v1"])
async def test_slot_is_released_when_the_handler_raises(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]], prefix: str, path: str
) -> None:
    app, _ = await make_app(concurrent=1, per_minute=100)
    async with client_for(app) as client:
        failed = [await client.get(f"{prefix}/__{path}") for _ in range(4)]
        ok = await client.get(V2_URL)

    assert 429 not in {r.status_code for r in failed}
    assert {r.status_code for r in failed} <= {500, 503}
    assert ok.status_code == 200


async def test_slot_is_released_when_the_client_disconnects(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
) -> None:
    app, hold = await make_app(concurrent=2)
    tasks, client = await open_requests(app, hold, 2)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for _ in range(20):
        await asyncio.sleep(0)

    after = [await client.get(V2_URL) for _ in range(3)]
    await client.aclose()

    assert [r.status_code for r in after] == [200, 200, 200]


async def test_unlimited_paths_ignore_the_concurrency_cap(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
) -> None:
    app, hold = await make_app(concurrent=2)
    tasks, client = await open_requests(app, hold, 2)
    try:
        health = await client.get("/health")
        docs = await client.get("/openapi.json")
    finally:
        await finish(hold, tasks)
        await client.aclose()

    assert health.status_code == 200
    assert docs.status_code == 200
    for response in (health, docs):
        assert "x-ratelimit-limit" not in response.headers


async def test_disabled_rate_limit_has_no_concurrency_cap(
    make_app: Callable[..., Awaitable[tuple[FastAPI, Hold]]],
) -> None:
    app, hold = await make_app(concurrent=1, enabled=False)
    tasks, client = await open_requests(app, hold, 3)
    try:
        extra = await client.get(V2_URL)
    finally:
        done = await finish(hold, tasks)
        await client.aclose()

    assert extra.status_code == 200
    assert [r.status_code for r in done] == [200, 200, 200]
    assert "x-ratelimit-limit" not in extra.headers
