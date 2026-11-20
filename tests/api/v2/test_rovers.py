"""GET /api/v2/rovers, /rovers/{rover}, /rovers/{rover}/cameras and the common rover rules."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.api.v2.conftest import (
    CAMERA_KEYS,
    CAMERAS_COUNT,
    ROVER_IDS,
    ROVER_KEYS,
    V2,
    World,
    assert_error,
    assert_json,
)

pytestmark = pytest.mark.api

# Every endpoint scoped to a rover: they all answer 404 rover_not_found the same way.
ROVER_ENDPOINTS = [
    "",
    "/cameras",
    "/photos",
    "/photos/latest",
    "/manifest",
    "/location",
    "/traverse",
]
ROVER_ENDPOINT_IDS = [
    "rover",
    "cameras",
    "photos",
    "latest",
    "manifest",
    "location",
    "traverse",
]


# --- /rovers -------------------------------------------------------------------------------


async def test_rovers_list_returns_exact_rovers_sorted_by_id(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(f"{V2}/rovers")

    assert response.status_code == 200
    assert_json(response.json(), {"items": [world.rover(rover) for rover in ROVER_IDS]})


async def test_rovers_list_has_no_pagination_keys(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"{V2}/rovers", params={"page": "2", "per_page": "1"})

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items"]
    assert [rover["id"] for rover in body["items"]] == ROVER_IDS


async def test_rover_counters_cover_all_sample_types(
    client: httpx.AsyncClient, world: World
) -> None:
    """max_sol/total_photos count subframes and other types (unlike v1)."""
    body = (await client.get(f"{V2}/rovers/curiosity")).json()

    assert body["max_sol"] == 20  # sol 20 has only a subframe
    assert body["max_date"] == "2012-08-26"
    assert body["total_photos"] == 9


async def test_rover_without_data_has_nulls(client: httpx.AsyncClient, world: World) -> None:
    body = (await client.get(f"{V2}/rovers/spirit")).json()

    assert body["max_sol"] is None
    assert body["max_date"] is None
    assert body["last_synced_at"] is None
    assert body["total_photos"] == 0
    assert body["cameras_count"] == CAMERAS_COUNT["spirit"]


async def test_rover_last_synced_at_is_utc_z_without_fraction(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(f"{V2}/rovers/perseverance")).json()

    assert body["last_synced_at"] == "2026-11-17T06:00:12Z"


async def test_rover_cameras_count_includes_cameras_added_by_sync(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(f"{V2}/rovers/curiosity")).json()

    assert body["cameras_count"] == 10  # 7 seed + FHAZ_LEFT_B, MAST_LEFT, CHEMCAM_RMI


@pytest.mark.parametrize(
    ("rover_id", "end_date", "data_source"),
    [
        ("curiosity", None, "sync"),
        ("perseverance", None, "sync"),
        ("spirit", "2011-05-25", "archive"),
        ("opportunity", "2019-02-13", "archive"),
    ],
)
async def test_rover_end_date_and_data_source(
    client: httpx.AsyncClient,
    world: World,
    rover_id: str,
    end_date: str | None,
    data_source: str,
) -> None:
    detail = (await client.get(f"{V2}/rovers/{rover_id}")).json()
    listed = {r["id"]: r for r in (await client.get(f"{V2}/rovers")).json()["items"]}[rover_id]

    for body in (detail, listed):
        assert body["end_date"] == end_date
        assert body["data_source"] == data_source
        keys = list(body)
        assert keys.index("end_date") == keys.index("landing_date") + 1
        assert keys.index("end_date") + 1 == keys.index("status")
        assert keys[-1] == "data_source"


async def test_rover_active_missions_have_null_end_date_independent_of_status(
    client: httpx.AsyncClient, world: World
) -> None:
    for rover_id in ("curiosity", "perseverance"):
        body = (await client.get(f"{V2}/rovers/{rover_id}")).json()
        assert body["status"] == "active" and body["end_date"] is None


async def test_rover_end_date_of_finished_mission_is_not_max_date(
    client: httpx.AsyncClient, world: World
) -> None:
    """end_date is a reference date of the mission end; the last photo stays in max_date."""
    body = (await client.get(f"{V2}/rovers/opportunity")).json()

    assert body["end_date"] == "2019-02-13"
    assert body["max_date"] == "2004-02-04"


# --- /rovers/{rover} -----------------------------------------------------------------------


@pytest.mark.parametrize("rover_id", ROVER_IDS)
async def test_rover_detail_returns_exact_rover(
    client: httpx.AsyncClient, world: World, rover_id: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover_id}")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ROVER_KEYS
    assert_json(body, world.rover(rover_id))


@pytest.mark.parametrize("slug", ["Perseverance", "PERSEVERANCE", "pErSeVeRaNcE"])
async def test_rover_slug_any_case_returns_rover(
    client: httpx.AsyncClient, world: World, slug: str
) -> None:
    response = await client.get(f"{V2}/rovers/{slug}")

    assert response.status_code == 200
    assert_json(response.json(), world.rover("perseverance"))


@pytest.mark.parametrize("suffix", ROVER_ENDPOINTS, ids=ROVER_ENDPOINT_IDS)
@pytest.mark.parametrize(
    ("slug", "shown"),
    [("pluto", "pluto"), ("PLUTO", "pluto"), ("curiosity2", "curiosity2")],
    ids=["unknown", "unknown-upper", "not-a-slug"],
)
async def test_rover_unknown_returns_404_rover_not_found(
    client: httpx.AsyncClient, world: World, suffix: str, slug: str, shown: str
) -> None:
    response = await client.get(f"{V2}/rovers/{slug}{suffix}")

    error = assert_error(response, 404, "rover_not_found")
    assert error["message"] == f"Rover '{shown}' not found"
    assert error["details"] is None


@pytest.mark.parametrize("suffix", ROVER_ENDPOINTS, ids=ROVER_ENDPOINT_IDS)
async def test_rover_long_unknown_slug_echoes_at_most_64_chars(
    client: httpx.AsyncClient, world: World, suffix: str
) -> None:
    slug = "a" * 300

    response = await client.get(f"{V2}/rovers/{slug}{suffix}")

    error = assert_error(response, 404, "rover_not_found")
    assert "a" * 65 not in error["message"], error["message"]


class _NoDatabaseSession:
    """Any use of the session fails the request loudly."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"database touched: session.{name}")


class _NoDatabaseFactory:
    def __call__(self) -> "_NoDatabaseFactory":
        return self

    async def __aenter__(self) -> _NoDatabaseSession:
        return _NoDatabaseSession()

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
async def no_db_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """A client whose application cannot use the database at all."""
    app.state.session_factory = _NoDatabaseFactory()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.mark.parametrize("suffix", ROVER_ENDPOINTS, ids=ROVER_ENDPOINT_IDS)
@pytest.mark.parametrize(
    "slug",
    ["curiosity2", "mars-2020", "cur_iosity", "%00", "curiosity%00", "%D0%BC%D0%B0%D1%80%D1%81"],
    ids=["digit", "dash", "underscore", "nul", "trailing-nul", "cyrillic"],
)
async def test_rover_not_a_slug_returns_404_without_database(
    no_db_client: httpx.AsyncClient, suffix: str, slug: str
) -> None:
    response = await no_db_client.get(f"{V2}/rovers/{slug}{suffix}")

    assert_error(response, 404, "rover_not_found")


# --- /rovers/{rover}/cameras ---------------------------------------------------------------


@pytest.mark.parametrize("rover_id", ROVER_IDS)
async def test_cameras_return_whole_catalog_sorted_by_code(
    client: httpx.AsyncClient, world: World, rover_id: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover_id}/cameras")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items"]
    codes = [camera["code"] for camera in body["items"]]
    assert codes == sorted(world.cameras[rover_id])
    assert len(codes) == CAMERAS_COUNT[rover_id]
    for camera in body["items"]:
        assert list(camera) == CAMERA_KEYS
        assert camera["name"] == world.cameras[rover_id][camera["code"]]


async def test_cameras_total_photos_counts_all_sample_types(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(f"{V2}/rovers/curiosity/cameras")

    totals = {camera["code"]: camera["total_photos"] for camera in response.json()["items"]}
    assert totals == {
        "CHEMCAM": 0,
        "CHEMCAM_RMI": 1,  # "chemcam prc"
        "FHAZ": 2,  # full + subframe; FHAZ_LEFT_B is a different camera
        "FHAZ_LEFT_B": 2,
        "MAHLI": 1,
        "MARDI": 0,
        "MAST": 0,
        "MAST_LEFT": 2,  # mixed + downsampled
        "NAVCAM": 1,
        "RHAZ": 0,
    }


async def test_cameras_exact_json_for_perseverance(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"{V2}/rovers/PERSEVERANCE/cameras")

    totals = {"NAVCAM_LEFT": 3, "NAVCAM_RIGHT": 2, "MCZ_LEFT": 1, "SUPERCAM_RMI": 1}
    expected = {
        "items": [
            {"code": code, "name": name, "total_photos": totals.get(code, 0)}
            for code, name in sorted(world.cameras["perseverance"].items())
        ]
    }
    assert_json(response.json(), expected)


async def test_cameras_rover_without_data_has_zero_totals(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(f"{V2}/rovers/spirit/cameras")).json()

    assert [camera["total_photos"] for camera in body["items"]] == [0] * CAMERAS_COUNT["spirit"]


@pytest.mark.parametrize("rover_id", ROVER_IDS)
async def test_cameras_totals_equal_group_by_over_photos(
    client: httpx.AsyncClient, world: World, migrated_db: str, rover_id: str
) -> None:
    """Stage 7: totals come from camera_sol_counts and equal the old GROUP BY result."""
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT c.code, count(p.id) AS n FROM cameras c "
                    "LEFT JOIN photos p ON p.camera_id = c.id "
                    "WHERE c.rover_id = :rover GROUP BY c.code"
                ),
                {"rover": rover_id},
            )
            expected = {code: n for code, n in rows}
    finally:
        await engine.dispose()

    body = (await client.get(f"{V2}/rovers/{rover_id}/cameras")).json()

    assert {camera["code"]: camera["total_photos"] for camera in body["items"]} == expected
