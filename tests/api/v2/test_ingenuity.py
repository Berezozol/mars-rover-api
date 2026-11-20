"""Ingenuity in API v2 (docs/API.md; stage 12): the rover, its photos, the flight log.

One dataset is committed once per module (the module must not use `clean_db`). Ingenuity photos:
sol 58 (flight 1; three photos of all kinds), sol 133 (flight 9; one photo) and sol 1069 (no
flight; two photos). Perseverance has a photo on sol 58 too: its `flight` must stay null.
The expected flight values come from the NASA log (tests/_ingenuity.py).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests._ingenuity import FLIGHTS
from tests.api.v2.conftest import (
    CAMERA_KEYS,
    INT32_MAX,
    LATEST_KEYS,
    MANIFEST_KEYS,
    MANIFEST_SOL_KEYS,
    PHOTO_DETAIL_KEYS,
    PHOTO_KEYS,
    ROVER_KEYS,
    ROVER_STATIC,
    V2,
    assert_error,
    assert_json,
    assert_validation_error,
)
from tests.conftest import _clean_database, build_record

pytestmark = pytest.mark.api

ING = f"{V2}/rovers/ingenuity"
FLIGHT_KEYS = [
    "flight",
    "sol",
    "date",
    "distance_m",
    "max_altitude_m",
    "max_groundspeed_mps",
    "duration_s",
    "from",
    "to",
    "total_photos",
]
HELI_CREDIT = "NASA/JPL-Caltech"
# Photos per flight sol over all sample types; every other flight has none.
PHOTOS_BY_SOL = {58: 3, 133: 1}

NAV_EXTRA = {
    "extended": {"dimension": "(640,480)"},
    "date_taken_mars": "Sol-00058M11:41:52.53212",
}
RTE_EXTRA = {
    "extended": {"dimension": "(4208,3120)"},
    "date_taken_mars": "Sol-00058M11:41:30.14414",
}


def _heli(source_id: str, sol: int, camera: str, minute: int, **overrides: Any) -> Any:
    taken_at = (
        datetime(2021, 4, 19, 8, minute, tzinfo=UTC)
        if sol == 58
        else datetime(2024, 2, 22, 1, minute, tzinfo=UTC)
    )
    values: dict[str, Any] = {
        "source_id": source_id,
        "sol": sol,
        "camera_code": camera,
        "taken_at": taken_at,
        "earth_date": taken_at.date(),
        "title": f"Mars Helicopter Sol {sol}",
        "credit": HELI_CREDIT,
        "site": None,
        "drive": None,
        "source_link": f"https://mars.nasa.gov/mars2020/multimedia/raw-images/{source_id}",
        "img_small": f"https://images.example.test/{source_id}_320.jpg",
        "img_medium": f"https://images.example.test/{source_id}_800.jpg",
        "img_large": f"https://images.example.test/{source_id}_1200.jpg",
    }
    values.update(overrides)
    return build_record(**values)


INGENUITY = [
    _heli("h58-nav-full", 58, "HELI_NAV", 1, extra=NAV_EXTRA),
    _heli("h58-nav-sub", 58, "HELI_NAV", 2, sample_type="subframe"),
    _heli("h58-rte", 58, "HELI_RTE", 3, extra=RTE_EXTRA),
    _heli("h133-nav", 133, "HELI_NAV", 1),
    _heli("h1069-nav", 1069, "HELI_NAV", 1),
    _heli("h1069-rte", 1069, "HELI_RTE", 2),
]
PERSEVERANCE_SOL_58 = build_record(
    source_id="p58",
    sol=58,
    camera_code="NAVCAM_LEFT",
    taken_at=datetime(2021, 4, 19, 9, 0, tzinfo=UTC),
    earth_date=datetime(2021, 4, 19).date(),
    site=14,
    drive=10,
)


async def _seed(factory: async_sessionmaker[AsyncSession], rover: str, records: list) -> None:
    async with factory() as session:
        for record in records:
            await upsert_photos(session, rover, [record])
        await recompute_sol_summaries(session, rover, {r.sol for r in records})
        await refresh_rover_stats(session, rover)
        await session.commit()


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def ingenuity_world(migrated_db: str) -> AsyncIterator[dict[str, int]]:
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _clean_database(factory)
        await _seed(factory, "ingenuity", INGENUITY)
        await _seed(factory, "perseverance", [PERSEVERANCE_SOL_58])
        async with factory() as session:
            rows = await session.execute(text("SELECT source_id, id FROM photos"))
            yield {source_id: photo_id for source_id, photo_id in rows}
    finally:
        try:
            await _clean_database(factory)
        finally:
            await engine.dispose()


@pytest.fixture
def ids(ingenuity_world: dict[str, int]) -> dict[str, int]:
    return ingenuity_world


def expected_flight(row: tuple[Any, ...]) -> dict[str, Any]:
    n, sol, day, distance, altitude, speed, duration, origin, destination = row
    return {
        "flight": n,
        "sol": sol,
        "date": day,
        "distance_m": distance,
        "max_altitude_m": altitude,
        "max_groundspeed_mps": float(speed),
        "duration_s": float(duration),
        "from": origin,
        "to": destination,
        "total_photos": PHOTOS_BY_SOL.get(sol, 0),
    }


def is_json_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


# --- the rover ------------------------------------------------------------------------------------


async def test_rovers_list_contains_ingenuity_between_curiosity_and_opportunity(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"{V2}/rovers")).json()

    assert [r["id"] for r in body["items"]] == [
        "curiosity",
        "ingenuity",
        "opportunity",
        "perseverance",
        "spirit",
    ]


async def test_rover_ingenuity_returns_static_fields_counters_and_archive_source(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(ING)

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ROVER_KEYS
    assert_json({k: body[k] for k in ROVER_STATIC["ingenuity"]}, ROVER_STATIC["ingenuity"])
    assert body["end_date"] == "2024-01-25"
    assert body["status"] == "complete"
    assert body["data_source"] == "archive"
    assert (body["max_sol"], body["max_date"], body["total_photos"]) == (1069, "2024-02-22", 6)
    assert body["cameras_count"] == 2


async def test_rover_ingenuity_slug_is_case_insensitive(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{V2}/rovers/INGENUITY")).json()

    assert body["id"] == "ingenuity"


async def test_ingenuity_cameras_list_the_two_helicopter_cameras_with_counts(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"{ING}/cameras")

    assert response.status_code == 200
    items = response.json()["items"]
    assert all(list(item) == CAMERA_KEYS for item in items)
    assert [(c["code"], c["name"], c["total_photos"]) for c in items] == [
        ("HELI_NAV", "Navigation Camera", 4),
        ("HELI_RTE", "Color Camera", 2),
    ]


async def test_ingenuity_photos_of_a_flight_sol_list_in_contract_order_with_frame_size(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    response = await client.get(f"{ING}/photos", params={"sol": 58})

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["nearest_sols"]) == (3, None)
    # taken_at DESC: rte (03), nav-sub (02), nav-full (01)
    assert [i["source"]["id"] for i in body["items"]] == ["h58-rte", "h58-nav-sub", "h58-nav-full"]
    rte, sub, full = body["items"]
    assert list(rte) == PHOTO_KEYS
    assert (rte["rover"], rte["camera"]) == (
        "ingenuity",
        {"code": "HELI_RTE", "name": "Color Camera"},
    )
    assert (rte["width"], rte["height"]) == (4208, 3120)
    assert (full["width"], full["height"]) == (640, 480)
    assert (sub["width"], sub["height"]) == (None, None)
    assert full["credit"] == HELI_CREDIT
    assert full["images"] == {
        "small": "https://images.example.test/h58-nav-full_320.jpg",
        "medium": "https://images.example.test/h58-nav-full_800.jpg",
        "large": "https://images.example.test/h58-nav-full_1200.jpg",
        "full": full["img_src"],
    }
    assert [i["id"] for i in body["items"]] == [
        ids["h58-rte"],
        ids["h58-nav-sub"],
        ids["h58-nav-full"],
    ]


async def test_ingenuity_camera_filter_accepts_helicopter_codes_and_rejects_others(
    client: httpx.AsyncClient,
) -> None:
    ok = await client.get(f"{ING}/photos", params={"camera": "heli_rte"})
    bad = await client.get(f"{ING}/photos", params={"camera": "NAVCAM_LEFT"})

    assert [i["source"]["id"] for i in ok.json()["items"]] == ["h1069-rte", "h58-rte"]
    details = assert_validation_error(bad, "camera")
    assert "HELI_NAV" in str(details) and "HELI_RTE" in str(details)


async def test_ingenuity_latest_returns_the_last_sol_with_photos(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"{ING}/photos/latest")).json()

    assert list(body) == LATEST_KEYS
    assert body["sol"] == 1069
    assert [i["source"]["id"] for i in body["items"]] == ["h1069-rte", "h1069-nav"]
    assert body["total"] == 2


async def test_ingenuity_manifest_lists_sols_newest_first_with_cameras(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(f"{ING}/manifest")).json()

    assert list(body) == MANIFEST_KEYS
    assert body["rover"]["id"] == "ingenuity"
    assert all(list(item) == MANIFEST_SOL_KEYS for item in body["sols"])
    assert [(s["sol"], s["total_photos"], s["cameras"]) for s in body["sols"]] == [
        (1069, 2, ["HELI_NAV", "HELI_RTE"]),
        (133, 1, ["HELI_NAV"]),
        (58, 3, ["HELI_NAV", "HELI_RTE"]),
    ]
    assert body["sols"][2]["earth_date"] == "2021-04-19"


# --- photo card -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "flight"),
    [
        pytest.param("h58-nav-full", 1, id="flight-1-sol-58"),
        pytest.param("h58-rte", 1, id="same-sol-other-camera"),
        pytest.param("h58-nav-sub", 1, id="subframe-too"),
        pytest.param("h133-nav", 9, id="flight-9-sol-133"),
        pytest.param("h1069-nav", None, id="sol-without-flight"),
    ],
)
async def test_ingenuity_photo_card_flight_follows_the_sol(
    client: httpx.AsyncClient, ids: dict[str, int], source_id: str, flight: int | None
) -> None:
    response = await client.get(f"{V2}/photos/{ids[source_id]}")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == PHOTO_DETAIL_KEYS
    assert list(body)[-2:] == ["flight", "samples"]  # `samples` (stage 13) is last
    assert body["flight"] == flight
    assert body["rover"] == "ingenuity"
    assert (body["site"], body["drive"], body["location"]) == (None, None, None)


async def test_ingenuity_photo_card_has_local_time_and_frame_size(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    body = (await client.get(f"{V2}/photos/{ids['h58-nav-full']}")).json()

    assert body["local_time"] == {"time": "11:41:52", "kind": "lmst"}
    assert (body["width"], body["height"]) == (640, 480)
    assert body["in_sol"] == {
        "position": 3,
        "total": 3,
        "newer_id": ids["h58-nav-sub"],
        "older_id": None,
    }


async def test_perseverance_photo_on_a_flight_sol_has_null_flight(
    client: httpx.AsyncClient, ids: dict[str, int]
) -> None:
    body = (await client.get(f"{V2}/photos/{ids['p58']}")).json()

    assert body["rover"] == "perseverance"
    assert body["sol"] == 58
    assert body["flight"] is None


# --- flights --------------------------------------------------------------------------------------


async def test_flights_list_returns_72_flights_in_order_with_exact_values(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"{ING}/flights")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items"]
    assert [f["flight"] for f in body["items"]] == list(range(1, 73))
    assert_json(body, {"items": [expected_flight(row) for row in FLIGHTS]})


async def test_flights_list_items_have_the_contract_keys_and_types(
    client: httpx.AsyncClient,
) -> None:
    items = (await client.get(f"{ING}/flights")).json()["items"]

    for item in items:
        assert list(item) == FLIGHT_KEYS
        assert isinstance(item["flight"], int) and isinstance(item["sol"], int)
        assert isinstance(item["date"], str) and len(item["date"]) == 10
        for key in ("distance_m", "max_altitude_m", "max_groundspeed_mps", "duration_s"):
            assert is_json_number(item[key]), (item["flight"], key, item[key])
        assert isinstance(item["from"], str) and item["from"]
        assert item["to"] is None or isinstance(item["to"], str)
        assert isinstance(item["total_photos"], int)


async def test_flights_list_first_and_last_flight_match_the_log(
    client: httpx.AsyncClient,
) -> None:
    items = (await client.get(f"{ING}/flights")).json()["items"]

    assert items[0] == {
        "flight": 1,
        "sol": 58,
        "date": "2021-04-19",
        "distance_m": 0,
        "max_altitude_m": 3,
        "max_groundspeed_mps": 0,
        "duration_s": 39.1,
        "from": "Wright Brothers Field",
        "to": None,
        "total_photos": 3,
    }
    assert items[-1] == {
        "flight": 72,
        "sol": 1035,
        "date": "2024-01-18",
        "distance_m": 0,
        "max_altitude_m": 12,
        "max_groundspeed_mps": 0,
        "duration_s": 32.0,
        "from": "Airfield Chi",
        "to": None,
        "total_photos": 0,
    }


async def test_flights_list_destination_is_null_for_exactly_27_flights(
    client: httpx.AsyncClient,
) -> None:
    items = (await client.get(f"{ING}/flights")).json()["items"]

    assert sum(1 for f in items if f["to"] is None) == 27
    assert next(f for f in items if f["flight"] == 9)["to"] == "Airfield F"


async def test_flights_total_photos_counts_all_types_of_the_flight_sol(
    client: httpx.AsyncClient,
) -> None:
    items = (await client.get(f"{ING}/flights")).json()["items"]

    assert {f["flight"]: f["total_photos"] for f in items if f["total_photos"]} == {1: 3, 9: 1}
    # Flight 2 (sol 61) has no photos: 0, not null; sol 1069 has photos but no flight.
    assert next(f for f in items if f["flight"] == 2)["total_photos"] == 0
    assert sum(f["total_photos"] for f in items) == 4


async def test_flights_total_photos_does_not_count_perseverance_photos_of_the_same_sol(
    client: httpx.AsyncClient,
) -> None:
    items = (await client.get(f"{ING}/flights")).json()["items"]

    assert items[0]["total_photos"] == 3  # sol 58: three helicopter photos, not four


async def test_flights_list_is_cacheable_for_five_minutes(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{ING}/flights")

    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["etag"]
    again = await client.get(f"{ING}/flights", headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304


async def test_flights_slug_is_case_insensitive(client: httpx.AsyncClient) -> None:
    upper = await client.get(f"{V2}/rovers/INGENUITY/flights")
    mixed = await client.get(f"{V2}/rovers/InGeNuItY/flights/9")

    assert upper.status_code == 200 and len(upper.json()["items"]) == 72
    assert mixed.status_code == 200 and mixed.json()["flight"] == 9


async def test_flights_list_ignores_pagination_parameters(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{ING}/flights", params={"page": 2, "per_page": 5})).json()

    assert list(body) == ["items"]
    assert len(body["items"]) == 72


# --- one flight -----------------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 9, 12, 72])
async def test_flight_by_number_returns_the_same_object_as_the_list(
    client: httpx.AsyncClient, n: int
) -> None:
    response = await client.get(f"{ING}/flights/{n}")

    assert response.status_code == 200
    assert_json(response.json(), expected_flight(FLIGHTS[n - 1]))


async def test_flight_9_has_both_airfields_and_one_photo(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{ING}/flights/9")).json()

    assert body == {
        "flight": 9,
        "sol": 133,
        "date": "2021-07-05",
        "distance_m": 625,
        "max_altitude_m": 10,
        "max_groundspeed_mps": 5,
        "duration_s": 166.4,
        "from": "Airfield E",
        "to": "Airfield F",
        "total_photos": 1,
    }


async def test_flight_decimal_values_are_numbers_not_strings(client: httpx.AsyncClient) -> None:
    body = (await client.get(f"{ING}/flights/4")).json()  # 3.50 m/s, 116.9 s

    assert body["max_groundspeed_mps"] == 3.5 and is_json_number(body["max_groundspeed_mps"])
    assert body["duration_s"] == 116.9 and is_json_number(body["duration_s"])
    assert Decimal(str(body["duration_s"])) == Decimal("116.9")


@pytest.mark.parametrize(
    "n", ["0", "73", "1000", str(INT32_MAX), str(INT32_MAX + 1), "99999999999"]
)
async def test_flight_that_does_not_exist_returns_404_flight_not_found(
    client: httpx.AsyncClient, n: str
) -> None:
    response = await client.get(f"{ING}/flights/{n}")

    assert_error(response, 404, "flight_not_found")


@pytest.mark.parametrize("n", ["abc", "1.5", "1e2", "0x10", "one"])
async def test_flight_with_a_non_integer_number_returns_422(
    client: httpx.AsyncClient, n: str
) -> None:
    response = await client.get(f"{ING}/flights/{n}")

    assert_validation_error(response, "n", "flight")


async def test_flight_with_a_huge_number_does_not_crash(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{ING}/flights/{'9' * 5000}")

    assert response.status_code in (404, 422)
    assert response.json()["error"]["code"] in ("flight_not_found", "validation_error")


# --- other rovers ---------------------------------------------------------------------------------


@pytest.mark.parametrize("rover", ["curiosity", "perseverance", "spirit", "opportunity"])
@pytest.mark.parametrize("tail", ["", "/1", "/72"])
async def test_flights_of_another_existing_rover_return_404_not_found(
    client: httpx.AsyncClient, rover: str, tail: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/flights{tail}")

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize("tail", ["", "/1"])
async def test_flights_of_an_unknown_rover_return_404_rover_not_found(
    client: httpx.AsyncClient, tail: str
) -> None:
    response = await client.get(f"{V2}/rovers/xyz/flights{tail}")

    assert_error(response, 404, "rover_not_found")


# --- location and traverse ------------------------------------------------------------------------


async def test_ingenuity_location_is_unavailable_and_empty(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{ING}/location")

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "rover": "ingenuity",
            "available": False,
            "updated_at": None,
            "source_max_sol": None,
            "items": [],
        },
    )


async def test_ingenuity_traverse_is_unavailable_and_has_no_features(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"{ING}/traverse")

    assert response.status_code == 200
    assert_json(
        response.json(),
        {
            "type": "FeatureCollection",
            "rover": "ingenuity",
            "available": False,
            "updated_at": None,
            "source_max_sol": None,
            "features": [],
        },
    )


@pytest.mark.parametrize("params", [{"site": 0}, {"site": 0, "drive_from": 0, "drive_to": 0}])
async def test_ingenuity_photos_have_no_site_so_site_filters_find_nothing(
    client: httpx.AsyncClient, params: dict[str, int]
) -> None:
    body = (await client.get(f"{ING}/photos", params=params)).json()

    assert (body["items"], body["total"]) == ([], 0)
