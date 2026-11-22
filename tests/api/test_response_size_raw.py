"""Price of big v1 responses by raw (uncompressed) size (docs/proposals/legacy-response-cpu.md).

AC-2 ... AC-7 over HTTP (AC-1 and AC-8 are unit tests in tests/unit/test_response_size.py; AC-9
is the unchanged suites tests/api/test_response_size_limit.py and tests/unit/test_response_size.py).
Test names start with `test_cpu_acN_` so they do not clash with the numbers of the earlier spec.

Units: wire U = 1024, raw R = 4096 (`rate_limit_v1_raw_bytes_per_request` and the hook
`app.state.v1_raw_bytes_per_request`). Bodies come from test routes under `/api/v1/__blob` (a
highly compressible `a * n`), so no database rows are needed. Time is a fake clock.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Response
from starlette.background import BackgroundTask

from app.api.caching import RESPONSE_SIZE_SCOPE_KEY
from app.core.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

UNIT = 1024
RAW = 4 * UNIT
PER_MINUTE = 60
PER_HOUR = 2000
IP_A = "203.0.113.61"
IP_B = "203.0.113.62"
PLAIN = {"Accept-Encoding": "identity"}
GZIP = {"Accept-Encoding": "gzip"}

BLOB = "/api/v1/__blob"
BLOB_ALT = "/mars-photos/api/v1/__blob"
BLOB_V2 = "/api/v2/__blob"
STATUS = "/api/v1/__status"
PROBE = "/api/v1/__probe"
PROBE_V2 = "/api/v2/__probe"
SMALL = "/api/v1/__small"
SMALL_V2 = "/api/v2/__small"


class Clock:
    def __init__(self, now: float = 90_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def add_test_routes(app: FastAPI) -> None:
    captured: list[dict[str, Any]] = []
    app.state.captured_sizes = captured

    def blob(n: int = 0) -> Response:
        return Response(content=b"a" * n, media_type="text/plain")

    def status(n: int = 0, code: int = 404) -> Response:
        headers = {"Location": "/api/v1/__small"} if 300 <= code < 400 else None
        return Response(
            content=b"a" * n, status_code=code, media_type="text/plain", headers=headers
        )

    def small() -> Response:
        return Response(content=b"ok", media_type="text/plain")

    for path in (BLOB, BLOB_ALT, BLOB_V2):
        app.add_api_route(path, blob, methods=["GET"])
    app.add_api_route(STATUS, status, methods=["GET"])
    app.add_api_route(SMALL, small, methods=["GET"])
    app.add_api_route(SMALL_V2, small, methods=["GET"])


def add_probe_route(app: FastAPI) -> None:
    """A route that records the size dict of its own scope after the response is complete."""
    from fastapi import Request

    async def probe(request: Request, n: int = 0, code: int = 200) -> Response:
        info = request.scope.get(RESPONSE_SIZE_SCOPE_KEY)

        def capture() -> None:
            app.state.captured_sizes.append(None if info is None else dict(info))

        return Response(
            content=b"a" * n,
            status_code=code,
            media_type="text/plain",
            background=BackgroundTask(capture),
        )

    app.add_api_route(PROBE, probe, methods=["GET"])
    app.add_api_route(PROBE_V2, probe, methods=["GET"])


Build = Callable[..., Awaitable[tuple[FastAPI, Clock]]]


@pytest.fixture
async def build(settings: Settings) -> AsyncIterator[Build]:
    apps: list[FastAPI] = []

    async def make(**extra: Any) -> tuple[FastAPI, Clock]:
        values: dict[str, Any] = {
            "rate_limit_enabled": True,
            "rate_limit_per_minute": PER_MINUTE,
            "rate_limit_per_hour": PER_HOUR,
            "rate_limit_v1_bytes_per_request": UNIT,
            "rate_limit_v1_raw_bytes_per_request": RAW,
            **extra,
        }
        app = create_app(settings.model_copy(update=values))
        apps.append(app)
        add_test_routes(app)
        add_probe_route(app)
        app.state.v1_raw_bytes_per_request = values["rate_limit_v1_raw_bytes_per_request"]
        app.state.v1_bytes_per_request = values["rate_limit_v1_bytes_per_request"]
        clock = Clock()
        if getattr(app.state, "rate_limiter", None) is not None:
            app.state.rate_limiter.clock = clock
        return app, clock

    try:
        yield make
    finally:
        for app in apps:
            await app.state.engine.dispose()


def client_for(app: FastAPI, ip: str = IP_A) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 40000), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def remaining(response: httpx.Response) -> int:
    return int(response.headers["x-ratelimit-remaining"])


async def spent_after(client: httpx.AsyncClient, probe: str = SMALL) -> int:
    """Hits already counted, read through one more (counted) request to a tiny route."""
    response = await client.get(probe, headers=PLAIN)
    assert response.status_code == 200, response.text
    return PER_MINUTE - remaining(response) - 1


# --- AC-2: GET with gzip ----------------------------------------------------------------------


@pytest.mark.parametrize("blob", [BLOB, BLOB_ALT], ids=["api-v1", "mars-photos-api-v1"])
async def test_cpu_ac2_gzip_get_of_a_10r_body_costs_10_requests(build: Build, blob: str) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{blob}?n={10 * RAW}", headers=GZIP)
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert len(response.content) == 10 * RAW  # httpx has decoded it
        # The wire size is tiny (one request by bytes), so the 10 come from the raw side.
        assert remaining(response) == PER_MINUTE - 1  # own headers do not include the charge
        assert await spent_after(client) == 10


async def test_cpu_ac2_the_charge_is_in_the_hour_window_too(build: Build) -> None:
    app, _ = await build(rate_limit_per_minute=1000, rate_limit_per_hour=100)
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={10 * RAW}", headers=GZIP)
        probe = await client.get(SMALL, headers=PLAIN)

    assert probe.headers["x-ratelimit-limit"] == "100"
    assert remaining(probe) == 100 - 10 - 1


async def test_cpu_ac2_ninth_gzip_request_of_a_big_sol_is_blocked(build: Build) -> None:
    """Spec goal: with 8 requests per big response and 60 per minute, 7 pass and then 429."""
    app, clock = await build(rate_limit_per_minute=60)
    url = f"{BLOB}?n={8 * RAW}"  # 8 requests each
    async with client_for(app) as client:
        statuses = [(await client.get(url, headers=GZIP)).status_code for _ in range(8)]
        blocked = await client.get(url, headers=GZIP)
        clock.advance(int(blocked.headers["retry-after"]) + 1)
        recovered = await client.get(SMALL, headers=PLAIN)

    # 7 x 8 = 56 hits, the 8th request (hit 57) passes and adds 8: the window is over its limit.
    assert statuses == [200] * 8
    assert blocked.status_code == 429
    assert blocked.json() == {"errors": "Rate limit exceeded"}
    assert recovered.status_code == 200


@pytest.mark.parametrize("size", [RAW, RAW + 1, 2 * RAW, 2 * RAW + 1, 10 * RAW + 1])
async def test_cpu_ac2_gzip_cost_boundaries_follow_started_raw_units(
    build: Build, size: int
) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={size}", headers=GZIP)
        cost = await spent_after(client)

    assert cost == -(-size // RAW)


# --- AC-3: HEAD -------------------------------------------------------------------------------


async def test_cpu_ac3_head_of_a_10r_resource_costs_10_requests(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        get = await client.get(f"{BLOB}?n={10 * RAW}", headers=GZIP)
        head = await client.head(f"{BLOB}?n={10 * RAW}", headers=GZIP)
        assert head.status_code == 200
        assert head.content == b""
        for name in ("etag", "cache-control", "vary", "content-type"):
            assert head.headers.get(name) == get.headers.get(name), name
        # GET (10) + HEAD (10) + the 2 requests themselves are not extra: 10 + 10 = 20 hits.
        assert await spent_after(client) == 20


async def test_cpu_ac3_head_alone_costs_10_requests(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        head = await client.head(f"{BLOB}?n={10 * RAW}", headers=GZIP)
        assert head.status_code == 200
        assert head.content == b""
        assert await spent_after(client) == 10


async def test_cpu_ac3_head_without_gzip_costs_the_same(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        head = await client.head(f"{BLOB}?n={10 * RAW}", headers=PLAIN)
        assert head.content == b""
        assert (
            await spent_after(client) == 10 * RAW // RAW
        )  # R >= U: raw side (10) wins over wire (0)


async def test_cpu_ac3_head_of_a_small_resource_costs_one_request(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        head = await client.head(f"{BLOB}?n={RAW}", headers=GZIP)
        assert head.status_code == 200
        assert await spent_after(client) == 1


# --- AC-4: 304 --------------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", [GZIP, PLAIN], ids=["gzip", "identity"])
async def test_cpu_ac4_not_modified_of_a_10r_resource_costs_10_requests(
    build: Build, encoding: dict[str, str]
) -> None:
    app, _ = await build()
    url = f"{BLOB}?n={10 * RAW}"
    async with client_for(app, IP_B) as first, client_for(app, IP_A) as second:
        etag = (await first.get(url, headers=GZIP)).headers["etag"]
        not_modified = await second.get(url, headers={**encoding, "If-None-Match": etag})
        assert not_modified.status_code == 304
        assert not_modified.content == b""
        assert await spent_after(second) == 10


async def test_cpu_ac4_not_modified_of_a_small_resource_costs_one_request(build: Build) -> None:
    app, _ = await build()
    url = f"{BLOB}?n={RAW}"
    async with client_for(app) as client:
        etag = (await client.get(url, headers=GZIP)).headers["etag"]
        not_modified = await client.get(url, headers={**GZIP, "If-None-Match": etag})
        assert not_modified.status_code == 304
        assert await spent_after(client) == 1 + 1  # the 200 and the 304: one request each


# --- AC-5: no gzip ----------------------------------------------------------------------------

# R = 4 U in the fixture; the cases with R = U are in the next test.
PLAIN_CASES = [
    pytest.param(0, 1, id="empty"),
    pytest.param(UNIT, 1, id="exactly-u"),
    pytest.param(UNIT + 1, 2, id="u-plus-1"),
    pytest.param(RAW, 4, id="exactly-r"),
    pytest.param(RAW + 1, 5, id="r-plus-1"),
    pytest.param(10 * RAW, 40, id="10r"),
]


@pytest.mark.parametrize(("size", "cost"), PLAIN_CASES)
async def test_cpu_ac5_plain_get_costs_max_of_wire_and_raw_units(
    build: Build, size: int, cost: int
) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{BLOB}?n={size}", headers=PLAIN)
        assert len(response.content) == size
        assert await spent_after(client) == cost == max(1, -(-size // UNIT), -(-size // RAW))


@pytest.mark.parametrize(
    ("size", "cost"),
    [
        pytest.param(UNIT, 1, id="exactly-u"),
        pytest.param(UNIT + 1, 2, id="u-plus-1"),
        pytest.param(7 * UNIT, 7, id="7u"),
    ],
)
async def test_cpu_ac5_with_r_equal_to_u_plain_get_costs_what_it_did_before(
    build: Build, size: int, cost: int
) -> None:
    app, _ = await build(rate_limit_v1_raw_bytes_per_request=UNIT)
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={size}", headers=PLAIN)
        assert await spent_after(client) == cost


async def test_cpu_ac5_a_larger_r_makes_the_wire_side_decide(build: Build) -> None:
    app, _ = await build(rate_limit_v1_raw_bytes_per_request=1_048_576)
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={6 * UNIT}", headers=PLAIN)
        assert await spent_after(client) == 6


async def test_cpu_ac5_r_is_read_per_request_from_the_test_hook(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={10 * RAW}", headers=GZIP)
        assert await spent_after(client) == 10
        app.state.v1_raw_bytes_per_request = 2 * RAW
        await client.get(f"{BLOB}?n={10 * RAW}", headers=GZIP)
        # 10 + the probe + 5 + this probe: the second response costs 5.
        assert await spent_after(client) == 10 + 1 + 5


# --- AC-6: small v1 bodies and v2 stay at one request --------------------------------------------


@pytest.mark.parametrize(
    "size", [0, 1, RAW - 1, RAW], ids=["empty", "one", "r-minus-1", "exactly-r"]
)
async def test_cpu_ac6_v1_gzip_body_up_to_r_costs_one_request(build: Build, size: int) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={size}", headers=GZIP)
        assert await spent_after(client) == 1


async def test_cpu_ac6_v1_body_of_r_plus_1_with_gzip_costs_two(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await client.get(f"{BLOB}?n={RAW + 1}", headers=GZIP)
        assert await spent_after(client) == 2


@pytest.mark.parametrize("encoding", [GZIP, PLAIN], ids=["gzip", "identity"])
async def test_cpu_ac6_v2_response_costs_one_request_whatever_its_size(
    build: Build, encoding: dict[str, str]
) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{BLOB_V2}?n={100 * RAW}", headers=encoding)
        assert response.status_code == 200
        head = await client.head(f"{BLOB_V2}?n={100 * RAW}", headers=encoding)
        assert head.status_code == 200
        # Two v2 requests so far, one hit each.
        assert await spent_after(client, SMALL_V2) == 2


async def test_cpu_ac6_v2_gets_no_size_dict_in_its_scope(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        await client.get(f"{PROBE_V2}?n=10", headers=GZIP)

    assert app.state.captured_sizes == [None]


async def test_cpu_ac6_disabled_limiter_serves_big_gzip_responses_without_charge(
    build: Build,
) -> None:
    app, _ = await build(rate_limit_enabled=False, rate_limit_per_minute=1)
    async with client_for(app) as client:
        responses = [await client.get(f"{BLOB}?n={10 * RAW}", headers=GZIP) for _ in range(5)]

    for response in responses:
        assert response.status_code == 200
        assert len(response.content) == 10 * RAW
        assert not [h for h in response.headers if h.startswith("x-ratelimit")]


# --- AC-2 / AC-3 / AC-7: what the caching layer reports ---------------------------------------


async def test_cpu_ac2_caching_layer_reports_the_raw_size_of_a_200(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{PROBE}?n=12345", headers=GZIP)

    assert response.status_code == 200
    assert app.state.captured_sizes == [{"raw": 12345}]


async def test_cpu_ac3_head_shares_the_size_dict_with_the_original_scope(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        head = await client.head(f"{PROBE}?n=12345", headers=GZIP)

    assert head.status_code == 200
    assert head.content == b""
    assert app.state.captured_sizes == [{"raw": 12345}]


@pytest.mark.parametrize("code", [400, 404, 500, 503], ids=["400", "404", "500", "503"])
async def test_cpu_ac7_non_200_responses_report_no_raw_size(build: Build, code: int) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{PROBE}?n={10 * RAW}&code={code}", headers=GZIP)

    assert response.status_code == code
    assert app.state.captured_sizes
    # The dict is present on v1 but stays empty: no raw size is reported for a non-200.
    assert app.state.captured_sizes[0] == {}


@pytest.mark.parametrize("code", [400, 404, 500, 503], ids=["400", "404", "500", "503"])
async def test_cpu_ac7_big_non_200_costs_by_the_wire_only(build: Build, code: int) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{STATUS}?n={10 * RAW}&code={code}", headers=GZIP)
        assert response.status_code == code
        assert response.headers["content-encoding"] == "gzip"
        assert await spent_after(client) == 1


async def test_cpu_ac7_big_non_200_without_gzip_is_still_charged_by_the_wire(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{STATUS}?n={10 * RAW}&code=404", headers=PLAIN)
        assert response.status_code == 404
        assert await spent_after(client) == 40  # ceil(10 * RAW / U), as under the earlier spec


async def test_cpu_ac7_redirect_with_a_big_body_costs_by_the_wire_only(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get(f"{STATUS}?n={10 * RAW}&code=302", headers=GZIP)
        assert response.status_code == 302
        assert await spent_after(client) == 1


async def test_cpu_ac7_short_v1_errors_cost_one_request(build: Build) -> None:
    app, _ = await build()
    async with client_for(app) as client:
        response = await client.get("/api/v1/rovers/pluto", headers=GZIP)
        assert response.status_code == 400
        assert await spent_after(client) == 1


async def test_cpu_ac7_rejected_429_adds_no_raw_charge(build: Build) -> None:
    app, clock = await build(rate_limit_per_minute=2)
    async with client_for(app) as client:
        await client.get(SMALL, headers=GZIP)
        await client.get(SMALL, headers=GZIP)
        rejected = [await client.get(f"{BLOB}?n={10 * RAW}", headers=GZIP) for _ in range(3)]
        clock.advance(61)
        probe = await client.get(SMALL, headers=PLAIN)

    assert [r.status_code for r in rejected] == [429] * 3
    assert probe.status_code == 200
    assert probe.headers["x-ratelimit-remaining"] == "1"
