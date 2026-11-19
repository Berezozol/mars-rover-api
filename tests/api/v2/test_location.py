"""GET /api/v2/rovers/{rover}/location (docs/API.md, Waypoint; stage 11).

Own committed data per test (`clean_db`): waypoints of Perseverance and Curiosity inserted in
scrambled order. Covered: response shape and key order, sort `sol, site, drive`, filters,
422 for bad or conflicting parameters, 404, `available: false` for Spirit/Opportunity, the
state before the first load, and the full path sync -> API on the recorded MMGIS files.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.services.location_sync import LocationSyncService
from app.sources.http import UpstreamClient
from tests._locations import BASE_URL, Factory, fixture_json, insert_dataset, mock_files
from tests.api.v2._locations_data import (
    CURIOSITY_UPDATED,
    CURIOSITY_WAYPOINTS,
    PERSEVERANCE_ORDER,
    PERSEVERANCE_UPDATED,
    PERSEVERANCE_WAYPOINTS,
    expected_waypoints,
    location_response,
    seed_locations,
)
from tests.api.v2.conftest import (
    HUGE,
    INT32_MAX,
    V2,
    assert_error,
    assert_json,
    assert_validation_error,
)

pytestmark = pytest.mark.api

P = "perseverance"


@pytest.fixture
async def seeded(clean_db: Factory) -> Factory:
    await seed_locations(clean_db)
    return clean_db


def perseverance_expected(order: list[tuple[int, int, int]]) -> dict[str, Any]:
    return location_response(
        P,
        expected_waypoints(PERSEVERANCE_WAYPOINTS, order),
        updated_at=PERSEVERANCE_UPDATED,
        source_max_sol=30,
    )


# --- shape and order ---------------------------------------------------------------------------


async def test_location_returns_exact_json_sorted_by_sol_site_drive(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    response = await client.get(f"{V2}/rovers/{P}/location")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["rover", "available", "updated_at", "source_max_sol", "items"]
    assert_json(body, perseverance_expected(PERSEVERANCE_ORDER))
    assert [(i["sol"], i["site"], i["drive"]) for i in body["items"]] == PERSEVERANCE_ORDER


async def test_location_item_has_all_waypoint_keys_and_nulls_for_missing_values(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    items = (await client.get(f"{V2}/rovers/{P}/location")).json()["items"]

    full, bare = items[0], items[2]
    assert list(full) == [
        "sol",
        "site",
        "drive",
        "lat",
        "lon",
        "elev_geoid",
        "easting",
        "northing",
        "roll",
        "pitch",
        "yaw",
        "tilt",
        "dist_m",
        "dist_total_m",
    ]
    assert full == {
        "sol": 5,
        "site": 2,
        "drive": 0,
        "lat": 18.1,
        "lon": 77.1,
        "elev_geoid": -2500.0,
        "easting": 4086431.2,
        "northing": 1092178.4,
        "roll": 1.2,
        "pitch": -3.4,
        "yaw": 142.0,
        "tilt": 3.6,
        "dist_m": 112.4,
        "dist_total_m": 33874.9,
    }
    assert list(bare) == list(full)
    assert [bare[key] for key in list(bare)[5:]] == [None] * 9
    assert all(isinstance(full[key], int) for key in ("sol", "site", "drive"))


async def test_location_updated_at_has_no_fraction_and_z_suffix(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    body = (await client.get(f"{V2}/rovers/{P}/location")).json()

    assert body["updated_at"] == "2026-10-28T15:05:06Z"
    assert body["source_max_sol"] == 30


async def test_location_is_scoped_to_the_rover(client: httpx.AsyncClient, seeded: Factory) -> None:
    body = (await client.get(f"{V2}/rovers/curiosity/location")).json()

    order = [(7, 1, 0), (8, 1, 70)]
    assert_json(
        body,
        location_response(
            "curiosity",
            expected_waypoints(CURIOSITY_WAYPOINTS, order),
            updated_at=CURIOSITY_UPDATED,
            source_max_sol=8,
        ),
    )


async def test_location_rover_slug_is_case_insensitive(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    response = await client.get(f"{V2}/rovers/Perseverance/location")

    assert response.status_code == 200
    assert response.json()["rover"] == P


async def test_location_has_no_pagination(client: httpx.AsyncClient, seeded: Factory) -> None:
    body = (await client.get(f"{V2}/rovers/{P}/location", params={"page": 2, "per_page": 1})).json()

    assert len(body["items"]) == len(PERSEVERANCE_ORDER)
    assert "total" not in body and "page" not in body


# --- filters -----------------------------------------------------------------------------------

ALL = PERSEVERANCE_ORDER


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({"sol": "10"}, [k for k in ALL if k[0] == 10], id="sol"),
        pytest.param({"sol": "5"}, [(5, 2, 0)], id="sol-single"),
        pytest.param({"sol": "11"}, [], id="sol-empty"),
        pytest.param({"sol": "0"}, [], id="sol-zero"),
        pytest.param({"from_sol": "10"}, [k for k in ALL if k[0] >= 10], id="from"),
        pytest.param({"to_sol": "10"}, [k for k in ALL if k[0] <= 10], id="to"),
        pytest.param(
            {"from_sol": "10", "to_sol": "20"}, [k for k in ALL if 10 <= k[0] <= 20], id="range"
        ),
        pytest.param({"from_sol": "20", "to_sol": "20"}, [(20, 5, 0)], id="range-single-sol"),
        pytest.param({"from_sol": "0", "to_sol": "0"}, [], id="range-zero"),
        pytest.param({"from_sol": "31"}, [], id="from-after-last"),
        pytest.param({"from_sol": "0"}, ALL, id="from-zero"),
        pytest.param({"to_sol": str(INT32_MAX)}, ALL, id="to-int32-max"),
        pytest.param({"from_sol": "6", "to_sol": "9"}, [], id="range-between-sols"),
    ],
)
async def test_location_filters_by_sol_and_range_inclusive(
    client: httpx.AsyncClient, seeded: Factory, params: dict[str, str], expected: list
) -> None:
    response = await client.get(f"{V2}/rovers/{P}/location", params=params)

    assert response.status_code == 200
    assert_json(response.json(), perseverance_expected(expected))


async def test_location_empty_filter_result_keeps_the_rover_state(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    body = (await client.get(f"{V2}/rovers/{P}/location", params={"sol": "999"})).json()

    assert body["items"] == []
    assert body["available"] is True
    assert body["updated_at"] == PERSEVERANCE_UPDATED
    assert body["source_max_sol"] == 30


# --- 422 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"sol": "10", "from_sol": "5"}, id="sol-with-from"),
        pytest.param({"sol": "10", "to_sol": "20"}, id="sol-with-to"),
        pytest.param({"sol": "10", "from_sol": "5", "to_sol": "20"}, id="sol-with-both"),
        pytest.param({"from_sol": "20", "to_sol": "10"}, id="from-greater-than-to"),
        pytest.param({"sol": "-1"}, id="sol-negative"),
        pytest.param({"from_sol": "-1"}, id="from-negative"),
        pytest.param({"to_sol": "-1"}, id="to-negative"),
        pytest.param({"sol": "abc"}, id="sol-word"),
        pytest.param({"from_sol": "1.5"}, id="from-float"),
        pytest.param({"to_sol": "1e3"}, id="to-exponent"),
        pytest.param({"sol": str(INT32_MAX + 1)}, id="sol-over-int32"),
        pytest.param({"from_sol": str(INT32_MAX + 1)}, id="from-over-int32"),
        pytest.param({"to_sol": HUGE}, id="to-huge"),
        pytest.param({"sol": ""}, id="sol-empty"),
    ],
)
async def test_location_invalid_or_conflicting_parameters_return_422(
    client: httpx.AsyncClient, seeded: Factory, params: dict[str, str]
) -> None:
    response = await client.get(f"{V2}/rovers/{P}/location", params=params)

    assert_validation_error(response, "sol", "from_sol", "to_sol")


async def test_location_bound_at_int32_max_is_valid(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    response = await client.get(f"{V2}/rovers/{P}/location", params={"sol": str(INT32_MAX)})

    assert response.status_code == 200
    assert response.json()["items"] == []


# --- 404 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    ["pluto", "perseverance2", "mars-2020", "%D0%BC%D0%B0%D1%80%D1%81", "%00"],
    ids=["unknown", "digit", "dash", "cyrillic", "nul"],
)
async def test_location_unknown_rover_returns_404_rover_not_found(
    client: httpx.AsyncClient, seeded: Factory, slug: str
) -> None:
    response = await client.get(f"{V2}/rovers/{slug}/location")

    assert_error(response, 404, "rover_not_found")


# --- rovers without a source and the state before the first load -------------------------------


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_location_mer_rovers_are_available_false_and_empty(
    client: httpx.AsyncClient, seeded: Factory, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/location")

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "rover": rover,
            "available": False,
            "updated_at": None,
            "source_max_sol": None,
            "items": [],
        },
    )


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_location_mer_rovers_with_filters_stay_empty(
    client: httpx.AsyncClient, seeded: Factory, rover: str
) -> None:
    body = (await client.get(f"{V2}/rovers/{rover}/location", params={"sol": "10"})).json()

    assert (body["available"], body["items"]) == (False, [])


@pytest.mark.parametrize("rover", ["perseverance", "curiosity"])
async def test_location_before_the_first_load_is_available_true_and_empty(
    client: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/location")

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "rover": rover,
            "available": True,
            "updated_at": None,
            "source_max_sol": None,
            "items": [],
        },
    )


async def test_location_failed_first_attempt_is_still_empty_with_null_state(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await insert_dataset(
        clean_db, P, "waypoints", checked_at=datetime(2026, 11, 18, tzinfo=UTC), last_error="boom"
    )

    body = (await client.get(f"{V2}/rovers/{P}/location")).json()

    assert (body["available"], body["items"]) == (True, [])
    assert body["updated_at"] is None and body["source_max_sol"] is None


async def test_location_reflects_data_written_after_the_first_response(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    assert (await client.get(f"{V2}/rovers/{P}/location")).json()["items"] == []
    await seed_locations(clean_db)

    body = (await client.get(f"{V2}/rovers/{P}/location")).json()

    assert len(body["items"]) == 7


# --- sync -> API on the recorded files ---------------------------------------------------------


@pytest.fixture
async def synced(
    clean_db: Factory, settings: Settings, migrated_db: str, upstream_router: respx.MockRouter
) -> AsyncIterator[None]:
    mock_files(upstream_router)
    values = settings.model_copy(
        update={
            "location_base_url": BASE_URL,
            "upstream_max_rps": 0,
            "upstream_max_retries": 0,
        }
    )
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    client = UpstreamClient(values)
    try:
        results = await LocationSyncService(engine, client, values).refresh_due(datetime.now(UTC))
        assert {r.status for r in results} == {"changed"}
        yield
    finally:
        await client.aclose()
        await engine.dispose()


async def test_location_after_a_sync_serves_the_recorded_waypoints_up_to_the_last_sol(
    client: httpx.AsyncClient, synced: None
) -> None:
    raw = fixture_json(P, "waypoints")["features"]
    body = (await client.get(f"{V2}/rovers/{P}/location")).json()

    assert body["available"] is True
    assert body["source_max_sol"] == 2022
    assert body["updated_at"] is not None
    expected = sorted(
        (f["properties"]["sol"], f["properties"]["site"], f["properties"]["drive"]) for f in raw
    )
    assert [(i["sol"], i["site"], i["drive"]) for i in body["items"]] == expected
    last = body["items"][-1]
    assert (last["site"], last["drive"], last["sol"]) == (93, 0, 2022)
    assert last["lat"] == 18.43559434 and last["lon"] == 77.23322287
    assert last["elev_geoid"] == -1936.366943
    assert list(last) == [
        "sol",
        "site",
        "drive",
        "lat",
        "lon",
        "elev_geoid",
        "easting",
        "northing",
        "roll",
        "pitch",
        "yaw",
        "tilt",
        "dist_m",
        "dist_total_m",
    ]


async def test_location_after_a_sync_filters_the_stops_of_one_sol(
    client: httpx.AsyncClient, synced: None
) -> None:
    body = (await client.get(f"{V2}/rovers/{P}/location", params={"sol": "340"})).json()

    assert [(i["site"], i["drive"]) for i in body["items"]] == [(9, 364), (9, 1064), (9, 1554)]


async def test_location_after_a_sync_serves_curiosity_up_to_sol_5073(
    client: httpx.AsyncClient, synced: None
) -> None:
    body = (await client.get(f"{V2}/rovers/curiosity/location")).json()

    assert body["source_max_sol"] == 5073
    assert (body["items"][-1]["site"], body["items"][-1]["drive"]) == (125, 2490)
    assert 43 in {i["site"] for i in body["items"]}
