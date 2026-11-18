"""Admin endpoints (docs/API.md "Admin"; stage 6 contract, section 2).

- Routes exist only when ADMIN_TOKEN is set and non-empty, otherwise 404 not_found.
- X-Admin-Token is checked before the body: missing or wrong -> 401 unauthorized.
- POST /api/v2/admin/sync -> 202 {"run_id", "status": "running"}; the sync goes on in the
  background (tasks kept in `app.state.sync_tasks`, awaited here); 409 sync_in_progress when
  the rover lock is held (checked before 202); 404 rover_not_found; 422 validation_error.
- GET /api/v2/admin/sync/runs -> paginated runs, `started_at DESC`, `failed_sols` ascending.
- Every admin response has `Cache-Control: no-store`.

The upstream is replaced through the agreed hook `app.state.adapter_factory` with
`FakeAdapter` (no network). The app runs its real lifespan, so shutdown is exercised too.
This module commits data and uses `clean_db` only (never `v2_world`).
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import NoSourceForRover
from app.main import create_app
from app.services.sync import SYNC_LOCK_NAMESPACE
from app.sources import http
from app.sources.registry import SYNCABLE_ROVERS
from tests.api.v2.conftest import V2, assert_error, assert_json, assert_validation_error
from tests.integration._fakes import FakeAdapter, legacy_id, photo_count, sol_records, sync_runs

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("clean_db")]

Factory = async_sessionmaker[AsyncSession]

ADMIN_TOKEN = "test-admin-token"
AUTH = {"X-Admin-Token": ADMIN_TOKEN}
SYNC = f"{V2}/admin/sync"
RUNS = f"{V2}/admin/sync/runs"
OVERLAP = 7
CHUNK = 10
NO_STORE = "no-store"
WAIT = 10  # seconds; generous upper bound, the fakes finish in milliseconds

RUN_KEYS = [
    "id",
    "rover",
    "mode",
    "from_sol",
    "to_sol",
    "status",
    "started_at",
    "finished_at",
    "photos_upserted",
    "failed_sols",
    "error",
]
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


# --- fixtures and helpers ------------------------------------------------------------------


class Adapters:
    """`app.state.adapter_factory` double: one FakeAdapter per rover, like the registry.

    Rovers without a source raise `NoSourceForRover`, as `registry.build_adapter` does.
    """

    def __init__(self) -> None:
        self.by_rover: dict[str, FakeAdapter] = {}
        self.calls: list[str] = []
        self.error: BaseException | None = None

    def __call__(self, rover_id: str) -> FakeAdapter:
        self.calls.append(rover_id)
        if self.error is not None:
            raise self.error
        key = rover_id.lower()
        if key not in SYNCABLE_ROVERS:
            raise NoSourceForRover(rover_id)
        return self.by_rover.setdefault(key, FakeAdapter(key))

    def set(self, adapter: FakeAdapter) -> FakeAdapter:
        self.by_rover[adapter.rover_id] = adapter
        return adapter


def admin_settings(settings: Settings, token: str | None = ADMIN_TOKEN) -> Settings:
    return settings.model_copy(
        update={
            "admin_token": None if token is None else SecretStr(token),
            "sync_enabled": False,
            "sync_overlap_sols": OVERLAP,
            "sync_chunk_sols": CHUNK,
        }
    )


def make_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def adapters() -> Adapters:
    return Adapters()


@pytest.fixture
async def admin_app(settings: Settings, adapters: Adapters) -> AsyncIterator[FastAPI]:
    """Admin-enabled app inside its real lifespan, upstream replaced by `adapters`."""
    app = create_app(admin_settings(settings))
    try:
        async with app.router.lifespan_context(app):
            app.state.adapter_factory = adapters
            yield app
    finally:
        await app.state.engine.dispose()


@pytest.fixture
async def admin(admin_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(admin_app) as client:
        yield client


async def drain(app: FastAPI) -> None:
    """Await every background sync of the app (the agreed `app.state.sync_tasks` hook)."""
    tasks = list(app.state.sync_tasks)
    if tasks:
        await asyncio.wait(tasks, timeout=WAIT)
    assert all(task.done() for task in tasks), "background sync did not finish"


def upstream_error(message: str = "boom") -> http.UpstreamError:
    return http.UpstreamError("https://feed.example.test/", 500, message)


class HeldLock:
    """Holds the rover sync lock (SYNC_LOCK_NAMESPACE, rovers.legacy_id) from the test."""

    def __init__(self, factory: Factory, rover_id: str) -> None:
        self.factory = factory
        self.rover_id = rover_id

    async def __aenter__(self) -> "HeldLock":
        self.params = {
            "ns": SYNC_LOCK_NAMESPACE,
            "key": await legacy_id(self.factory, self.rover_id),
        }
        self.connection = await self.factory.kw["bind"].connect()
        await self.connection.execute(text("SELECT pg_advisory_lock(:ns, :key)"), self.params)
        await self.connection.commit()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.connection.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), self.params)
        await self.connection.close()


async def lock_is_free(factory: Factory, rover_id: str) -> bool:
    params = {"ns": SYNC_LOCK_NAMESPACE, "key": await legacy_id(factory, rover_id)}
    async with factory.kw["bind"].connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:ns, :key)"), params)
        if acquired:
            await connection.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), params)
        await connection.commit()
        return bool(acquired)


async def insert_run(factory: Factory, **values: Any) -> int:
    row: dict[str, Any] = {
        "rover_id": "curiosity",
        "mode": "backfill",
        "from_sol": 0,
        "to_sol": 10,
        "status": "success",
        "started_at": datetime(2026, 11, 1, 6, 0, 0, tzinfo=UTC),
        "finished_at": None,
        "photos_upserted": 0,
        "failed_sols": [],
        "error": None,
    }
    row.update(values)
    async with factory() as session:
        run_id = await session.scalar(
            text(
                "INSERT INTO sync_runs (rover_id, mode, from_sol, to_sol, status, started_at, "
                "finished_at, photos_upserted, failed_sols, error) VALUES (:rover_id, :mode, "
                ":from_sol, :to_sol, :status, :started_at, :finished_at, :photos_upserted, "
                ":failed_sols, :error) RETURNING id"
            ),
            row,
        )
        await session.commit()
    return int(run_id)


def assert_no_store(response: httpx.Response) -> None:
    assert response.headers.get("cache-control") == NO_STORE, dict(response.headers)
    assert "etag" not in response.headers


def assert_accepted(response: httpx.Response) -> int:
    assert response.status_code == 202, response.text
    body = response.json()
    assert list(body) == ["run_id", "status"]
    assert body["status"] == "running"
    assert isinstance(body["run_id"], int) and body["run_id"] > 0
    assert_no_store(response)
    return body["run_id"]


async def get_run(client: httpx.AsyncClient, run_id: int) -> dict[str, Any]:
    response = await client.get(RUNS, headers=AUTH, params={"per_page": 100})
    assert response.status_code == 200, response.text
    [run] = [item for item in response.json()["items"] if item["id"] == run_id]
    assert list(run) == RUN_KEYS
    return run


# --- admin disabled ------------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
@pytest.mark.parametrize(("method", "url"), [("POST", SYNC), ("GET", RUNS)], ids=["sync", "runs"])
async def test_admin_routes_without_admin_token_return_404(
    settings: Settings, token: str | None, method: str, url: str
) -> None:
    app = create_app(admin_settings(settings, token))
    try:
        async with make_client(app) as client:
            response = await client.request(
                method,
                url,
                headers={"X-Admin-Token": token or "anything"},
                json={"rover": "curiosity"},
            )
    finally:
        await app.state.engine.dispose()

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
async def test_admin_routes_without_admin_token_are_not_in_openapi(
    settings: Settings, token: str | None
) -> None:
    app = create_app(admin_settings(settings, token))
    try:
        async with make_client(app) as client:
            paths = (await client.get("/openapi.json")).json()["paths"]
    finally:
        await app.state.engine.dispose()

    assert not [path for path in paths if "/admin" in path]


# --- authentication ------------------------------------------------------------------------

BAD_HEADERS = [
    {},
    {"X-Admin-Token": ""},
    {"X-Admin-Token": "wrong"},
    {"X-Admin-Token": ADMIN_TOKEN.upper()},
    {"X-Admin-Token": ADMIN_TOKEN + "x"},
    {"X-Admin-Token": ADMIN_TOKEN[:-1]},
    {"Authorization": f"Bearer {ADMIN_TOKEN}"},
]
BAD_HEADER_IDS = ["missing", "empty", "wrong", "other-case", "longer", "prefix", "bearer"]


@pytest.mark.parametrize("headers", BAD_HEADERS, ids=BAD_HEADER_IDS)
async def test_admin_sync_without_valid_token_returns_401(
    admin: httpx.AsyncClient, adapters: Adapters, clean_db: Factory, headers: dict[str, str]
) -> None:
    response = await admin.post(SYNC, headers=headers, json={"rover": "curiosity"})

    assert_error(response, 401, "unauthorized")
    assert_no_store(response)
    assert adapters.calls == []
    assert await sync_runs(clean_db) == []


@pytest.mark.parametrize("headers", BAD_HEADERS, ids=BAD_HEADER_IDS)
async def test_admin_runs_without_valid_token_returns_401(
    admin: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await admin.get(RUNS, headers=headers)

    assert_error(response, 401, "unauthorized")
    assert_no_store(response)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": b"not json", "headers": {"Content-Type": "application/json"}},
        {"json": {"rover": 5}},
        {"json": {"rover": "spirit"}},
        {"json": {"rover": "pluto"}},
        {"json": {"rover": "curiosity", "from_sol": 5, "to_sol": 1}},
        {"json": {}},
    ],
    ids=["invalid-json", "rover-int", "no-source", "unknown-rover", "from-gt-to", "empty"],
)
async def test_admin_sync_wrong_token_is_checked_before_body(
    admin: httpx.AsyncClient, adapters: Adapters, kwargs: dict[str, Any]
) -> None:
    headers = {**kwargs.pop("headers", {}), "X-Admin-Token": "wrong"}

    response = await admin.post(SYNC, headers=headers, **kwargs)

    assert_error(response, 401, "unauthorized")
    assert adapters.calls == []


async def test_admin_runs_wrong_token_is_checked_before_query(admin: httpx.AsyncClient) -> None:
    response = await admin.get(
        RUNS, headers={"X-Admin-Token": "wrong"}, params={"page": 0, "rover": "pluto"}
    )

    assert_error(response, 401, "unauthorized")


# --- 202: the sync runs in the background ----------------------------------------------------


async def test_admin_sync_backfill_returns_202_and_run_finishes(
    admin_app: FastAPI, admin: httpx.AsyncClient, adapters: Adapters, clean_db: Factory
) -> None:
    adapter = adapters.set(FakeAdapter("curiosity"))
    adapter.add(sol_records("curiosity", 3, 2)).add(sol_records("curiosity", 5, 1))

    response = await admin.post(
        SYNC, headers=AUTH, json={"rover": "curiosity", "from_sol": 0, "to_sol": 5}
    )

    run_id = assert_accepted(response)
    assert admin_app.state.sync_tasks, "the background sync must be tracked in app.state.sync_tasks"
    await drain(admin_app)
    run = await get_run(admin, run_id)
    assert {k: v for k, v in run.items() if k not in ("started_at", "finished_at")} == {
        "id": run_id,
        "rover": "curiosity",
        "mode": "backfill",
        "from_sol": 0,
        "to_sol": 5,
        "status": "success",
        "photos_upserted": 3,
        "failed_sols": [],
        "error": None,
    }
    assert ISO_Z.match(run["started_at"]) and ISO_Z.match(run["finished_at"]), run
    assert run["finished_at"] >= run["started_at"]
    assert adapter.fetch_calls == [(0, 5)]
    assert await photo_count(clean_db, "curiosity") == 3
    assert await lock_is_free(clean_db, "curiosity")
    assert not admin_app.state.sync_tasks, "finished tasks must be dropped from the set"


async def test_admin_sync_run_is_running_until_background_finishes(
    admin_app: FastAPI, admin: httpx.AsyncClient, adapters: Adapters, clean_db: Factory
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block(from_sol: int, to_sol: int) -> None:
        entered.set()
        await release.wait()

    adapters.set(FakeAdapter("perseverance", on_fetch=block)).add(sol_records("perseverance", 2, 1))
    run_id = assert_accepted(
        await admin.post(
            SYNC, headers=AUTH, json={"rover": "perseverance", "from_sol": 0, "to_sol": 2}
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=WAIT)
        running = await get_run(admin, run_id)
        assert (running["status"], running["finished_at"], running["error"]) == (
            "running",
            None,
            None,
        )
        assert running["failed_sols"] == []
        assert not await lock_is_free(clean_db, "perseverance")
    finally:
        release.set()
        await drain(admin_app)

    finished = await get_run(admin, run_id)
    assert (finished["status"], finished["photos_upserted"]) == ("success", 1)
    assert await lock_is_free(clean_db, "perseverance")


@pytest.mark.parametrize("rover", ["curiosity", "Curiosity", "CURIOSITY"])
async def test_admin_sync_incremental_without_range_rover_case_insensitive(
    admin_app: FastAPI, admin: httpx.AsyncClient, adapters: Adapters, rover: str
) -> None:
    adapter = adapters.set(FakeAdapter("curiosity", latest=10))
    adapter.add(sol_records("curiosity", 9, 1))

    run_id = assert_accepted(await admin.post(SYNC, headers=AUTH, json={"rover": rover}))

    await drain(admin_app)
    run = await get_run(admin, run_id)
    # Empty database: from = latest - overlap (docs/ARCHITECTURE.md, incremental range).
    assert (run["rover"], run["mode"], run["from_sol"], run["to_sol"]) == (
        "curiosity",
        "incremental",
        10 - OVERLAP,
        10,
    )
    assert (run["status"], run["photos_upserted"]) == ("success", 1)
    assert adapter.calls[0] == ("latest_sol",)


async def test_admin_sync_partial_run_reports_failed_sols_ascending(
    admin_app: FastAPI, admin: httpx.AsyncClient, adapters: Adapters
) -> None:
    # One chunk 0..5: sol 5 is stored, the iterator fails at sol 4 -> sols 0..4 failed.
    adapter = adapters.set(FakeAdapter("curiosity", fail_at={4: upstream_error()}))
    adapter.add(sol_records("curiosity", 5, 1))

    run_id = assert_accepted(
        await admin.post(
            SYNC, headers=AUTH, json={"rover": "curiosity", "from_sol": 0, "to_sol": 5}
        )
    )

    await drain(admin_app)
    run = await get_run(admin, run_id)
    assert run["status"] == "partial"
    assert run["failed_sols"] == [0, 1, 2, 3, 4]
    assert run["photos_upserted"] == 1
    assert isinstance(run["error"], str) and "0-4" in run["error"]


async def test_admin_sync_background_crash_marks_run_failed_logs_and_releases_lock(
    admin_app: FastAPI,
    admin: httpx.AsyncClient,
    adapters: Adapters,
    clean_db: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    adapters.set(FakeAdapter("curiosity", fail_at={1: RuntimeError("adapter bug")}))

    run_id = assert_accepted(
        await admin.post(
            SYNC, headers=AUTH, json={"rover": "curiosity", "from_sol": 0, "to_sol": 1}
        )
    )

    await drain(admin_app)
    run = await get_run(admin, run_id)
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    assert "adapter bug" in run["error"]
    assert any(
        "adapter bug" in record.getMessage() + str(record.exc_info and record.exc_info[1])
        for record in caplog.records
        if record.levelno >= logging.ERROR
    ), [record.getMessage() for record in caplog.records]
    assert await lock_is_free(clean_db, "curiosity")
    # The service and the endpoint keep working.
    adapters.set(FakeAdapter("curiosity"))
    assert_accepted(
        await admin.post(
            SYNC, headers=AUTH, json={"rover": "curiosity", "from_sol": 0, "to_sol": 1}
        )
    )
    await drain(admin_app)


async def test_admin_sync_lock_released_after_background_run_allows_next_sync(
    admin_app: FastAPI, admin: httpx.AsyncClient
) -> None:
    body = {"rover": "curiosity", "from_sol": 0, "to_sol": 1}
    first = assert_accepted(await admin.post(SYNC, headers=AUTH, json=body))
    await drain(admin_app)

    second = assert_accepted(await admin.post(SYNC, headers=AUTH, json=body))
    await drain(admin_app)

    assert second > first
    assert (await get_run(admin, second))["status"] == "success"


async def test_admin_shutdown_cancels_running_sync_marks_failed_and_releases_lock(
    settings: Settings, clean_db: Factory
) -> None:
    entered = asyncio.Event()

    async def hang(from_sol: int, to_sol: int) -> None:
        entered.set()
        await asyncio.Event().wait()

    adapters = Adapters()
    adapters.set(FakeAdapter("curiosity", latest=10, on_fetch=hang))
    app = create_app(admin_settings(settings))
    try:
        async with app.router.lifespan_context(app):
            app.state.adapter_factory = adapters
            async with make_client(app) as client:
                assert_accepted(await client.post(SYNC, headers=AUTH, json={"rover": "curiosity"}))
            await asyncio.wait_for(entered.wait(), timeout=WAIT)
            tasks = list(app.state.sync_tasks)
        # Leaving the lifespan must cancel and await the background sync.
        assert tasks and all(task.done() for task in tasks)
    finally:
        await app.state.engine.dispose()

    [run] = await sync_runs(clean_db)
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    # A cancelled incremental run fails as a whole: its stored range is retried next time.
    assert (run["mode"], run["from_sol"], run["to_sol"]) == ("incremental", 10 - OVERLAP, 10)
    assert run["failed_sols"] == list(range(10 - OVERLAP, 11))
    assert "CancelledError" in run["error"]
    assert await lock_is_free(clean_db, "curiosity")


# --- 409: the rover lock is held -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [{"rover": "curiosity"}, {"rover": "curiosity", "from_sol": 0, "to_sol": 5}],
    ids=["incremental", "backfill"],
)
async def test_admin_sync_lock_held_returns_409_without_run(
    admin_app: FastAPI,
    admin: httpx.AsyncClient,
    adapters: Adapters,
    clean_db: Factory,
    body: dict[str, Any],
) -> None:
    adapter = adapters.set(FakeAdapter("curiosity", latest=10))

    async with HeldLock(clean_db, "curiosity"):
        response = await admin.post(SYNC, headers=AUTH, json=body)
        await drain(admin_app)

    assert_error(response, 409, "sync_in_progress")
    assert_no_store(response)
    assert adapter.calls == []
    assert await sync_runs(clean_db) == []


async def test_admin_sync_lock_of_other_rover_does_not_block(
    admin_app: FastAPI, admin: httpx.AsyncClient, clean_db: Factory
) -> None:
    async with HeldLock(clean_db, "perseverance"):
        run_id = assert_accepted(
            await admin.post(
                SYNC, headers=AUTH, json={"rover": "curiosity", "from_sol": 0, "to_sol": 1}
            )
        )
        await drain(admin_app)

    assert (await get_run(admin, run_id))["status"] == "success"


async def test_admin_sync_second_request_while_first_runs_returns_409(
    admin_app: FastAPI, admin: httpx.AsyncClient, adapters: Adapters, clean_db: Factory
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block(from_sol: int, to_sol: int) -> None:
        entered.set()
        await release.wait()

    adapters.set(FakeAdapter("curiosity", on_fetch=block))
    body = {"rover": "curiosity", "from_sol": 0, "to_sol": 1}
    first = assert_accepted(await admin.post(SYNC, headers=AUTH, json=body))
    try:
        await asyncio.wait_for(entered.wait(), timeout=WAIT)
        second = await admin.post(SYNC, headers=AUTH, json={"rover": "CURIOSITY"})
        assert_error(second, 409, "sync_in_progress")
    finally:
        release.set()
        await drain(admin_app)

    assert [(run["id"], run["status"]) for run in await sync_runs(clean_db)] == [(first, "success")]


# --- 404 / 422 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rover",
    ["pluto", "cur1osity", "curiosity!", "../curiosity", "", "кьюриосити", "x" * 5000],
    ids=["unknown", "digit", "punct", "path", "empty", "cyrillic", "huge"],
)
async def test_admin_sync_unknown_or_malformed_rover_returns_404(
    admin: httpx.AsyncClient, adapters: Adapters, clean_db: Factory, rover: str
) -> None:
    response = await admin.post(SYNC, headers=AUTH, json={"rover": rover})

    assert_error(response, 404, "rover_not_found")
    assert_no_store(response)
    assert await sync_runs(clean_db) == []


@pytest.mark.parametrize("rover", ["spirit", "opportunity", "Spirit", "OPPORTUNITY"])
async def test_admin_sync_rover_without_source_returns_422_on_rover(
    admin: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    response = await admin.post(
        SYNC, headers=AUTH, json={"rover": rover, "from_sol": 0, "to_sol": 1}
    )

    assert_validation_error(response, "rover")
    assert_no_store(response)
    assert await sync_runs(clean_db) == []


async def test_admin_sync_rover_without_source_returns_422_with_default_factory(
    settings: Settings, clean_db: Factory
) -> None:
    """Without the test override the real registry decides (and no request leaves the box)."""
    app = create_app(admin_settings(settings))
    try:
        async with app.router.lifespan_context(app), make_client(app) as client:
            response = await client.post(SYNC, headers=AUTH, json={"rover": "spirit"})
    finally:
        await app.state.engine.dispose()

    assert_validation_error(response, "rover")
    assert await sync_runs(clean_db) == []


RANGE_FIELDS = ("from_sol", "to_sol", "request", "body")


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({"rover": "curiosity", "from_sol": 1}, RANGE_FIELDS),
        ({"rover": "curiosity", "to_sol": 1}, RANGE_FIELDS),
        ({"rover": "curiosity", "from_sol": 5, "to_sol": 1}, RANGE_FIELDS),
        ({"rover": "curiosity", "from_sol": -1, "to_sol": 1}, ("from_sol",)),
        ({"rover": "curiosity", "from_sol": 0, "to_sol": 2**31}, ("to_sol",)),
        ({"rover": "curiosity", "from_sol": 2**31, "to_sol": 2**31}, ("from_sol", "to_sol")),
        ({"rover": "curiosity", "from_sol": "abc", "to_sol": 1}, ("from_sol",)),
        ({"rover": "curiosity", "from_sol": 1.5, "to_sol": 2}, ("from_sol",)),
        ({"from_sol": 0, "to_sol": 1}, ("rover",)),
        ({"rover": None}, ("rover",)),
        ({"rover": 5}, ("rover",)),
    ],
    ids=[
        "from-only",
        "to-only",
        "from-gt-to",
        "negative",
        "to-above-int32",
        "both-above-int32",
        "from-not-int",
        "from-float",
        "no-rover",
        "rover-null",
        "rover-int",
    ],
)
async def test_admin_sync_invalid_body_returns_422(
    admin: httpx.AsyncClient,
    adapters: Adapters,
    clean_db: Factory,
    body: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    response = await admin.post(SYNC, headers=AUTH, json=body)

    assert_validation_error(response, *fields)
    assert_no_store(response)
    assert await sync_runs(clean_db) == []


async def test_admin_sync_bounds_inclusive_are_accepted(
    admin_app: FastAPI, admin: httpx.AsyncClient, adapters: Adapters
) -> None:
    top = 2**31 - 1
    adapters.set(FakeAdapter("curiosity"))

    run_id = assert_accepted(
        await admin.post(
            SYNC, headers=AUTH, json={"rover": "curiosity", "from_sol": top, "to_sol": top}
        )
    )
    await drain(admin_app)
    run = await get_run(admin, run_id)
    assert (run["from_sol"], run["to_sol"], run["status"]) == (top, top, "success")


@pytest.mark.parametrize(
    "content", [b"not json", b"[]", b""], ids=["invalid-json", "array", "empty"]
)
async def test_admin_sync_malformed_body_returns_422(
    admin: httpx.AsyncClient, clean_db: Factory, content: bytes
) -> None:
    response = await admin.post(
        SYNC, headers={**AUTH, "Content-Type": "application/json"}, content=content
    )

    assert_error(response, 422, "validation_error")
    assert await sync_runs(clean_db) == []


async def test_admin_sync_non_db_oserror_returns_500_not_503(
    admin: httpx.AsyncClient, adapters: Adapters
) -> None:
    """Stage 6, section 4: an OSError outside database use is a 500, not db_unavailable."""
    adapters.error = OSError("disk is full")

    response = await admin.post(SYNC, headers=AUTH, json={"rover": "curiosity"})

    assert_error(response, 500, "internal_error")


# --- GET /admin/sync/runs ------------------------------------------------------------------


async def test_admin_runs_empty_list(admin: httpx.AsyncClient) -> None:
    response = await admin.get(RUNS, headers=AUTH)

    assert response.status_code == 200, response.text
    assert_json(response.json(), {"items": [], "page": 1, "per_page": 25, "total": 0})
    assert_no_store(response)


async def test_admin_runs_exact_json_sorted_by_started_at_desc(
    admin: httpx.AsyncClient, clean_db: Factory
) -> None:
    older = await insert_run(
        clean_db,
        rover_id="perseverance",
        mode="incremental",
        from_sol=2024,
        to_sol=2031,
        status="partial",
        started_at=datetime(2026, 11, 17, 6, 0, 0, 999999, tzinfo=UTC),
        finished_at=datetime(2026, 11, 17, 6, 0, 12, 345678, tzinfo=UTC),
        photos_upserted=412,
        failed_sols=[2030, 2025, 2027],
        error="failed sols: 2025, 2027, 2030 ; last error: UpstreamError: boom",
    )
    newer = await insert_run(
        clean_db,
        rover_id="curiosity",
        mode="incremental",
        from_sol=None,
        to_sol=None,
        status="running",
        started_at=datetime(2026, 11, 18, 0, 0, 1, tzinfo=UTC),
    )

    response = await admin.get(RUNS, headers=AUTH)

    assert response.status_code == 200, response.text
    assert_json(
        response.json(),
        {
            "items": [
                {
                    "id": newer,
                    "rover": "curiosity",
                    "mode": "incremental",
                    "from_sol": None,
                    "to_sol": None,
                    "status": "running",
                    "started_at": "2026-11-18T00:00:01Z",
                    "finished_at": None,
                    "photos_upserted": 0,
                    "failed_sols": [],
                    "error": None,
                },
                {
                    "id": older,
                    "rover": "perseverance",
                    "mode": "incremental",
                    "from_sol": 2024,
                    "to_sol": 2031,
                    "status": "partial",
                    # Fractions are dropped, never rounded up.
                    "started_at": "2026-11-17T06:00:00Z",
                    "finished_at": "2026-11-17T06:00:12Z",
                    "photos_upserted": 412,
                    "failed_sols": [2025, 2027, 2030],
                    "error": "failed sols: 2025, 2027, 2030 ; last error: UpstreamError: boom",
                },
            ],
            "page": 1,
            "per_page": 25,
            "total": 2,
        },
    )


async def test_admin_runs_sort_is_by_started_at_not_id(
    admin: httpx.AsyncClient, clean_db: Factory
) -> None:
    late = await insert_run(clean_db, started_at=datetime(2026, 11, 10, tzinfo=UTC))
    early = await insert_run(clean_db, started_at=datetime(2026, 11, 1, tzinfo=UTC))
    middle = await insert_run(clean_db, started_at=datetime(2026, 11, 5, tzinfo=UTC))

    body = (await admin.get(RUNS, headers=AUTH)).json()

    assert [item["id"] for item in body["items"]] == [late, middle, early]


async def test_admin_runs_pagination_and_total(admin: httpx.AsyncClient, clean_db: Factory) -> None:
    ids = [
        await insert_run(clean_db, started_at=datetime(2026, 11, 1, hour, tzinfo=UTC))
        for hour in range(5)
    ]
    newest_first = list(reversed(ids))

    pages = [
        (await admin.get(RUNS, headers=AUTH, params={"page": page, "per_page": 2})).json()
        for page in (1, 2, 3, 4)
    ]

    assert [[item["id"] for item in body["items"]] for body in pages] == [
        newest_first[0:2],
        newest_first[2:4],
        newest_first[4:5],
        [],
    ]
    assert [(body["page"], body["per_page"], body["total"]) for body in pages] == [
        (1, 2, 5),
        (2, 2, 5),
        (3, 2, 5),
        (4, 2, 5),
    ]


@pytest.mark.parametrize("rover", ["perseverance", "Perseverance", "PERSEVERANCE"])
async def test_admin_runs_rover_filter_case_insensitive(
    admin: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    await insert_run(clean_db, rover_id="curiosity")
    wanted = await insert_run(clean_db, rover_id="perseverance")

    body = (await admin.get(RUNS, headers=AUTH, params={"rover": rover})).json()

    assert [item["id"] for item in body["items"]] == [wanted]
    assert body["items"][0]["rover"] == "perseverance"
    assert body["total"] == 1


async def test_admin_runs_rover_filter_known_rover_without_runs_is_empty(
    admin: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_run(clean_db, rover_id="curiosity")

    body = (await admin.get(RUNS, headers=AUTH, params={"rover": "spirit"})).json()

    assert (body["items"], body["total"]) == ([], 0)


@pytest.mark.parametrize(
    "rover", ["pluto", "cur1osity", "x" * 5000], ids=["unknown", "digit", "huge"]
)
async def test_admin_runs_unknown_rover_returns_404(admin: httpx.AsyncClient, rover: str) -> None:
    response = await admin.get(RUNS, headers=AUTH, params={"rover": rover})

    assert_error(response, 404, "rover_not_found")
    assert_no_store(response)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page": 0}, "page"),
        ({"page": 1_000_001}, "page"),
        ({"page": "x"}, "page"),
        ({"per_page": 0}, "per_page"),
        ({"per_page": 101}, "per_page"),
    ],
    ids=["page-zero", "page-too-big", "page-word", "per-page-zero", "per-page-101"],
)
async def test_admin_runs_invalid_pagination_returns_422(
    admin: httpx.AsyncClient, params: dict[str, Any], field: str
) -> None:
    response = await admin.get(RUNS, headers=AUTH, params=params)

    assert_validation_error(response, field)
    assert_no_store(response)


async def test_admin_runs_if_none_match_is_never_304(admin: httpx.AsyncClient) -> None:
    response = await admin.get(RUNS, headers={**AUTH, "If-None-Match": "*"})

    assert response.status_code == 200
    assert_no_store(response)


# --- HEAD ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "headers"),
    [(SYNC, AUTH), (RUNS, AUTH), (RUNS, {}), (RUNS, {**AUTH, "If-None-Match": "*"})],
    ids=["sync", "runs", "runs-no-token", "runs-if-none-match"],
)
async def test_admin_head_is_not_200_and_leaks_no_body(
    admin: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> None:
    """Whatever status backend picks for HEAD on admin routes, it is not a cacheable 200."""
    response = await admin.head(url, headers=headers)

    assert response.status_code != 200
    assert response.status_code != 304
    assert response.content == b""
    assert "etag" not in response.headers


# --- ADMIN_TOKEN validation ----------------------------------------------------------------


def settings_with_token(settings: Settings, token: str | None) -> Settings:
    """Build Settings through validation (model_copy would skip the validators)."""
    values: dict[str, Any] = {
        "database_url": settings.database_url,
        "test_database_url": settings.test_database_url,
        "environment": "test",
        "sync_enabled": False,
        "admin_token": token,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "token",
    [" ", " " * 20, "\t\n  \t", "Zq7short", "Zq7" + "x" * 12],
    ids=["space", "spaces-20", "tabs-newlines", "short", "fifteen"],
)
async def test_admin_token_whitespace_or_short_fails_startup_clearly(
    settings: Settings, token: str
) -> None:
    with pytest.raises((ValueError, RuntimeError)) as exc_info:
        app = create_app(settings_with_token(settings, token))
        try:
            async with app.router.lifespan_context(app):
                pass
        finally:
            await app.state.engine.dispose()

    assert "admin_token" in str(exc_info.value).lower(), str(exc_info.value)
    if token.strip():
        assert token not in str(exc_info.value), "the token must not be echoed in the error"


@pytest.mark.parametrize("token", [None, ""], ids=["unset", "empty"])
async def test_admin_token_empty_or_unset_disables_admin(
    settings: Settings, token: str | None
) -> None:
    app = create_app(settings_with_token(settings, token))
    try:
        async with app.router.lifespan_context(app), make_client(app) as client:
            response = await client.get(RUNS, headers={"X-Admin-Token": "anything"})
    finally:
        await app.state.engine.dispose()

    assert_error(response, 404, "not_found")


async def test_admin_token_of_16_chars_enables_admin(settings: Settings) -> None:
    token = "a" * 16
    app = create_app(settings_with_token(settings, token))
    try:
        async with app.router.lifespan_context(app), make_client(app) as client:
            response = await client.get(RUNS, headers={"X-Admin-Token": token})
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 200, response.text


# --- OpenAPI -------------------------------------------------------------------------------


def _documents_admin_header(schema: dict[str, Any], operation: dict[str, Any]) -> bool:
    for parameter in operation.get("parameters", []):
        if parameter.get("in") == "header" and parameter.get("name", "").lower() == "x-admin-token":
            return True
    schemes = schema.get("components", {}).get("securitySchemes", {})
    for requirement in operation.get("security", []):
        for name in requirement:
            scheme = schemes.get(name, {})
            if scheme.get("in") == "header" and scheme.get("name", "").lower() == "x-admin-token":
                return True
    return False


@pytest.mark.parametrize(
    ("path", "method", "codes"),
    [
        ("/api/v2/admin/sync", "post", {"202", "401", "404", "409", "422"}),
        ("/api/v2/admin/sync/runs", "get", {"200", "401", "422"}),
    ],
    ids=["sync", "runs"],
)
async def test_admin_routes_documented_in_openapi(
    admin: httpx.AsyncClient, path: str, method: str, codes: set[str]
) -> None:
    schema = (await admin.get("/openapi.json")).json()

    operation = schema["paths"][path][method]
    assert codes <= set(operation["responses"]), operation["responses"].keys()
    assert _documents_admin_header(schema, operation), operation
    for code in codes - {"200", "202"}:
        content = operation["responses"][code].get("content", {})
        ref = content.get("application/json", {}).get("schema", {}).get("$ref", "")
        assert ref.endswith("/ErrorResponse"), (code, operation["responses"][code])
