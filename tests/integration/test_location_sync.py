"""Location sync (app/services/location_sync.py; stage 11, ADR-5..ADR-7).

Real Postgres (`clean_db`), the MMGIS files are mocked with respx on the recorded fixtures.
Covered: first load, conditional requests (304 / 200 with the same hash / 200 with new data),
the once-a-day rule and `force`, acceptance rules (shrink guard, max_sol, 50% valid objects,
root type), atomic replacement, failures recorded per file, the advisory lock, and the
scheduler hook (`SyncScheduler.run_once` -> `LocationSyncService.refresh_due`).
"""

import asyncio
import copy
import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.main import create_app
from app.services import scheduler as scheduler_module
from app.services.location_sync import (
    LOCATION_LOCK_NAMESPACE,
    DatasetResult,
    LocationSyncService,
)
from app.services.scheduler import SyncScheduler
from app.sources.http import UpstreamClient
from tests._locations import (
    BASE_URL,
    ROVER_KINDS,
    count,
    dataset,
    feature_collection,
    file_url,
    fixture_bytes,
    fixture_json,
    mock_files,
    ok_response,
    rows,
    sha256,
    to_body,
    total_calls,
    traverse_rows,
    waypoint_rows,
)
from tests.integration._fakes import FakeAdapter, legacy_id, photo_count

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

Factory = async_sessionmaker[AsyncSession]
Key = tuple[str, str]
PERSEVERANCE = "perseverance"
CURIOSITY = "curiosity"

EXPECTED_ITEMS = {
    (PERSEVERANCE, "waypoints"): 13,
    (PERSEVERANCE, "traverse"): 7,
    (CURIOSITY, "waypoints"): 13,
    (CURIOSITY, "traverse"): 10,  # the MultiLineString is split into 5 segments
}
EXPECTED_MAX_SOL = {
    (PERSEVERANCE, "waypoints"): 2022,
    (PERSEVERANCE, "traverse"): 2022,
    (CURIOSITY, "waypoints"): 5073,
    (CURIOSITY, "traverse"): None,
}


# --- fixtures and helpers ----------------------------------------------------------------------


@pytest.fixture
async def engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def location_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "location_base_url": BASE_URL,
            "location_refresh_hours": 24,
            "location_min_keep_ratio": 0.9,
            "upstream_max_rps": 0,
            "upstream_max_retries": 0,
            "upstream_retry_base_seconds": 0.0,
        }
    )


@pytest.fixture
async def make_service(
    engine: AsyncEngine, location_settings: Settings
) -> AsyncIterator[Callable[..., LocationSyncService]]:
    clients: list[UpstreamClient] = []

    def build(**overrides: Any) -> LocationSyncService:
        values = location_settings.model_copy(update=overrides)
        client = UpstreamClient(values)
        clients.append(client)
        return LocationSyncService(engine, client, values)

    try:
        yield build
    finally:
        for client in clients:
            await client.aclose()


@pytest.fixture
def service(make_service: Callable[..., LocationSyncService]) -> LocationSyncService:
    return make_service()


@pytest.fixture
def files(upstream_router: respx.MockRouter) -> dict[Key, respx.Route]:
    return mock_files(upstream_router)


def by_key(results: list[DatasetResult]) -> dict[Key, DatasetResult]:
    found = {(r.rover_id, r.kind): r for r in results}
    assert len(found) == len(results), "one result per (rover, kind)"
    return found


def statuses(results: list[DatasetResult]) -> dict[Key, str]:
    return {key: result.status for key, result in by_key(results).items()}


def all_status(status: str, keys: list[Key] | None = None) -> dict[Key, str]:
    return {key: status for key in (keys or ROVER_KINDS)}


def wp_features(rover: str = PERSEVERANCE) -> list[dict[str, Any]]:
    return fixture_json(rover, "waypoints")["features"]


def wp_body(features: list[Any], rover: str = PERSEVERANCE) -> bytes:
    base = {k: v for k, v in fixture_json(rover, "waypoints").items() if k != "features"}
    return feature_collection(features, base)


def set_wp_body(files: dict[Key, respx.Route], body: bytes, rover: str = PERSEVERANCE) -> None:
    files[(rover, "waypoints")].mock(return_value=ok_response(body))


async def first_load(service: LocationSyncService) -> datetime:
    now = datetime.now(UTC)
    assert statuses(await service.refresh_due(now)) == all_status("changed")
    return now


# --- first load --------------------------------------------------------------------------------


async def test_refresh_due_first_load_stores_every_file_and_reports_changed(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    results = by_key(await service.refresh_due(datetime.now(UTC)))

    assert set(results) == set(ROVER_KINDS)
    for key, result in results.items():
        assert isinstance(result, DatasetResult)
        assert (result.status, result.items, result.dropped, result.error) == (
            "changed",
            EXPECTED_ITEMS[key],
            0,
            None,
        ), key
    assert [route.call_count for route in files.values()] == [1, 1, 1, 1]
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 13
    assert await count(clean_db, "rover_waypoints", CURIOSITY) == 13
    assert await count(clean_db, "rover_traverse_segments", PERSEVERANCE) == 7
    assert await count(clean_db, "rover_traverse_segments", CURIOSITY) == 10


async def test_first_load_sends_no_conditional_headers(
    service: LocationSyncService, files: dict[Key, respx.Route]
) -> None:
    await service.refresh_due(datetime.now(UTC))

    for route in files.values():
        headers = route.calls.last.request.headers
        assert "if-none-match" not in headers and "if-modified-since" not in headers


async def test_first_load_records_the_state_of_every_file(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    before = datetime.now(UTC)
    await service.refresh_due(before)

    for rover, kind in ROVER_KINDS:
        body = fixture_bytes(rover, kind)
        state = await dataset(clean_db, rover, kind)
        assert state is not None, (rover, kind)
        assert state["content_sha256"] == hashlib.sha256(body).hexdigest()
        assert state["etag"] == files[(rover, kind)].calls.last.response.headers["ETag"]
        assert state["last_modified"] == "Wed, 28 Oct 2026 15:05:06 GMT"
        assert state["items"] == EXPECTED_ITEMS[(rover, kind)]
        assert state["dropped"] == 0
        assert state["max_sol"] == EXPECTED_MAX_SOL[(rover, kind)]
        assert state["last_error"] is None
        for column in ("checked_at", "succeeded_at", "changed_at"):
            assert state[column] is not None, column
            assert state[column] >= before - timedelta(seconds=5), column
        assert state["succeeded_at"] == state["changed_at"]


async def test_first_load_waypoint_rows_hold_the_whitelisted_values(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)

    stored = {(r["site"], r["drive"]): r for r in await waypoint_rows(clean_db, PERSEVERANCE)}
    assert len(stored) == 13
    assert stored[(3, 110)] == {
        "rover_id": PERSEVERANCE,
        "site": 3,
        "drive": 110,
        "sol": 14,
        "lat": 18.44454338,
        "lon": 77.45094675,
        "elev_geoid": -2569.861816,
        "easting": 4354497.517,
        "northing": 1093294.73,
        "roll": 0.970606993,
        "pitch": stored[(3, 110)]["pitch"],
        "yaw": stored[(3, 110)]["yaw"],
        "tilt": 1.006714395,
        "dist_m": stored[(3, 110)]["dist_m"],
        "dist_total_m": 6.255,
    }
    assert stored[(93, 0)]["sol"] == 2022
    curiosity = {(r["site"], r["drive"]): r for r in await waypoint_rows(clean_db, CURIOSITY)}
    assert (43, 216) in curiosity and (125, 2490) in curiosity
    assert curiosity[(125, 2490)]["lon"] == 137.388886


async def test_first_load_traverse_rows_hold_lon_lat_pairs_in_order(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)

    perseverance = await traverse_rows(clean_db, PERSEVERANCE)
    assert [r["seq"] for r in perseverance] == list(range(7))
    assert [r["sol"] for r in perseverance] == [14, 15, 16, 668, 670, 2009, 2022]
    assert [r["length_m"] for r in perseverance] == [6.25, 36.39, 27.43, 9.57, 7.98, 6.26, 22.06]
    raw = fixture_json(PERSEVERANCE, "traverse")["features"]
    for row, feature in zip(perseverance, raw, strict=True):
        assert row["coordinates"] == [
            [round(c[0], 7), round(c[1], 7)] for c in feature["geometry"]["coordinates"]
        ]
    curiosity = await traverse_rows(clean_db, CURIOSITY)
    assert [r["seq"] for r in curiosity] == list(range(10))
    assert [r["sol"] for r in curiosity] == [None] * 10
    assert [r["length_m"] for r in curiosity[:5]] == [935.64, None, None, None, None]
    assert [len(r["coordinates"]) for r in curiosity[:5]] == [7, 50, 278, 63, 50]


async def test_refresh_due_loads_only_known_missions(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await service.refresh_due(datetime.now(UTC))

    stored = await rows(clean_db, "SELECT DISTINCT rover_id FROM location_datasets")
    assert {r["rover_id"] for r in stored} == {PERSEVERANCE, CURIOSITY}


# --- conditional requests ----------------------------------------------------------------------


async def test_repeat_with_304_is_unchanged_keeps_rows_and_moves_succeeded_at(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    clean_db: Factory,
) -> None:
    await first_load(service)
    before = {key: await dataset(clean_db, *key) for key in ROVER_KINDS}
    rows_before = await waypoint_rows(clean_db, PERSEVERANCE)
    for route in files.values():
        route.mock(return_value=httpx.Response(304))

    results = await service.refresh(PERSEVERANCE)

    assert statuses(results) == all_status(
        "unchanged", [(PERSEVERANCE, "waypoints"), (PERSEVERANCE, "traverse")]
    )
    for kind in ("waypoints", "traverse"):
        request = files[(PERSEVERANCE, kind)].calls.last.request
        assert request.headers["if-none-match"] == before[(PERSEVERANCE, kind)]["etag"]
        assert request.headers["if-modified-since"] == "Wed, 28 Oct 2026 15:05:06 GMT"
        after = await dataset(clean_db, PERSEVERANCE, kind)
        assert after["succeeded_at"] > before[(PERSEVERANCE, kind)]["succeeded_at"]
        assert after["changed_at"] == before[(PERSEVERANCE, kind)]["changed_at"]
        assert after["content_sha256"] == before[(PERSEVERANCE, kind)]["content_sha256"]
        assert after["etag"] == before[(PERSEVERANCE, kind)]["etag"]
        assert after["last_error"] is None
    assert await waypoint_rows(clean_db, PERSEVERANCE) == rows_before
    assert files[(CURIOSITY, "waypoints")].call_count == 1  # other rover not asked


async def test_repeat_with_200_and_the_same_body_is_unchanged(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    before = await dataset(clean_db, PERSEVERANCE, "waypoints")
    rows_before = await waypoint_rows(clean_db, PERSEVERANCE)
    # A server that ignores the validators and sends the file again (new ETag, same bytes).
    files[(PERSEVERANCE, "waypoints")].mock(
        return_value=ok_response(
            fixture_bytes(PERSEVERANCE, "waypoints"),
            etag='"other"',
            modified="Thu, 29 Oct 2026 00:00:00 GMT",
        )
    )

    results = by_key(await service.refresh(PERSEVERANCE))

    assert results[(PERSEVERANCE, "waypoints")].status == "unchanged"
    after = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert after["changed_at"] == before["changed_at"]
    assert after["content_sha256"] == before["content_sha256"]
    assert after["succeeded_at"] > before["succeeded_at"]
    assert await waypoint_rows(clean_db, PERSEVERANCE) == rows_before


async def test_repeat_with_a_new_body_is_changed_and_replaces_the_rows(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    before = await dataset(clean_db, PERSEVERANCE, "waypoints")
    features = copy.deepcopy(wp_features())
    extra = copy.deepcopy(features[-1])
    extra["properties"].update({"site": 94, "drive": 0, "sol": 2025, "RMC": "94_0"})
    new_body = wp_body([*features, extra])
    files[(PERSEVERANCE, "waypoints")].mock(
        return_value=ok_response(new_body, etag='"new"', modified="Fri, 30 Oct 2026 12:00:00 GMT")
    )

    results = by_key(await service.refresh(PERSEVERANCE))

    assert results[(PERSEVERANCE, "waypoints")].status == "changed"
    assert results[(PERSEVERANCE, "waypoints")].items == 14
    after = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert after["content_sha256"] == sha256(new_body)
    assert after["etag"] == '"new"'
    assert after["last_modified"] == "Fri, 30 Oct 2026 12:00:00 GMT"
    assert after["changed_at"] > before["changed_at"]
    assert after["items"] == 14 and after["max_sol"] == 2025
    stored = await waypoint_rows(clean_db, PERSEVERANCE)
    assert len(stored) == 14 and (94, 0) in {(r["site"], r["drive"]) for r in stored}


async def test_changed_file_replaces_rows_instead_of_appending(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    # Drop a row of the middle: it must disappear (replace, not upsert); counts stay >= 90%.
    features = wp_features()
    new_body = wp_body(features[:5] + features[6:])
    set_wp_body(files, new_body)

    await service.refresh(PERSEVERANCE)

    stored = await waypoint_rows(clean_db, PERSEVERANCE)
    assert len(stored) == 12
    assert (9, 1064) not in {(r["site"], r["drive"]) for r in stored}
    # The other rover is untouched.
    assert await count(clean_db, "rover_waypoints", CURIOSITY) == 13


async def test_force_sends_no_conditional_headers_and_rewrites_the_data(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)

    results = by_key(await service.refresh(PERSEVERANCE, force=True))

    assert {r.status for r in results.values()} <= {"changed", "unchanged"}
    for kind in ("waypoints", "traverse"):
        headers = files[(PERSEVERANCE, kind)].calls.last.request.headers
        assert "if-none-match" not in headers and "if-modified-since" not in headers
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 13


async def test_refresh_ignores_the_once_a_day_rule(
    service: LocationSyncService, files: dict[Key, respx.Route]
) -> None:
    await first_load(service)

    await service.refresh(PERSEVERANCE)

    assert files[(PERSEVERANCE, "waypoints")].call_count == 2
    assert files[(PERSEVERANCE, "traverse")].call_count == 2


# --- once a day --------------------------------------------------------------------------------


async def test_refresh_due_within_a_day_of_success_makes_no_requests(
    service: LocationSyncService, files: dict[Key, respx.Route]
) -> None:
    now = await first_load(service)
    assert total_calls(files) == 4

    results = await service.refresh_due(now + timedelta(hours=1))
    assert statuses(results) == all_status("skipped")
    results = await service.refresh_due(now + timedelta(hours=23))
    assert statuses(results) == all_status("skipped")

    assert total_calls(files) == 4


async def test_refresh_due_after_a_day_asks_again_with_validators(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    now = await first_load(service)
    for route in files.values():
        route.mock(return_value=httpx.Response(304))

    results = await service.refresh_due(now + timedelta(hours=25))

    assert statuses(results) == all_status("unchanged")
    assert total_calls(files) == 8
    for route in files.values():
        assert route.calls.last.request.headers["if-none-match"]


async def test_refresh_due_honours_location_refresh_hours(
    make_service: Callable[..., LocationSyncService], files: dict[Key, respx.Route]
) -> None:
    service = make_service(location_refresh_hours=6)
    now = await first_load(service)
    for route in files.values():
        route.mock(return_value=httpx.Response(304))

    assert statuses(await service.refresh_due(now + timedelta(hours=5))) == all_status("skipped")
    assert statuses(await service.refresh_due(now + timedelta(hours=7))) == all_status("unchanged")


async def test_refresh_due_the_day_is_counted_from_the_last_successful_check_in_the_database(
    make_service: Callable[..., LocationSyncService], files: dict[Key, respx.Route]
) -> None:
    now = await first_load(make_service())

    # A "restarted" process: a new service object, same database.
    results = await make_service().refresh_due(now + timedelta(hours=2))

    assert statuses(results) == all_status("skipped")
    assert total_calls(files) == 4


async def test_refresh_due_failed_file_is_retried_in_the_next_cycle_not_waited_a_day(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    now = datetime.now(UTC)
    files[(PERSEVERANCE, "waypoints")].mock(return_value=httpx.Response(500))
    first = statuses(await service.refresh_due(now))
    assert first[(PERSEVERANCE, "waypoints")] == "failed"
    files[(PERSEVERANCE, "waypoints")].mock(
        return_value=ok_response(fixture_bytes(PERSEVERANCE, "waypoints"))
    )

    second = statuses(await service.refresh_due(now + timedelta(hours=6)))

    assert second[(PERSEVERANCE, "waypoints")] == "changed"
    assert second[(PERSEVERANCE, "traverse")] == "skipped"
    assert files[(PERSEVERANCE, "traverse")].call_count == 1


# --- acceptance rules --------------------------------------------------------------------------


async def test_shrink_over_ten_percent_is_rejected_and_old_data_stays(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    before = await dataset(clean_db, PERSEVERANCE, "waypoints")
    rows_before = await waypoint_rows(clean_db, PERSEVERANCE)
    set_wp_body(files, wp_body(wp_features()[-5:]))  # 5 < 90% of 13, max_sol unchanged

    results = by_key(await service.refresh(PERSEVERANCE))

    result = results[(PERSEVERANCE, "waypoints")]
    assert result.status == "rejected"
    assert result.error
    assert await waypoint_rows(clean_db, PERSEVERANCE) == rows_before
    after = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert after["succeeded_at"] == before["succeeded_at"]
    assert after["changed_at"] == before["changed_at"]
    assert after["content_sha256"] == before["content_sha256"]
    assert after["etag"] == before["etag"]
    assert after["items"] == 13
    assert after["last_error"]
    assert after["checked_at"] > before["checked_at"]
    # The other file of the same rover is judged on its own.
    assert results[(PERSEVERANCE, "traverse")].status == "unchanged"


async def test_shrink_exactly_at_the_ratio_is_accepted(
    make_service: Callable[..., LocationSyncService],
    files: dict[Key, respx.Route],
    clean_db: Factory,
) -> None:
    service = make_service(location_min_keep_ratio=0.5)
    await first_load(service)
    set_wp_body(files, wp_body(wp_features()[-7:]))  # 7 >= 0.5 * 13

    results = by_key(await service.refresh(PERSEVERANCE))

    assert results[(PERSEVERANCE, "waypoints")].status == "changed"
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 7


async def test_shrink_just_inside_ten_percent_is_accepted(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    set_wp_body(files, wp_body(wp_features()[1:]))  # 12 >= 0.9 * 13 = 11.7

    results = by_key(await service.refresh(PERSEVERANCE))

    assert results[(PERSEVERANCE, "waypoints")].status == "changed"
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 12


async def test_max_sol_decrease_is_rejected_and_old_data_stays(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    before = await dataset(clean_db, PERSEVERANCE, "waypoints")
    rows_before = await waypoint_rows(clean_db, PERSEVERANCE)
    older = copy.deepcopy(wp_features())
    for feature in older:
        if feature["properties"]["sol"] >= 2009:
            feature["properties"]["sol"] = 1500  # same row count, max_sol 2022 -> 1698
    set_wp_body(files, wp_body(older))

    result = by_key(await service.refresh(PERSEVERANCE))[(PERSEVERANCE, "waypoints")]

    assert result.status == "rejected"
    assert await waypoint_rows(clean_db, PERSEVERANCE) == rows_before
    after = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert (after["max_sol"], after["succeeded_at"]) == (2022, before["succeeded_at"])
    assert after["last_error"]


@pytest.mark.parametrize("what", ["shrink", "max_sol"])
async def test_force_accepts_a_file_the_guards_reject(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory, what: str
) -> None:
    await first_load(service)
    if what == "shrink":
        body = wp_body(wp_features()[-5:])
        expected_rows = 5
    else:
        older = copy.deepcopy(wp_features())
        for feature in older:
            feature["properties"]["sol"] = min(feature["properties"]["sol"], 1500)
        body = wp_body(older)
        expected_rows = 13
    set_wp_body(files, body)
    assert (
        by_key(await service.refresh(PERSEVERANCE))[(PERSEVERANCE, "waypoints")].status
        == "rejected"
    )

    result = by_key(await service.refresh(PERSEVERANCE, force=True))[(PERSEVERANCE, "waypoints")]

    assert result.status == "changed"
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == expected_rows
    after = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert after["content_sha256"] == sha256(body)
    assert after["last_error"] is None


async def test_first_load_with_less_than_half_valid_objects_is_rejected(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    features = copy.deepcopy(wp_features())
    for feature in features[:8]:  # 8 of 13 are not points: 5 valid = 38%
        feature["geometry"] = {"type": "LineString", "coordinates": [[1, 2], [3, 4]]}
    set_wp_body(files, wp_body(features))

    result = by_key(await service.refresh(PERSEVERANCE))[(PERSEVERANCE, "waypoints")]

    assert result.status == "rejected"
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 0
    state = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert state["succeeded_at"] is None and state["last_error"]


async def test_dropped_objects_are_counted_and_the_rest_is_stored(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    clean_db: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    features = copy.deepcopy(wp_features())
    del features[0]["properties"]["lat"]
    set_wp_body(files, wp_body(features))

    result = by_key(await service.refresh(PERSEVERANCE))[(PERSEVERANCE, "waypoints")]

    assert (result.status, result.items, result.dropped) == ("changed", 12, 1)
    state = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert (state["items"], state["dropped"]) == (12, 1)
    assert any(r.levelno >= logging.WARNING for r in caplog.records), "dropped objects are logged"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"<html>maintenance</html>", id="html"),
        pytest.param(b"[]", id="json-array"),
        pytest.param(
            to_body({"type": "Feature", "properties": {}, "geometry": None}), id="feature"
        ),
    ],
)
async def test_a_document_that_is_not_a_feature_collection_never_replaces_good_data(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    clean_db: Factory,
    body: bytes,
) -> None:
    await first_load(service)
    before = await dataset(clean_db, PERSEVERANCE, "waypoints")
    rows_before = await waypoint_rows(clean_db, PERSEVERANCE)
    set_wp_body(files, body)

    result = by_key(await service.refresh(PERSEVERANCE))[(PERSEVERANCE, "waypoints")]

    assert result.status == "rejected"
    assert result.error
    assert await waypoint_rows(clean_db, PERSEVERANCE) == rows_before
    after = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert after["succeeded_at"] == before["succeeded_at"]
    assert after["content_sha256"] == before["content_sha256"]
    assert after["last_error"]


async def test_traverse_shrink_is_guarded_like_waypoints(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    raw = fixture_json(PERSEVERANCE, "traverse")
    base = {k: v for k, v in raw.items() if k != "features"}
    files[(PERSEVERANCE, "traverse")].mock(
        return_value=ok_response(feature_collection(raw["features"][-3:], base))
    )

    result = by_key(await service.refresh(PERSEVERANCE))[(PERSEVERANCE, "traverse")]

    assert result.status == "rejected"
    assert await count(clean_db, "rover_traverse_segments", PERSEVERANCE) == 7


async def test_rejected_then_good_file_clears_last_error(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)
    set_wp_body(files, wp_body(wp_features()[-5:]))
    await service.refresh(PERSEVERANCE)
    assert (await dataset(clean_db, PERSEVERANCE, "waypoints"))["last_error"]
    set_wp_body(files, fixture_bytes(PERSEVERANCE, "waypoints"))

    result = by_key(await service.refresh(PERSEVERANCE))[(PERSEVERANCE, "waypoints")]

    assert result.status in {"changed", "unchanged"}
    assert (await dataset(clean_db, PERSEVERANCE, "waypoints"))["last_error"] is None


# --- failures ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.Response(500), id="http-500"),
        pytest.param(httpx.Response(404), id="http-404"),
        pytest.param(httpx.ReadTimeout("slow"), id="timeout"),
        pytest.param(httpx.ConnectError("refused"), id="connect-error"),
    ],
)
async def test_failed_request_is_recorded_without_raising_and_other_files_still_load(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    clean_db: Factory,
    failure: Any,
) -> None:
    if isinstance(failure, httpx.Response):
        files[(PERSEVERANCE, "waypoints")].mock(return_value=failure)
    else:
        files[(PERSEVERANCE, "waypoints")].mock(side_effect=failure)

    results = by_key(await service.refresh_due(datetime.now(UTC)))

    assert results[(PERSEVERANCE, "waypoints")].status == "failed"
    assert results[(PERSEVERANCE, "waypoints")].error
    for key in ROVER_KINDS:
        if key != (PERSEVERANCE, "waypoints"):
            assert results[key].status == "changed", key
    assert await count(clean_db, "rover_waypoints", PERSEVERANCE) == 0
    assert await count(clean_db, "rover_traverse_segments", PERSEVERANCE) == 7
    assert await count(clean_db, "rover_waypoints", CURIOSITY) == 13
    state = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert state is not None
    assert state["checked_at"] is not None
    assert state["succeeded_at"] is None and state["changed_at"] is None
    assert state["last_error"]


async def test_failure_after_a_good_load_keeps_data_and_the_old_success_time(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    now = await first_load(service)
    before = await dataset(clean_db, PERSEVERANCE, "waypoints")
    rows_before = await waypoint_rows(clean_db, PERSEVERANCE)
    files[(PERSEVERANCE, "waypoints")].mock(return_value=httpx.Response(503))

    results = by_key(await service.refresh_due(now + timedelta(hours=25)))

    assert results[(PERSEVERANCE, "waypoints")].status == "failed"
    after = await dataset(clean_db, PERSEVERANCE, "waypoints")
    assert after["succeeded_at"] == before["succeeded_at"]
    assert after["changed_at"] == before["changed_at"]
    assert after["checked_at"] > before["checked_at"]
    assert after["last_error"]
    assert await waypoint_rows(clean_db, PERSEVERANCE) == rows_before


async def test_failed_file_is_logged_as_a_warning(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    files[(CURIOSITY, "traverse")].mock(return_value=httpx.Response(500))

    await service.refresh_due(datetime.now(UTC))

    assert any(r.levelno >= logging.WARNING for r in caplog.records)


async def test_body_over_location_max_bytes_fails_and_changes_nothing(
    make_service: Callable[..., LocationSyncService],
    files: dict[Key, respx.Route],
    clean_db: Factory,
) -> None:
    service = make_service(location_max_bytes=2048)  # the fixtures are larger

    results = by_key(await service.refresh_due(datetime.now(UTC)))

    assert results[(PERSEVERANCE, "waypoints")].status == "failed"
    assert results[(CURIOSITY, "traverse")].status == "failed"
    assert await count(clean_db, "rover_waypoints") == 0
    assert await count(clean_db, "rover_traverse_segments") == 0


async def test_database_error_during_replacement_leaves_the_old_rows_intact(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic replacement: DELETE + INSERT run, then a DB error rolls everything back."""
    from sqlalchemy.exc import OperationalError

    from app.repositories import locations as repo
    from app.services import location_sync as sync_module

    await first_load(service)
    rows_before = await waypoint_rows(clean_db, PERSEVERANCE)
    original = repo.replace_waypoints

    async def failing(session: Any, rover_id: str, items: Any) -> Any:
        await original(session, rover_id, items)
        raise OperationalError("INSERT", {}, Exception("connection lost"))

    monkeypatch.setattr(repo, "replace_waypoints", failing)
    for name in dir(sync_module):  # the service may hold the function or the module
        if getattr(sync_module, name, None) is original:
            monkeypatch.setattr(sync_module, name, failing)
    features = copy.deepcopy(wp_features())
    features[-1]["properties"]["sol"] = 2100  # new content, so a replacement is attempted
    set_wp_body(files, wp_body(features))

    results = by_key(await service.refresh(PERSEVERANCE))

    assert results[(PERSEVERANCE, "waypoints")].status == "failed"
    assert await waypoint_rows(clean_db, PERSEVERANCE) == rows_before


# --- lock --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "offset"), [("waypoints", 0), ("traverse", 1)])
async def test_busy_advisory_lock_skips_that_file_only(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    clean_db: Factory,
    engine: AsyncEngine,
    kind: str,
    offset: int,
) -> None:
    key = await legacy_id(clean_db, PERSEVERANCE) * 2 + offset
    async with engine.connect() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": LOCATION_LOCK_NAMESPACE, "key": key},
        )

        results = by_key(await service.refresh_due(datetime.now(UTC)))

    expected = all_status("changed")
    expected[(PERSEVERANCE, kind)] = "skipped"
    assert {k: r.status for k, r in results.items()} == expected
    table = "rover_waypoints" if kind == "waypoints" else "rover_traverse_segments"
    assert await count(clean_db, table, PERSEVERANCE) == 0


async def test_lock_is_released_after_the_run(
    service: LocationSyncService, files: dict[Key, respx.Route], clean_db: Factory
) -> None:
    await first_load(service)

    holders = await rows(
        clean_db,
        "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND granted "
        "AND classid = :ns "
        "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())",
        ns=LOCATION_LOCK_NAMESPACE,
    )
    assert holders[0]["n"] == 0
    # ...and a second run can take it again.
    assert statuses(await service.refresh(PERSEVERANCE)) == {
        (PERSEVERANCE, "waypoints"): "unchanged",
        (PERSEVERANCE, "traverse"): "unchanged",
    }


async def test_location_lock_does_not_block_photo_sync_locks(
    service: LocationSyncService,
    files: dict[Key, respx.Route],
    engine: AsyncEngine,
    clean_db: Factory,
) -> None:
    from app.services.sync import SYNC_LOCK_NAMESPACE

    assert LOCATION_LOCK_NAMESPACE != SYNC_LOCK_NAMESPACE
    async with engine.connect() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": SYNC_LOCK_NAMESPACE, "key": await legacy_id(clean_db, PERSEVERANCE)},
        )

        assert statuses(await service.refresh_due(datetime.now(UTC))) == all_status("changed")


# --- scheduler hook ----------------------------------------------------------------------------


@pytest.fixture
def photo_sync_fakes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Replace the photo adapters with in-memory ones; returns the order of events."""
    events: list[Any] = []

    def build_adapter(rover_id: str, client: Any, _settings: Any) -> FakeAdapter:
        events.append(("photos", rover_id))
        return FakeAdapter(rover_id, latest=0)

    monkeypatch.setattr(scheduler_module, "build_adapter", build_adapter)
    return events


async def run_job_once(app: Any) -> list[Any]:
    """Start the lifespan (the scheduler runs once at start); the results of that run_once."""
    finished: asyncio.Future[list[Any]] = asyncio.get_running_loop().create_future()
    original = SyncScheduler.run_once

    async def wrapped(self: SyncScheduler) -> list[Any]:
        try:
            value = await original(self)
        except BaseException as exc:
            if not finished.done():
                finished.set_exception(exc)
            raise
        if not finished.done():
            finished.set_result(value)
        return value

    SyncScheduler.run_once = wrapped  # type: ignore[method-assign]
    try:
        async with app.router.lifespan_context(app):
            return await asyncio.wait_for(finished, timeout=15)
    finally:
        SyncScheduler.run_once = original  # type: ignore[method-assign]


async def test_run_once_refreshes_locations_after_the_photo_sync(
    settings: Settings,
    photo_sync_fakes: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh_due(self: LocationSyncService, now: datetime | None = None) -> list[Any]:
        photo_sync_fakes.append("locations")
        return []

    monkeypatch.setattr(LocationSyncService, "refresh_due", refresh_due)
    app = create_app(settings.model_copy(update={"sync_enabled": True}))

    results = await run_job_once(app)

    assert [r.rover_id for r in results] == ["perseverance", "curiosity"]
    assert photo_sync_fakes == [("photos", "perseverance"), ("photos", "curiosity"), "locations"]


async def test_run_once_survives_a_crash_of_the_location_refresh(
    settings: Settings,
    photo_sync_fakes: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh_due(self: LocationSyncService, now: datetime | None = None) -> list[Any]:
        photo_sync_fakes.append("locations")
        raise RuntimeError("location source exploded")

    monkeypatch.setattr(LocationSyncService, "refresh_due", refresh_due)
    app = create_app(settings.model_copy(update={"sync_enabled": True}))

    results = await run_job_once(app)

    assert [r.rover_id for r in results] == ["perseverance", "curiosity"]
    assert [r.status for r in results] == ["success", "success"]
    assert photo_sync_fakes[-1] == "locations"


async def test_run_once_makes_no_location_requests_when_location_sync_is_disabled(
    settings: Settings,
    photo_sync_fakes: list[Any],
    files: dict[Key, respx.Route],
    clean_db: Factory,
) -> None:
    app = create_app(
        settings.model_copy(
            update={
                "sync_enabled": True,
                "location_sync_enabled": False,
                "location_base_url": BASE_URL,
            }
        )
    )

    results = await run_job_once(app)

    assert len(results) == 2
    assert total_calls(files) == 0
    assert await count(clean_db, "location_datasets") == 0
    assert await count(clean_db, "rover_waypoints") == 0


async def test_a_failing_location_source_does_not_stop_the_photo_sync(
    settings: Settings,
    photo_sync_fakes: list[Any],
    clean_db: Factory,
    upstream_router: respx.MockRouter,
) -> None:
    """End to end: real LocationSyncService, every MMGIS file answers 500."""
    routes = [upstream_router.get(file_url(r, k)).respond(500) for r, k in ROVER_KINDS]
    app = create_app(
        settings.model_copy(
            update={
                "sync_enabled": True,
                "upstream_max_retries": 0,
                "upstream_max_rps": 0,
                "location_base_url": BASE_URL,
            }
        )
    )

    results = await run_job_once(app)

    assert [r.status for r in results] == ["success", "success"]
    assert sum(route.call_count for route in routes) == 4
    states = await rows(clean_db, "SELECT kind, last_error FROM location_datasets")
    assert len(states) == 4 and all(s["last_error"] for s in states)
    assert await photo_count(clean_db) == 0  # fake adapters had no photos; nothing crashed
