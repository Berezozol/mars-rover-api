"""Heavy-query gate over HTTP (docs/proposals/overload-protection.md; docs/API.md error
`service_busy` and the "Перегрузка" bullet). AC-3 ... AC-8 and AC-11.

Setup: an app with a small pool (4, no overflow), a pool wait of 1 s, `HEAVY_QUERY_CONCURRENCY=1`
and the per-IP limit off. `photos_repo.count_photos` is replaced by `SELECT pg_sleep(N)` for
filters that are heavy by their shape (so a "heavy" request holds its slot and a pooled
connection for N seconds); everything else answers normally. Requests run concurrently on one
ASGI `httpx.AsyncClient`. A slot is held by a background task that the test cancels at the end
(this also exercises the client-disconnect path).

Margins are generous: a slot holder sleeps 30 s in the database (the test cancels it), other
heavy requests 2 s, light requests may take up to 1 s, a refusal is timed as the best of three.

Per-client limit (docs/API.md "Перегрузка"): one heavy search at a time per client address
(IPv6 by /48, HIGH-2 2026-11-26). The default test client is 10.0.0.1; tests that need a
second heavy request running at the same time use another address (`make_client(app, ip=...)`).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import Settings
from app.core.errors import ServiceBusy
from app.main import create_app
from app.repositories import photos as photos_repo
from app.repositories.photos import PhotoFilter, recompute_sol_summaries, upsert_photos
from tests.conftest import build_record

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

HOLD_SECONDS = 2.0
HOLDER_SECONDS = 30.0  # the slot holder: cancelled by the test, never waited for
LIGHT_SLA_SECONDS = 1.0
REFUSAL_SLA_SECONDS = 0.2
DEFAULT_IP = "10.0.0.1"

V2 = "/api/v2"
# Not pinned by sol/day, with a date filter: scan_heavy in every version of the class table.
HEAVY_URL = f"{V2}/rovers/curiosity/photos?date_min=2012-08-06&sample_type=full"
# Same shape, but the stub holds the connection for HOLDER_SECONDS (recognised by the date).
HOLDER_DATE = date(2012, 8, 7)
HOLDER_URL = f"{V2}/rovers/curiosity/photos?date_min={HOLDER_DATE.isoformat()}&sample_type=full"
MULTI_URL = (
    f"{V2}/photos?rovers=curiosity,perseverance&sample_type=full&min_width=1"
    "&sort=-taken_at&page=100&per_page=100"
)
BUSY_BODY = {
    "error": {"code": "service_busy", "message": "Server is busy, retry later", "details": None}
}


def _shape_is_heavy(flt: PhotoFilter) -> bool:
    """Independent of `classify_total`: what makes a count slow in the spec (section 6)."""
    if flt.sol is not None or flt.earth_date is not None:
        return False
    stops = flt.site is not None or flt.site_min is not None or flt.site_max is not None
    on_stops = stops or flt.near is not None
    scan_filters = (
        flt.date_min is not None
        or flt.date_max is not None
        or flt.min_width is not None
        or flt.min_height is not None
        or bool(flt.local_time_ranges)
        # sample_type is in the stops index: it only makes a plain search heavy
        or (not on_stops and flt.sample_type not in (None, "full"))
    )
    stops_with_camera = on_stops and flt.camera_codes is not None
    return scan_filters or stops_with_camera


class CountStub:
    """Stand-in for `count_photos`: sleeps in the database when the filter is heavy."""

    def __init__(self) -> None:
        self.hold = HOLD_SECONDS
        self.calls = 0
        self.error: BaseException | None = None

    async def __call__(self, session: Any, flt: PhotoFilter) -> int:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if _shape_is_heavy(flt):
            seconds = HOLDER_SECONDS if flt.date_min == HOLDER_DATE else self.hold
            await session.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": seconds})
        return 0


class Harness:
    def __init__(self, settings: Settings, stub: CountStub) -> None:
        self._settings = settings
        self.stub = stub
        self.apps: list[FastAPI] = []

    def build(self, **overrides: Any) -> FastAPI:
        update: dict[str, Any] = {
            "db_pool_size": 4,
            "db_max_overflow": 0,
            "heavy_query_concurrency": 1,
            "db_pool_timeout_seconds": 1.0,
            "rate_limit_enabled": False,
        }
        update.update(overrides)
        app = create_app(self._settings.model_copy(update=update))
        self.apps.append(app)
        return app


@pytest.fixture
async def harness(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
    stub = CountStub()
    monkeypatch.setattr(photos_repo, "count_photos", stub)
    built = Harness(settings, stub)
    try:
        yield built
    finally:
        for app in built.apps:
            await app.state.engine.dispose()


@pytest.fixture
async def gate_app(harness: Harness) -> FastAPI:
    return harness.build()


def make_client(app: FastAPI, ip: str = DEFAULT_IP) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def client(gate_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(gate_app) as http_client:
        yield http_client


def in_use(app: FastAPI) -> int:
    return app.state.heavy_query_gate.in_use


async def wait_for(condition: Callable[[], bool], what: str, limit: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for: {what}")
        await asyncio.sleep(0.01)


async def cancel_all(*tasks: "asyncio.Future[Any]") -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def held_slot(app: FastAPI, client: httpx.AsyncClient) -> AsyncIterator[None]:
    """Keep one heavy request in flight (it holds a slot for 30 s); cancel it on exit."""
    stub = photos_repo.count_photos
    assert isinstance(stub, CountStub)
    calls_before = stub.calls
    task = asyncio.ensure_future(client.get(HOLDER_URL))
    try:
        await wait_for(lambda: in_use(app) == 1, "the first heavy request to take the slot")
        # The slot is taken a few awaits before the holder reaches the count: wait for the stub
        # call to be registered, so that later readings of `stub.calls` are stable.
        await wait_for(lambda: stub.calls > calls_before, "the holder to reach the count")
        yield
    finally:
        await cancel_all(task)
        await wait_for(lambda: in_use(app) == 0, "the slot to be released")


def assert_busy(response: httpx.Response) -> None:
    assert response.status_code == 503, response.text
    assert response.json() == BUSY_BODY
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"


# --- AC-3 ----------------------------------------------


async def test_heavy_flood_leaves_light_requests_fast_and_allows_exactly_one_heavy(
    gate_app: FastAPI, client: httpx.AsyncClient
) -> None:
    """AC-3: 10 parallel heavy searches, 6 light requests meanwhile."""
    heavy = [asyncio.ensure_future(client.get(HEAVY_URL)) for _ in range(10)]
    try:
        await wait_for(lambda: in_use(gate_app) == 1, "a heavy request to take the slot")

        async def timed(url: str) -> tuple[httpx.Response, float]:
            started = time.perf_counter()
            response = await client.get(url)
            return response, time.perf_counter() - started

        light_urls = [
            f"{V2}/rovers",
            f"{V2}/rovers/curiosity/photos?sol=1000",
            "/api/v1/rovers/curiosity/photos?sol=1000",
        ] * 2
        lights = await asyncio.gather(*(timed(url) for url in light_urls))
        heavy_responses = await asyncio.gather(*heavy)
    finally:
        await cancel_all(*heavy)

    for (response, elapsed), url in zip(lights, light_urls, strict=True):
        assert response.status_code == 200, (url, response.text)
        assert elapsed < LIGHT_SLA_SECONDS, (url, elapsed)
    statuses = sorted(response.status_code for response in heavy_responses)
    assert statuses == [200] + [503] * 9, statuses
    for response in heavy_responses:
        if response.status_code == 503:
            assert_busy(response)
    assert in_use(gate_app) == 0


async def test_two_heavy_requests_started_together_give_exactly_one_200(
    gate_app: FastAPI, client: httpx.AsyncClient
) -> None:
    """AC-3 edge: HEAVY_QUERY_CONCURRENCY=1, two heavy requests at once."""
    responses = await asyncio.gather(client.get(HEAVY_URL), client.get(HEAVY_URL))

    assert sorted(r.status_code for r in responses) == [200, 503]
    assert_busy(next(r for r in responses if r.status_code == 503))
    assert in_use(gate_app) == 0


async def test_gate_limit_follows_the_setting(harness: Harness) -> None:
    """AC-3/AC-9: with HEAVY_QUERY_CONCURRENCY=2 two heavy requests run, the third is refused."""
    app = harness.build(heavy_query_concurrency=2)
    async with (
        make_client(app, "10.0.0.1") as one,
        make_client(app, "10.0.0.2") as two,
        make_client(app, "10.0.0.3") as other,
    ):
        first = asyncio.ensure_future(one.get(HOLDER_URL))
        second = asyncio.ensure_future(two.get(HOLDER_URL))
        try:
            await wait_for(lambda: in_use(app) == 2, "two heavy requests to take both slots")
            third = await other.get(HEAVY_URL)
        finally:
            await cancel_all(first, second)

    assert_busy(third)


# --- AC-4 ----------------------------------------------


async def test_refused_heavy_request_is_an_instant_503_without_database_work(
    gate_app: FastAPI,
    client: httpx.AsyncClient,
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4: body, headers, < 200 ms (the pool wait is 1 s), no count and no key query."""
    assert (await client.get(f"{V2}/rovers")).status_code == 200  # warm the pool
    list_calls: list[Any] = []
    real_list = photos_repo.list_photos_multi

    async def spy_list(*args: Any, **kwargs: Any) -> Any:
        list_calls.append(args)
        return await real_list(*args, **kwargs)

    monkeypatch.setattr(photos_repo, "list_photos_multi", spy_list)
    async with held_slot(gate_app, client):
        calls_before = harness.stub.calls
        attempts: list[tuple[httpx.Response, float]] = []
        for _ in range(3):
            started = time.perf_counter()
            response = await client.get(HEAVY_URL)
            attempts.append((response, time.perf_counter() - started))
        calls_after = harness.stub.calls

    for response, _ in attempts:
        assert_busy(response)
        assert "etag" not in response.headers
        assert list(response.json()["error"]) == ["code", "message", "details"]
    assert min(elapsed for _, elapsed in attempts) < REFUSAL_SLA_SECONDS, [
        elapsed for _, elapsed in attempts
    ]
    assert calls_after == calls_before  # no count reached the repository
    assert list_calls == []  # no page-key query either


async def test_refused_heavy_request_is_logged_as_a_warning(
    gate_app: FastAPI, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Spec section 7: `Heavy query rejected path=... in_use=...` at WARNING."""
    async with held_slot(gate_app, client):
        with caplog.at_level("WARNING"):
            response = await client.get(HEAVY_URL)

    assert response.status_code == 503
    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(m.startswith("Heavy query rejected") and "in_use=1" in m for m in messages), messages


# --- class boundary over HTTP (decision 2026-11-21) ----------------------------------------------

ROVER_PHOTOS = f"{V2}/rovers/curiosity/photos"
REFUSED_WHILE_BUSY = [
    "date_max=2020-01-01",
    "min_width=100",
    "min_height=100",
    "local_time_min=05:00&local_time_max=07:00",
    "golden_hour=true",
    "sample_type=subframe",
    "sample_type=downsampled&sol_min=1&sol_max=100",
    "site_min=0&site_max=100000&camera=FHAZ",
    "site_min=0&site_max=100000&min_width=1",
    "site_min=0&site_max=100000&date_min=2012-08-06",
    "near=10,20&radius_km=5&camera=FHAZ",
]
SERVED_WHILE_BUSY = [
    "",
    "sample_type=full",
    "camera=FHAZ",
    "sol_min=1&sol_max=100",
    "sol_min=1&sol_max=100&camera=FHAZ",
    "sol=1000&sample_type=subframe",
    "sol=1000&min_width=1",
    "earth_date=2020-01-01&sample_type=subframe",
    "site_min=0&site_max=100000",
    "site_min=0&site_max=100000&sample_type=full",
    "near=10,20&radius_km=5",
    "near=10,20&radius_km=5&sample_type=full",
    "site_min=0&site_max=100000&sample_type=subframe",
    "near=10,20&radius_km=5&sample_type=subframe",
]


@pytest.mark.parametrize("query", REFUSED_WHILE_BUSY, ids=REFUSED_WHILE_BUSY)
async def test_heavy_filter_shape_is_refused_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, query: str
) -> None:
    """AC-1/AC-3 over HTTP: every `scan_heavy` shape is refused when the slot is taken."""
    async with held_slot(gate_app, client):
        response = await client.get(f"{ROVER_PHOTOS}?{query}")

    assert_busy(response)


@pytest.mark.parametrize(
    "query", SERVED_WHILE_BUSY, ids=[q or "no-filters" for q in SERVED_WHILE_BUSY]
)
async def test_summary_and_scan_light_shapes_pass_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, query: str
) -> None:
    """AC-1/AC-3 over HTTP: `summary` / `scan_light` / pinned searches do not use the gate."""
    async with held_slot(gate_app, client):
        response = await client.get(f"{ROVER_PHOTOS}?{query}")
        assert in_use(gate_app) == 1  # still only the holder

    assert response.status_code == 200, response.text


# --- taken_at sort without a sol / day (decision 2026-11-21, a) ---------------------------

TAKEN_AT_REFUSED = [
    pytest.param(f"{ROVER_PHOTOS}?camera=FHAZ&sort=taken_at", id="rover-camera-asc"),
    pytest.param(f"{ROVER_PHOTOS}?camera=FHAZ&sort=-taken_at", id="rover-camera-desc"),
    pytest.param(f"{ROVER_PHOTOS}?sample_type=full&sort=-taken_at", id="rover-full-desc"),
    pytest.param(f"{ROVER_PHOTOS}?sample_type=full&sort=taken_at", id="rover-full-asc"),
    pytest.param(f"{ROVER_PHOTOS}?sample_type=subframe&sort=-taken_at", id="rover-subframe-desc"),
    pytest.param(
        f"{ROVER_PHOTOS}?camera=FHAZ&sort=taken_at&page=3&per_page=10", id="rover-camera-page-3"
    ),
    pytest.param(
        f"{ROVER_PHOTOS}?camera=FHAZ&sol_min=1&sol_max=100&sort=-taken_at",
        id="rover-sol-range-is-not-a-pin",
    ),
    pytest.param(f"{V2}/photos?rovers=curiosity&camera=FHAZ&sort=taken_at", id="multi-camera-asc"),
    pytest.param(
        f"{V2}/photos?rovers=curiosity&sample_type=full&sort=-taken_at", id="multi-full-desc"
    ),
]
TAKEN_AT_SERVED = [
    pytest.param(f"{ROVER_PHOTOS}?camera=FHAZ&sol=1000&sort=taken_at", id="rover-camera-sol"),
    pytest.param(
        f"{ROVER_PHOTOS}?sample_type=full&sol=1000&sort=-taken_at", id="rover-full-sol-desc"
    ),
    pytest.param(
        f"{ROVER_PHOTOS}?camera=FHAZ&earth_date=2020-01-01&sort=taken_at", id="rover-camera-day"
    ),
    pytest.param(
        f"{ROVER_PHOTOS}?sample_type=full&earth_date=2020-01-01&sort=-taken_at",
        id="rover-full-day-desc",
    ),
    pytest.param(f"{ROVER_PHOTOS}?camera=FHAZ", id="rover-camera-default-sort"),
    pytest.param(f"{ROVER_PHOTOS}?camera=FHAZ&sort=-sol", id="rover-camera-sort-sol-desc"),
    pytest.param(f"{ROVER_PHOTOS}?sample_type=full&sort=sol", id="rover-full-sort-sol"),
    pytest.param(f"{ROVER_PHOTOS}?sort=-taken_at", id="rover-taken-at-alone"),
    pytest.param(
        f"{V2}/photos?rovers=curiosity&camera=FHAZ&sol=1000&sort=-taken_at", id="multi-camera-sol"
    ),
    pytest.param(
        f"{V2}/photos?rovers=curiosity&sample_type=full&earth_date=2020-01-01&sort=taken_at",
        id="multi-full-day",
    ),
    pytest.param(f"{V2}/photos?rovers=curiosity&camera=FHAZ", id="multi-camera-default-sort"),
]


@pytest.mark.parametrize("url", TAKEN_AT_REFUSED)
async def test_taken_at_sort_with_camera_or_sample_type_is_refused_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, url: str
) -> None:
    """Decision 2026-11-21: no sol/earth_date + camera|sample_type + sort=(-)taken_at is heavy."""
    async with held_slot(gate_app, client):
        response = await client.get(url)
        assert in_use(gate_app) == 1

    assert_busy(response)


@pytest.mark.parametrize("url", TAKEN_AT_SERVED)
async def test_taken_at_sort_pinned_or_other_shapes_are_served_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, url: str
) -> None:
    """The taken_at rule needs no sol/earth_date, a camera or sample_type, and a taken_at sort."""
    async with held_slot(gate_app, client):
        response = await client.get(url)
        assert in_use(gate_app) == 1

    assert response.status_code == 200, response.text


# --- AC-5 ----------------------------------------------


async def test_multi_rover_heavy_search_is_refused_while_the_slot_is_taken(
    gate_app: FastAPI, client: httpx.AsyncClient
) -> None:
    """AC-5: the cross-rover list is gated too."""
    async with held_slot(gate_app, client):
        response = await client.get(MULTI_URL)

    assert_busy(response)


async def test_multi_rover_heavy_search_takes_one_slot_not_one_per_rover(
    gate_app: FastAPI, client: httpx.AsyncClient, harness: Harness
) -> None:
    """AC-5: with a free gate (limit 1) two rovers are served; the peak is a single slot."""
    harness.stub.hold = 0.3
    task = asyncio.ensure_future(client.get(MULTI_URL))
    peak = 0
    while not task.done():
        peak = max(peak, in_use(gate_app))
        await asyncio.sleep(0.01)
    response = await task

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    assert harness.stub.calls == 2  # one count per rover ...
    assert peak == 1  # ... under one slot
    assert in_use(gate_app) == 0


# --- AC-6 ----------------------------------------------


@pytest.mark.parametrize("outcome", ["ok", "statement-timeout", "unexpected-error", "disconnect"])
async def test_slot_is_freed_after_every_kind_of_outcome(harness: Harness, outcome: str) -> None:
    """AC-6: 200, 503 by statement_timeout, 500, client disconnect -> the next heavy gets a slot.

    (404 and 422 never take a slot: AC-7.)
    """
    if outcome == "statement-timeout":
        app = harness.build(db_statement_timeout_seconds=0.3)
    else:
        app = harness.build()
    stub = harness.stub
    async with make_client(app) as client:
        if outcome == "ok":
            stub.hold = 0.1
            first = await client.get(HEAVY_URL)
            assert first.status_code == 200
        elif outcome == "statement-timeout":
            first = await client.get(HEAVY_URL)  # pg_sleep(2) > 0.3 s: the database cancels it
            assert_busy(first)
        elif outcome == "unexpected-error":
            stub.error = RuntimeError("boom")
            first = await client.get(HEAVY_URL)
            assert first.status_code == 500
            stub.error = None
        else:
            task = asyncio.ensure_future(client.get(HEAVY_URL))
            await wait_for(lambda: in_use(app) == 1, "the request to take the slot")
            await cancel_all(task)
            await wait_for(lambda: in_use(app) == 0, "the slot to be freed after the disconnect")

        await wait_for(lambda: in_use(app) == 0, "the slot to be freed")
        stub.hold = 0.0
        stub.error = None
        follow_up = await client.get(HEAVY_URL)

    assert follow_up.status_code == 200, follow_up.text
    assert in_use(app) == 0


async def test_disconnected_heavy_request_does_not_leave_the_pool_exhausted(
    gate_app: FastAPI, client: httpx.AsyncClient, harness: Harness
) -> None:
    """AC-6: after several cancelled heavy requests light requests still get connections."""
    for _ in range(3):
        task = asyncio.ensure_future(client.get(HEAVY_URL))
        await wait_for(lambda: in_use(gate_app) == 1, "the request to take the slot")
        await cancel_all(task)
        await wait_for(lambda: in_use(gate_app) == 0, "the slot to be freed")

    responses = await asyncio.gather(*(client.get(f"{V2}/rovers") for _ in range(8)))

    assert [r.status_code for r in responses] == [200] * 8


# --- AC-7 ----------------------------------------------

REJECTED_BEFORE_THE_GATE = [
    pytest.param(
        f"{V2}/rovers/curiosity/photos?min_width=1&page=0", 422, "validation_error", id="page-zero"
    ),
    pytest.param(
        f"{V2}/rovers/curiosity/photos?sample_type=subframe&sol_min=5&sol_max=1",
        422,
        "validation_error",
        id="sol-min-above-sol-max",
    ),
    pytest.param(
        f"{V2}/photos?rovers=curiosity,perseverance&site_min=1&min_width=1",
        422,
        "validation_error",
        id="site-needs-one-rover",
    ),
    pytest.param(
        f"{V2}/photos?min_width=1&page=101&per_page=100",
        422,
        "validation_error",
        id="window-over-10000",
    ),
]


@pytest.mark.parametrize(("url", "status", "code"), REJECTED_BEFORE_THE_GATE)
async def test_rejected_request_gets_its_own_error_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, url: str, status: int, code: str
) -> None:
    """AC-7: 422 / 404 are decided before the gate: not `503` even when no slot is free."""
    async with held_slot(gate_app, client):
        response = await client.get(url)
        assert in_use(gate_app) == 1

    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(("url", "status", "code"), REJECTED_BEFORE_THE_GATE)
async def test_rejected_request_takes_no_slot_and_no_count(
    gate_app: FastAPI,
    client: httpx.AsyncClient,
    harness: Harness,
    url: str,
    status: int,
    code: str,
) -> None:
    """AC-7: with a free gate the rejected request never occupies it and never counts."""
    peak = 0
    task = asyncio.ensure_future(client.get(url))
    while not task.done():
        peak = max(peak, in_use(gate_app))
        await asyncio.sleep(0)
    response = await task

    assert response.status_code == status
    assert peak == 0
    assert harness.stub.calls == 0


UNKNOWN_ROVER_HEAVY = f"{V2}/rovers/nosuch/photos?date_min=2012-08-06&sample_type=full"


async def test_unknown_rover_with_heavy_shape_is_404_when_the_gate_is_free(
    gate_app: FastAPI, client: httpx.AsyncClient, harness: Harness
) -> None:
    """AC-7: the gate decides before the rover lookup, but a free gate lets the 404 through
    and nothing is counted."""
    response = await client.get(UNKNOWN_ROVER_HEAVY)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "rover_not_found"
    assert harness.stub.calls == 0
    assert in_use(gate_app) == 0


async def test_unknown_rover_with_heavy_shape_is_404_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, harness: Harness
) -> None:
    """AC-7 (decision 2026-11-21): the slug is validated before the gate, without the database:
    an unknown rover is always 404, a full gate does not turn it into 503, and it takes no slot."""
    async with held_slot(gate_app, client):
        calls_before = harness.stub.calls
        response = await client.get(UNKNOWN_ROVER_HEAVY)
        assert in_use(gate_app) == 1  # only the holder
        assert harness.stub.calls == calls_before

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "rover_not_found"


@pytest.mark.parametrize(
    "slug",
    ["x%0Aforged%20level=INFO", "cur%20iosity", "a" * 200],
    ids=["newline-injection", "space", "very-long"],
)
async def test_malformed_rover_slug_is_404_before_the_gate(
    gate_app: FastAPI, client: httpx.AsyncClient, slug: str
) -> None:
    """Decision 2026-11-21: the slug regex + known rovers are checked before the gate."""
    async with held_slot(gate_app, client):
        response = await client.get(
            f"{V2}/rovers/{slug}/photos?date_min=2012-08-06&sample_type=full"
        )
        assert in_use(gate_app) == 1

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "rover_not_found"


async def test_busy_log_line_carries_the_route_template_not_the_raw_path(
    gate_app: FastAPI, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Decision 2026-11-21 (d): the refusal log has `/rovers/{rover}/photos`, so user
    input in the path cannot forge log lines."""
    async with held_slot(gate_app, client):
        with caplog.at_level("WARNING"):
            response = await client.get(HEAVY_URL)

    assert response.status_code == 503
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Heavy query")]
    assert len(messages) == 1, messages
    # The router-relative template (the prefix is not part of it): a placeholder, not user input.
    assert "path=/rovers/{rover}/photos " in messages[0], messages
    assert "curiosity" not in messages[0]


async def test_forged_slug_never_reaches_the_logs(
    gate_app: FastAPI, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A slug like `x%0Aforged level=INFO` is a 404 and appears in no log record."""
    with caplog.at_level("DEBUG"):
        response = await client.get(
            f"{V2}/rovers/x%0Aforged%20level=INFO/photos?date_min=2012-08-06&sample_type=full"
        )

    assert response.status_code == 404, response.text
    assert all("forged" not in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


# --- deep pages are heavy whatever the filters (decision 2026-11-21) -----------------------------

DEEP_REFUSED = [
    pytest.param(f"{ROVER_PHOTOS}?per_page=100&page=101", id="rover-page-101x100"),
    pytest.param(f"{ROVER_PHOTOS}?per_page=50&page=201", id="rover-page-201x50"),
    pytest.param(f"{ROVER_PHOTOS}?per_page=100&page=5000", id="rover-far-page"),
    pytest.param(f"{ROVER_PHOTOS}?sol_min=1&sol_max=100&per_page=25&page=401", id="rover-sols"),
]
DEEP_SERVED = [
    pytest.param(f"{ROVER_PHOTOS}?per_page=100&page=100", id="rover-9900"),
    pytest.param(f"{ROVER_PHOTOS}?per_page=50&page=200", id="rover-9950"),
    pytest.param(f"{V2}/photos?rovers=curiosity&per_page=100&page=100", id="multi-9900"),
]


@pytest.mark.parametrize("url", DEEP_REFUSED)
async def test_deep_page_with_light_filters_is_refused_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, url: str
) -> None:
    """(page - 1) * per_page >= 10 000 is heavy regardless of filters."""
    async with held_slot(gate_app, client):
        response = await client.get(url)

    assert_busy(response)


@pytest.mark.parametrize("url", DEEP_REFUSED)
async def test_deep_page_with_a_free_gate_is_served_through_one_slot(
    gate_app: FastAPI, client: httpx.AsyncClient, url: str
) -> None:
    """No depth cap: a deep page goes through the gate and is answered (an empty page)."""
    task = asyncio.ensure_future(client.get(url))
    peak = 0
    while not task.done():
        peak = max(peak, in_use(gate_app))
        await asyncio.sleep(0)
    response = await task

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert peak == 1
    assert in_use(gate_app) == 0


@pytest.mark.parametrize("url", DEEP_SERVED)
async def test_page_just_below_the_boundary_is_not_gated(
    gate_app: FastAPI, client: httpx.AsyncClient, url: str
) -> None:
    """(page - 1) * per_page = 9 900 / 9 950 stays light while the gate is full."""
    async with held_slot(gate_app, client):
        response = await client.get(url)
        assert in_use(gate_app) == 1

    assert response.status_code == 200, response.text


async def test_multi_rover_page_at_the_boundary_is_a_validation_error_not_busy(
    gate_app: FastAPI, client: httpx.AsyncClient
) -> None:
    """`/photos?page=101&per_page=100` is past the 10 000 window: 422 comes before the gate."""
    async with held_slot(gate_app, client):
        response = await client.get(f"{V2}/photos?page=101&per_page=100")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"


# --- per-client limit ----------------------------------------------------------------------------

SAME_CLIENT = [
    pytest.param("10.0.0.1", "10.0.0.1", id="ipv4-same-address"),
    pytest.param("2001:db8:1:2::1", "2001:db8:1:2::2", id="ipv6-same-64"),
    pytest.param("2001:db8:1:2::1", "2001:db8:1:2:ffff:ffff:ffff:ffff", id="ipv6-64-edges"),
    pytest.param("2001:db8:1:2::1", "2001:db8:1:3::1", id="ipv6-two-64-networks-of-one-48"),
    pytest.param("2001:db8:1:2::1", "2001:db8:1:ffff::1", id="ipv6-48-edges"),
]
OTHER_CLIENT = [
    pytest.param("10.0.0.1", "10.0.0.2", id="ipv4-two-addresses"),
    pytest.param("2001:db8:1:2::1", "2001:db8:2:2::1", id="ipv6-two-48-networks"),
]


@pytest.mark.parametrize(("first_ip", "second_ip"), SAME_CLIENT)
async def test_second_heavy_search_from_the_same_client_is_refused_below_the_server_limit(
    harness: Harness, first_ip: str, second_ip: str
) -> None:
    """503 although HEAVY_QUERY_CONCURRENCY=2 leaves a free slot; once the first one is gone
    the client can run a heavy search again."""
    app = harness.build(heavy_query_concurrency=2)
    async with make_client(app, first_ip) as one, make_client(app, second_ip) as two:
        holder = asyncio.ensure_future(one.get(HOLDER_URL))
        try:
            await wait_for(lambda: in_use(app) == 1, "the first heavy request to take a slot")
            refused = await two.get(HEAVY_URL)
            assert in_use(app) == 1  # the free server slot was not taken
        finally:
            await cancel_all(holder)
        await wait_for(lambda: in_use(app) == 0, "the slot to be released")
        harness.stub.hold = 0.0
        again = await two.get(HEAVY_URL)

    assert_busy(refused)
    assert again.status_code == 200, again.text


@pytest.mark.parametrize(("first_ip", "second_ip"), SAME_CLIENT)
async def test_two_parallel_heavy_searches_from_one_client_give_one_200_and_one_503(
    harness: Harness, first_ip: str, second_ip: str
) -> None:
    app = harness.build(heavy_query_concurrency=2)
    harness.stub.hold = 0.5
    async with make_client(app, first_ip) as one, make_client(app, second_ip) as two:
        responses = await asyncio.gather(one.get(HEAVY_URL), two.get(HEAVY_URL))

    assert sorted(r.status_code for r in responses) == [200, 503]
    assert_busy(next(r for r in responses if r.status_code == 503))
    assert in_use(app) == 0


@pytest.mark.parametrize(("first_ip", "second_ip"), OTHER_CLIENT)
async def test_heavy_searches_from_two_clients_both_run(
    harness: Harness, first_ip: str, second_ip: str
) -> None:
    app = harness.build(heavy_query_concurrency=2)
    harness.stub.hold = 0.5
    async with make_client(app, first_ip) as one, make_client(app, second_ip) as two:
        responses = await asyncio.gather(one.get(HEAVY_URL), two.get(HEAVY_URL))

    assert [r.status_code for r in responses] == [200, 200]
    assert in_use(app) == 0


async def test_light_request_of_a_client_with_a_running_heavy_search_is_served(
    harness: Harness,
) -> None:
    """The per-client limit counts heavy searches only."""
    app = harness.build(heavy_query_concurrency=2)
    async with make_client(app) as client, held_slot(app, client):
        light = await client.get(f"{ROVER_PHOTOS}?sol=1000")

    assert light.status_code == 200, light.text


# --- ServiceBusy on a v1 path --------------------------------------------------------------------


async def test_service_busy_on_a_v1_path_uses_the_legacy_body(gate_app: FastAPI) -> None:
    """v1 body `{"errors": "Server is busy"}`. v1 never takes a slot itself, so a route that
    raises the error stands in (like the hold route of test_rate_limit_ipv6.py)."""

    async def busy() -> None:
        raise ServiceBusy(1)

    gate_app.add_api_route("/api/v1/__busy", busy, methods=["GET"])
    async with make_client(gate_app) as client:
        response = await client.get("/api/v1/__busy")

    assert response.status_code == 503
    assert response.json() == {"errors": "Server is busy"}
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"


# --- AC-8 ----------------------------------------------

V1_URLS = [
    "/rovers",
    "/rovers/curiosity",
    "/rovers/curiosity/photos?sol=1000",
    "/rovers/curiosity/photos?earth_date=2020-01-01&camera=FHAZ",
]


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
@pytest.mark.parametrize("path", V1_URLS, ids=["rovers", "rover", "sol", "earth-date-camera"])
async def test_v1_is_served_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, prefix: str, path: str
) -> None:
    """AC-8: v1 does not pass the gate and answers as before."""
    async with held_slot(gate_app, client):
        response = await client.get(f"{prefix}{path}")

    assert response.status_code == 200, response.text
    assert "errors" not in response.json()


async def test_v1_photos_keep_the_legacy_shape_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient
) -> None:
    """AC-8: the body is the usual `{"photos": [...]}`."""
    async with held_slot(gate_app, client):
        response = await client.get("/api/v1/rovers/curiosity/photos?sol=1000")

    assert response.json() == {"photos": []}


# --- AC-11 ----------------------------------------------


async def test_total_of_full_photos_comes_from_sol_summaries(
    clean_db: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-11: `sample_type=full` without a sol: total = sum(full_photos) = count(full); no
    count over `photos` is executed."""
    sols = {300: (3, 2), 301: (1, 1)}  # sol -> (full photos, other photos)
    records = []
    for sol, (full, other) in sols.items():
        for n in range(full):
            records.append(build_record(source_id=f"f-{sol}-{n}", sol=sol, camera_code="FHAZ"))
        for n in range(other):
            records.append(
                build_record(
                    source_id=f"o-{sol}-{n}",
                    sol=sol,
                    camera_code="FHAZ",
                    sample_type="subframe" if n % 2 == 0 else "downsampled",
                )
            )
    async with clean_db() as session:
        await upsert_photos(session, "curiosity", records)
        await recompute_sol_summaries(
            session, "curiosity", {sol: full + other for sol, (full, other) in sols.items()}
        )
        await session.commit()
        summed = await session.scalar(
            text("SELECT sum(full_photos) FROM sol_summaries WHERE rover_id = 'curiosity'")
        )
        counted = await session.scalar(
            text(
                "SELECT count(*) FROM photos WHERE rover_id = 'curiosity' AND sample_type = 'full'"
            )
        )
    assert summed == counted == 4  # precondition: full (4) differs from all photos (7)

    async def forbidden(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("count over photos must not run for sample_type=full")

    monkeypatch.setattr(photos_repo, "count_photos", forbidden)
    app = create_app(settings)
    try:
        async with make_client(app) as http_client:
            response = await http_client.get(f"{V2}/rovers/curiosity/photos?sample_type=full")
            multi = await http_client.get(f"{V2}/photos?rovers=curiosity&sample_type=full")
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 4
    assert multi.status_code == 200, multi.text
    assert multi.json()["total"] == 4


# --- stage 19, AC-12: `fields` is validated before the slot and the database -------------------

FIELDS_HEAVY_URLS = {
    "rover-photos": HEAVY_URL,
    "photos-multi": MULTI_URL,
}


@pytest.mark.parametrize("url", FIELDS_HEAVY_URLS.values(), ids=FIELDS_HEAVY_URLS)
async def test_ac12_bad_fields_on_a_heavy_search_is_422_not_503_while_the_gate_is_full(
    gate_app: FastAPI, client: httpx.AsyncClient, url: str
) -> None:
    """Stage 19 AC-12: the `fields` check runs before the heavy-query slot and the database."""
    async with held_slot(gate_app, client):
        bad = await client.get(f"{url}&fields=foo")
        control = await client.get(f"{url}&fields=sol")

    assert bad.status_code == 422, bad.text
    assert bad.json()["error"]["code"] == "validation_error"
    assert [d["field"] for d in bad.json()["error"]["details"]] == ["fields"]
    assert "retry-after" not in bad.headers
    # The same search with a valid `fields` is still refused as busy: `fields` does not change
    # the heavy-query rules.
    assert_busy(control)


async def test_ac12_bad_fields_does_not_take_a_slot_or_reach_the_database(
    gate_app: FastAPI, client: httpx.AsyncClient, harness: Harness
) -> None:
    calls_before = harness.stub.calls

    response = await client.get(f"{HEAVY_URL}&fields=SOL")

    assert response.status_code == 422
    assert harness.stub.calls == calls_before
