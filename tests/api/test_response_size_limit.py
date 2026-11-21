"""Cost of big v1 responses in the rate limit over HTTP (docs/proposals/legacy-response-size.md).

AC-2 ... AC-6, AC-8 (disabled limiter), AC-9, AC-10, AC-11. (AC-1, AC-7 and the settings part of
AC-8 are unit tests: tests/unit/test_response_size.py; AC-12 is the unchanged rate-limit suite.)

Big bodies come from test routes added to the app (`/api/v1/__blob?n=...`), so no database rows
are needed. Without gzip a client must send `Accept-Encoding: identity` (httpx asks for gzip
by default). The limiter works with 60 per minute / 2000 per hour, the byte unit U is 1024 and
time is a fake clock (`app.state.rate_limiter.clock`); nothing sleeps.

The cost of a response is read from `X-RateLimit-Remaining` of the next request to a tiny
route: remaining = 60 - (hits of the earlier requests) - 1.
"""

import gzip
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Response
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.ratelimit import extra_hits
from app.core.config import Settings
from app.core.errors import ServiceBusy
from app.main import create_app
from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from app.services import legacy as legacy_service
from tests.conftest import build_record

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

UNIT = 1024
PER_MINUTE = 60
PER_HOUR = 2000
IP_A = "203.0.113.51"
IP_B = "203.0.113.52"
PLAIN = {"Accept-Encoding": "identity"}
GZIP = {"Accept-Encoding": "gzip"}

BLOB = "/api/v1/__blob"
BLOB_ALT = "/mars-photos/api/v1/__blob"
BLOB_V2 = "/api/v2/__blob"
BLOB_ROOT = "/__blob"
SMALL = "/api/v1/__small"
SMALL_V2 = "/api/v2/__small"


class Clock:
    def __init__(self, now: float = 70_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _random_hex(size: int) -> bytes:
    """Deterministic, compressible to roughly one half."""
    return random.Random(20261121).randbytes(size // 2 + 1).hex().encode()[:size]


def add_test_routes(app: FastAPI) -> None:
    def blob(n: int = 0, mode: str = "plain") -> Response:
        body = b"a" * n if mode == "plain" else _random_hex(n)
        return Response(content=body, media_type="text/plain")

    def small() -> Response:
        return Response(content=b"ok", media_type="text/plain")

    async def busy() -> None:
        raise ServiceBusy(1)

    for path in (BLOB, BLOB_ALT, BLOB_V2, BLOB_ROOT):
        app.add_api_route(path, blob, methods=["GET"])
    for path in (SMALL, SMALL_V2):
        app.add_api_route(path, small, methods=["GET"])
    app.add_api_route("/api/v1/__busy", busy, methods=["GET"])


def make_settings(settings: Settings, **extra: Any) -> Settings:
    return settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_per_minute": PER_MINUTE,
            "rate_limit_per_hour": PER_HOUR,
            "rate_limit_v1_bytes_per_request": UNIT,
            **extra,
        }
    )


def client_for(app: FastAPI, ip: str = IP_A) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


Build = Callable[..., Awaitable[tuple[FastAPI, Clock]]]


@pytest.fixture
async def build(settings: Settings) -> AsyncIterator[Build]:
    apps: list[FastAPI] = []

    async def make(**extra: Any) -> tuple[FastAPI, Clock]:
        app = create_app(make_settings(settings, **extra))
        apps.append(app)
        add_test_routes(app)
        clock = Clock()
        if getattr(app.state, "rate_limiter", None) is not None:
            app.state.rate_limiter.clock = clock
        return app, clock

    try:
        yield make
    finally:
        for app in apps:
            await app.state.engine.dispose()


def remaining(response: httpx.Response) -> int:
    return int(response.headers["x-ratelimit-remaining"])


async def spent_after(client: httpx.AsyncClient, probe: str = SMALL) -> int:
    """Hits already counted, read through one more (counted) request to a tiny route."""
    response = await client.get(probe, headers=PLAIN)
    assert response.status_code == 200, response.text
    return PER_MINUTE - remaining(response) - 1


# --- AC-2 / AC-3: the cost of one response ----------------------------------------------------

SIZES = [
    pytest.param(0, 1, id="empty-body"),
    pytest.param(1, 1, id="one-byte"),
    pytest.param(UNIT - 1, 1, id="u-minus-1"),
    pytest.param(UNIT, 1, id="exactly-u"),
    pytest.param(UNIT + 1, 2, id="u-plus-1"),
    pytest.param(2 * UNIT, 2, id="exactly-2u"),
    pytest.param(2 * UNIT + 1, 3, id="2u-plus-1"),
    pytest.param(10 * UNIT, 10, id="10u"),
    pytest.param(31 * UNIT - 5, 31, id="almost-31u"),
]


@pytest.mark.parametrize(("size", "cost"), SIZES)
async def test_ac2_ac3_v1_response_costs_started_units_without_gzip(
    build: Build, size: int, cost: int
) -> None:
    """AC-2 (B > U: ceil(B / U) requests) and AC-3 (B <= U: exactly one)."""
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{BLOB}?n={size}", headers=PLAIN)
        assert response.status_code == 200
        assert len(response.content) == size
        # The response's own headers do not include the extra charge (decision 4) ...
        assert remaining(response) == PER_MINUTE - 1
        # ... the next ones do: `cost` hits for the big one, 1 for the probe itself.
        assert await spent_after(client) == cost


async def test_ac2_x_ratelimit_remaining_of_the_next_response_shows_the_charge(
    build: Build,
) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={5 * UNIT}", headers=PLAIN)
        probe = await client.get(SMALL, headers=PLAIN)

    assert remaining(probe) == PER_MINUTE - 5 - 1


async def test_ac2_charge_is_in_both_windows(build: Build) -> None:
    """The hour window is tighter here, so Remaining is read from it."""
    app, _ = await build(rate_limit_per_minute=1000, rate_limit_per_hour=100)
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={4 * UNIT}", headers=PLAIN)
        probe = await client.get(SMALL, headers=PLAIN)

    assert probe.headers["x-ratelimit-limit"] == "100"
    assert remaining(probe) == 100 - 4 - 1


async def test_ac2_charge_leaves_the_window_with_time(build: Build) -> None:
    app, clock = await build()
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={10 * UNIT}", headers=PLAIN)
        clock.advance(61)
        probe = await client.get(SMALL, headers=PLAIN)

    assert remaining(probe) == PER_MINUTE - 1


async def test_ac3_head_of_a_big_resource_costs_one_request(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        head = await client.head(f"{BLOB}?n={10 * UNIT}", headers=PLAIN)
        assert head.status_code == 200
        assert head.content == b""
        assert await spent_after(client) == 1


async def test_ac3_not_modified_costs_one_request(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        first = await client.get(f"{BLOB}?n={6 * UNIT}", headers=PLAIN)  # 6 requests
        etag = first.headers["etag"]
        not_modified = await client.get(
            f"{BLOB}?n={6 * UNIT}", headers={**PLAIN, "If-None-Match": etag}
        )
        assert not_modified.status_code == 304
        # `spent_after` counts hits made before the probe: 6 (big) + 1 (the 304) = 7.
        assert await spent_after(client) == 6 + 1


@pytest.mark.parametrize(
    ("url", "status"),
    [
        pytest.param("/api/v1/rovers/pluto", 400, id="v1-400"),
        pytest.param("/api/v1/rovers/curiosity/photos?sol=abc", 400, id="v1-invalid-param"),
        pytest.param("/api/v1/__busy", 503, id="v1-503"),
        pytest.param("/api/v1/__no_such_route", 404, id="v1-404"),
    ],
)
async def test_ac3_short_error_responses_cost_one_request(
    build: Build, url: str, status: int
) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(url, headers=PLAIN)
        assert response.status_code == status, response.text
        assert await spent_after(client) == 1


async def test_ac3_rejected_429_adds_no_charge(build: Build) -> None:
    app, clock = await build(rate_limit_per_minute=2)
    async with client_for(app) as client:
        await client.get(SMALL, headers=PLAIN)
        await client.get(SMALL, headers=PLAIN)
        rejected = [await client.get(SMALL, headers=PLAIN) for _ in range(3)]
        clock.advance(61)
        probe = await client.get(SMALL, headers=PLAIN)

    assert [r.status_code for r in rejected] == [429] * 3
    assert probe.status_code == 200
    assert probe.headers["x-ratelimit-remaining"] == "1"


# --- AC-4: gzip ---------------------------------------------------------------------------


async def test_ac4_gzip_response_is_charged_by_the_compressed_size(build: Build) -> None:
    app, _ = await build()
    raw = 40 * UNIT
    wire = len(gzip.compress(_random_hex(raw), compresslevel=9, mtime=0))
    expected_gz = extra_hits(wire, UNIT) + 1
    expected_plain = extra_hits(raw, UNIT) + 1
    assert expected_gz < expected_plain  # precondition: the sample really shrinks

    async with client_for(app, IP_A) as zipped, client_for(app, IP_B) as plain:
        gz_response = await zipped.get(f"{BLOB}?n={raw}&mode=random", headers=GZIP)
        plain_response = await plain.get(f"{BLOB}?n={raw}&mode=random", headers=PLAIN)
        assert gz_response.headers["content-encoding"] == "gzip"
        assert "content-encoding" not in plain_response.headers
        assert gz_response.content == plain_response.content
        gz_cost = await spent_after(zipped)
        plain_cost = await spent_after(plain)

    assert plain_cost == expected_plain
    assert gz_cost < plain_cost
    # The gzip stream of the app may differ from ours by a few bytes: within one unit.
    assert abs(gz_cost - expected_gz) <= 1


async def test_ac4_highly_compressible_body_costs_one_request_with_gzip(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{BLOB}?n={50 * UNIT}", headers=GZIP)
        assert response.headers["content-encoding"] == "gzip"
        assert await spent_after(client) == 1


@pytest.mark.parametrize("encoding", ["br", "deflate", "identity", "zstd"])
async def test_ac4_other_encodings_get_an_uncompressed_body_and_full_cost(
    build: Build, encoding: str
) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{BLOB}?n={5 * UNIT}", headers={"Accept-Encoding": encoding})
        assert "content-encoding" not in response.headers
        assert await spent_after(client) == 5


# --- AC-5: which paths are charged ------------------------------------------------------------


@pytest.mark.parametrize("blob", [BLOB, BLOB_ALT], ids=["api-v1", "mars-photos-api-v1"])
async def test_ac5_both_v1_prefixes_are_charged_by_bytes(build: Build, blob: str) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await client.get(f"{blob}?n={7 * UNIT}", headers=PLAIN)
        assert await spent_after(client) == 7


async def test_ac5_v2_response_costs_one_request_whatever_its_size(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{BLOB_V2}?n={20 * UNIT}", headers=PLAIN)
        assert response.status_code == 200
        assert len(response.content) == 20 * UNIT
        assert await spent_after(client, SMALL_V2) == 1


async def test_ac5_unlimited_paths_are_not_charged(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        big = await client.get(f"{BLOB_ROOT}?n={20 * UNIT}", headers=PLAIN)
        health = await client.get("/health", headers=PLAIN)
        root = await client.get("/", headers=PLAIN)
        assert big.status_code == 200
        assert health.status_code != 429 and root.status_code != 429
        for response in (big, health, root):
            assert "x-ratelimit-remaining" not in response.headers
        # Nothing was counted for this client.
        assert app.state.rate_limiter.peek("203.0.113.51").remaining == PER_MINUTE


# --- AC-6: the attack from the spec -----------------------------------------------------------


@pytest.mark.parametrize("blob", [BLOB, BLOB_ALT], ids=["api-v1", "mars-photos-api-v1"])
async def test_ac6_big_uncompressed_v1_responses_hit_429_on_the_fourth_request(
    build: Build, blob: str
) -> None:
    """Limit 30 per minute, body 10 x U (10 requests each): 200, 200, 200, then 429."""
    app, clock = await build(rate_limit_per_minute=30)
    url = f"{blob}?n={10 * UNIT}"
    async with client_for(app, IP_A) as attacker, client_for(app, IP_B) as bystander:
        statuses = [(await attacker.get(url, headers=PLAIN)).status_code for _ in range(3)]
        blocked = await attacker.get(url, headers=PLAIN)
        other_ip = await bystander.get(url, headers=PLAIN)
        small_blocked = await attacker.get(SMALL, headers=PLAIN)
        clock.advance(int(blocked.headers["retry-after"]) + 1)
        recovered = await attacker.get(SMALL, headers=PLAIN)

    assert statuses == [200, 200, 200]
    assert blocked.status_code == 429
    assert blocked.json() == {"errors": "Rate limit exceeded"}
    assert int(blocked.headers["retry-after"]) >= 1
    assert other_ip.status_code == 200
    assert small_blocked.status_code == 429  # the whole key is blocked, not only big requests
    assert recovered.status_code == 200


async def test_ac6_the_last_big_response_in_the_window_still_passes(build: Build) -> None:
    """Decision 4: no pre-check; a 10 x U response with 5 requests left is served in full."""
    app, _ = await build(rate_limit_per_minute=5)
    async with client_for(app) as client:
        big = await client.get(f"{BLOB}?n={10 * UNIT}", headers=PLAIN)
        after = await client.get(SMALL, headers=PLAIN)

    assert big.status_code == 200
    assert len(big.content) == 10 * UNIT
    assert after.status_code == 429
    assert after.headers["x-ratelimit-remaining"] == "0"
    assert int(after.headers["retry-after"]) >= 1


async def test_ac6_retry_after_covers_the_charged_requests(build: Build) -> None:
    app, clock = await build(rate_limit_per_minute=5)
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={10 * UNIT}", headers=PLAIN)
        clock.advance(30)
        blocked = await client.get(SMALL, headers=PLAIN)

    # The charged hits were counted at t0: they leave the window 30 s from now.
    assert blocked.status_code == 429
    assert 29 <= int(blocked.headers["retry-after"]) <= 31


async def test_ac6_gzip_client_downloads_the_same_resource_many_times(build: Build) -> None:
    """The honest client (gzip, compressible body) is not hurt: 10 big downloads, all 200."""
    app, _ = await build(rate_limit_per_minute=30)
    async with client_for(app) as client:
        statuses = [
            (await client.get(f"{BLOB}?n={10 * UNIT}", headers=GZIP)).status_code for _ in range(10)
        ]

    assert statuses == [200] * 10


# --- AC-8: limiter disabled -------------------------------------------------------------------


async def test_ac8_disabled_limiter_adds_no_charge_and_no_headers(build: Build) -> None:
    app, _ = await build(rate_limit_enabled=False, rate_limit_per_minute=1)
    assert getattr(app.state, "rate_limiter", None) is None
    async with client_for(app) as client:
        responses = [await client.get(f"{BLOB}?n={10 * UNIT}", headers=PLAIN) for _ in range(5)]

    for response in responses:
        assert response.status_code == 200
        assert len(response.content) == 10 * UNIT
        assert not [h for h in response.headers if h.startswith("x-ratelimit")]


# --- AC-9: v1 list while the heavy gate is full -----------------------------------------------


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_ac9_v1_sol_list_without_page_is_served_with_a_full_heavy_gate(
    build: Build, prefix: str
) -> None:
    app, _ = await build()
    gate = app.state.heavy_query_gate
    gate.in_use = gate.limit  # the gate is full
    try:
        async with client_for(app) as client:
            response = await client.get(f"{prefix}/rovers/curiosity/photos?sol=1000", headers=PLAIN)
    finally:
        gate.in_use = 0

    assert response.status_code == 200, response.text
    assert response.json() == {"photos": []}


# --- AC-10: route template in the error logs --------------------------------------------------

SECRET = "zzsecretzz"
V1_ERROR_URL = f"/api/v1/rovers/{SECRET}/photos?sol=1"

LOG_CASES = [
    pytest.param(
        lambda: DBAPIError("SELECT 1", {}, Exception("db says no")),
        500,
        "Database error",
        id="database-error",
    ),
    pytest.param(
        lambda: RuntimeError("kaboom"),
        500,
        "Unhandled error",
        id="unhandled-error",
    ),
    pytest.param(
        lambda: OperationalError("SELECT 1", {}, Exception("connection refused")),
        503,
        "Database unavailable",
        id="database-unavailable",
    ),
]


@pytest.mark.parametrize(("make_error", "status", "prefix"), LOG_CASES)
async def test_ac10_error_logs_carry_the_route_template_not_the_raw_path(
    build: Build,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    make_error: Callable[[], Exception],
    status: int,
    prefix: str,
) -> None:
    async def failing(*args: Any, **kwargs: Any) -> None:
        raise make_error()

    monkeypatch.setattr(legacy_service, "search_photos", failing)
    app, _ = await build()
    # create_app configures logging (it drops the root handlers, caplog's included).
    root = logging.getLogger()
    if caplog.handler not in root.handlers:
        root.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.DEBUG):
            async with client_for(app) as client:
                response = await client.get(V1_ERROR_URL, headers=PLAIN)
    finally:
        root.removeHandler(caplog.handler)

    assert response.status_code == status, response.text
    lines = [r for r in caplog.records if r.getMessage().startswith(prefix)]
    assert len(lines) == 1, [r.getMessage() for r in caplog.records]
    message = lines[0].getMessage()
    assert "path=/rovers/{rover}/photos" in message, message
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in str(record.args)


# --- AC-11: the rover slug is case-insensitive and cannot bypass the heavy gate ---------------

V2_HEAVY = "date_min=2012-08-06&sample_type=full"
Factory = async_sessionmaker[AsyncSession]


async def seed_curiosity(factory: Factory) -> None:
    """Two Curiosity photos (sol 1), so that both spellings of the slug compare real data."""
    records = [
        build_record(
            source_id=f"size-c{index}",
            sol=1,
            camera_code="FHAZ",
            sample_type="full",
            taken_at=datetime(2012, 8, 7, 12, index, tzinfo=UTC),
            earth_date=date(2012, 8, 7),
            img_full=f"https://images.example.test/size-c{index}.JPG",
            credit="NASA/JPL-Caltech/MSSS",
        )
        for index in (1, 2)
    ]
    async with factory() as session:
        await upsert_photos(session, "curiosity", records)
        await recompute_sol_summaries(session, "curiosity", {1})
        await refresh_rover_stats(session, "curiosity")
        await session.commit()


async def test_ac11_heavy_filter_with_a_free_gate_serves_capitalised_slug_like_lowercase(
    build: Build, clean_db: Factory
) -> None:
    await seed_curiosity(clean_db)
    app, _ = await build(rate_limit_enabled=False)
    async with client_for(app) as client:
        upper = await client.get(f"/api/v2/rovers/Curiosity/photos?{V2_HEAVY}")
        lower = await client.get(f"/api/v2/rovers/curiosity/photos?{V2_HEAVY}")

    assert lower.status_code == 200, lower.text
    assert upper.status_code == 200, upper.text
    assert len(lower.json()["items"]) == 2, lower.text  # real data, not two empty lists
    assert upper.json() == lower.json()


async def test_ac11_heavy_filter_with_a_full_gate_is_refused_for_both_spellings(
    build: Build,
) -> None:
    app, _ = await build(rate_limit_enabled=False)
    gate = app.state.heavy_query_gate
    gate.in_use = gate.limit
    try:
        async with client_for(app) as client:
            lower = await client.get(f"/api/v2/rovers/curiosity/photos?{V2_HEAVY}")
            upper = await client.get(f"/api/v2/rovers/Curiosity/photos?{V2_HEAVY}")
    finally:
        gate.in_use = 0

    assert lower.status_code == 503, lower.text  # precondition: the gate works
    for response in (lower, upper):
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "service_busy"


async def test_ac11_light_filter_with_a_full_gate_is_served_for_a_capitalised_slug(
    build: Build, clean_db: Factory
) -> None:
    await seed_curiosity(clean_db)
    app, _ = await build(rate_limit_enabled=False)
    gate = app.state.heavy_query_gate
    gate.in_use = gate.limit
    try:
        async with client_for(app) as client:
            upper = await client.get("/api/v2/rovers/Curiosity/photos?sol=1")
            lower = await client.get("/api/v2/rovers/curiosity/photos?sol=1")
    finally:
        gate.in_use = 0

    assert lower.status_code == 200, lower.text
    assert upper.status_code == 200, upper.text
    assert len(lower.json()["items"]) == 2, lower.text
    assert upper.json() == lower.json()
