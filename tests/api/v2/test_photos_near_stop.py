"""`near_stop` of Photo in `GET /api/v2/photos` and `/rovers/{rover}/photos` (docs/API.md, 18B).

Only with `near`, the last key of Photo: `{"sol", "site", "drive", "lat", "lon", "distance_km"}`;
the stop is the one `PhotoDetail.location` names for the same photo; `distance_km` is the
great-circle distance (R = 3389.5 km) from the point to that stop, rounded to 3 decimals and
never above `radius_km`. Without `near` the key is absent.
"""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests._locations import Factory, insert_waypoints, waypoint
from tests.api.v2.conftest import PHOTO_KEYS, V2
from tests.conftest import build_record

pytestmark = pytest.mark.api

P = "perseverance"
C = "curiosity"
NEAR_STOP_KEYS = ["sol", "site", "drive", "lat", "lon", "distance_km"]
RADIUS_EARTH = 3389.5

# Perseverance stops: site 80 drive 100 (the query point), drive 200 ~11.8 km north.
P_STOPS = [waypoint(2030, 80, 100, 18.0, 77.0), waypoint(2031, 80, 200, 18.1, 77.0)]
# Curiosity stops: 0.01 degree steps give 0, 0.5916, 1.1832 km from the point (-4, 137).
C_STOPS = [
    waypoint(1000, 12, 10, -4.0, 137.0),
    waypoint(1001, 12, 20, -4.01, 137.0),
    waypoint(1002, 12, 30, -4.02, 137.0),
]
# source_id -> (site, drive)
P_PHOTOS = {"p-at-a": (80, 100), "p-between": (80, 150), "p-at-b": (80, 200)}
C_PHOTOS = {"c-k0": (12, 10), "c-k1": (12, 20), "c-between": (12, 25), "c-k2": (12, 30)}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * RADIUS_EARTH * math.asin(math.sqrt(a))


def decimals(value: float) -> int:
    text_value = repr(value)
    return len(text_value.split(".")[1]) if "." in text_value else 0


async def _seed(factory: Factory, rover: str, photos: dict[str, tuple[int, int]], sol: int) -> None:
    records = []
    for index, (source_id, (site, drive)) in enumerate(photos.items()):
        taken = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
        records.append(
            build_record(
                source_id=source_id,
                sol=sol,
                camera_code="NAVCAM_LEFT" if rover == P else "FHAZ",
                taken_at=taken,
                earth_date=taken.date(),
                site=site,
                drive=drive,
            )
        )
    async with factory() as session:
        await upsert_photos(session, rover, records)
        await recompute_sol_summaries(session, rover, {sol})
        await refresh_rover_stats(session, rover)
        await session.commit()


@pytest.fixture
async def world(clean_db: Factory) -> dict[str, int]:
    await _seed(clean_db, P, P_PHOTOS, 2031)
    await _seed(clean_db, C, C_PHOTOS, 1002)
    await insert_waypoints(clean_db, P, P_STOPS)
    await insert_waypoints(clean_db, C, C_STOPS)
    async with clean_db() as session:
        return dict((await session.execute(text("SELECT source_id, id FROM photos"))).all())


ENDPOINTS = pytest.mark.parametrize(
    ("path", "extra"),
    [("/photos", {"rovers": C}), (f"/rovers/{C}/photos", {})],
    ids=["photos", "rover-photos"],
)


@ENDPOINTS
async def test_near_stop_is_the_last_key_with_typed_fields(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict[str, Any]
) -> None:
    response = await client.get(
        f"{V2}{path}", params={**extra, "near": "-4.0,137.0", "radius_km": 5, "per_page": 100}
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == len(C_PHOTOS)
    for item in items:
        assert list(item) == [*PHOTO_KEYS, "near_stop"]
        stop = item["near_stop"]
        assert list(stop) == NEAR_STOP_KEYS
        assert all(type(stop[k]) is int for k in ("sol", "site", "drive"))
        assert all(isinstance(stop[k], float) for k in ("lat", "lon"))
        assert isinstance(stop["distance_km"], int | float)


@ENDPOINTS
async def test_near_stop_equals_photo_detail_location_and_distance_is_haversine(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict[str, Any]
) -> None:
    lat, lon = -4.0, 137.0
    response = await client.get(
        f"{V2}{path}", params={**extra, "near": f"{lat},{lon}", "radius_km": 5, "per_page": 100}
    )
    items = response.json()["items"]
    assert {i["id"] for i in items} == {world[s] for s in C_PHOTOS}

    for item in items:
        location = (await client.get(f"{V2}/photos/{item['id']}")).json()["location"]
        stop = item["near_stop"]
        for key in ("sol", "site", "drive", "lat", "lon"):
            assert stop[key] == location[key], (item["id"], key)
        expected = round(haversine_km(lat, lon, location["lat"], location["lon"]), 3)
        assert stop["distance_km"] == pytest.approx(expected, abs=0.0006)


async def test_near_stop_between_stops_names_the_earlier_stop(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await client.get(
        f"{V2}/photos", params={"rovers": C, "near": "-4.0,137.0", "radius_km": 5}
    )

    item = next(i for i in response.json()["items"] if i["id"] == world["c-between"])
    assert (item["near_stop"]["site"], item["near_stop"]["drive"]) == (12, 20)
    assert item["near_stop"]["distance_km"] == pytest.approx(0.592, abs=0.002)


@pytest.mark.parametrize("radius", [0.6, 1.0, 1.2, 5.0], ids=lambda r: f"r{r}")
async def test_near_stop_distance_is_within_radius_and_has_at_most_three_decimals(
    client: httpx.AsyncClient, world: dict[str, int], radius: float
) -> None:
    response = await client.get(
        f"{V2}/photos",
        params={"rovers": C, "near": "-4.0,137.0", "radius_km": radius, "per_page": 100},
    )

    items = response.json()["items"]
    assert items
    for item in items:
        distance = item["near_stop"]["distance_km"]
        assert 0 <= distance <= radius
        assert decimals(distance) <= 3


async def test_near_stop_perseverance_exact_values(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await client.get(
        f"{V2}/photos", params={"rovers": P, "near": "18.0,77.0", "radius_km": 1}
    )

    items = response.json()["items"]
    assert {i["id"] for i in items} == {world["p-at-a"], world["p-between"]}
    for item in items:
        assert item["near_stop"] == {
            "sol": 2030,
            "site": 80,
            "drive": 100,
            "lat": 18.0,
            "lon": 77.0,
            "distance_km": 0.0,
        }


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"rovers": C}, id="photos-plain"),
        pytest.param({"rovers": C, "sol_min": 1000}, id="photos-sol-filter"),
        pytest.param({"rovers": C, "sort": "taken_at"}, id="photos-sorted"),
    ],
)
async def test_near_stop_key_is_absent_without_near(
    client: httpx.AsyncClient, world: dict[str, int], params: dict[str, Any]
) -> None:
    response = await client.get(f"{V2}/photos", params=params)

    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    for item in items:
        assert "near_stop" not in item
        assert list(item) == PHOTO_KEYS


async def test_near_stop_radius_km_without_near_is_422(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/photos", params={"rovers": C, "radius_km": 1})

    assert response.status_code == 422


async def test_near_stop_distance_never_exceeds_a_four_decimal_radius(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    # The stop is 0.59158 km from the point: rounding to 3 decimals gives 0.592, above the radius.
    radius = 0.5916
    assert round(haversine_km(-4.0, 137.0, -4.01, 137.0), 3) > radius

    response = await client.get(
        f"{V2}/photos", params={"rovers": C, "near": "-4.0,137.0", "radius_km": radius}
    )

    items = response.json()["items"]
    assert {i["id"] for i in items} >= {world["c-k1"]}
    for item in items:
        assert item["near_stop"]["distance_km"] <= radius


async def test_near_stop_key_is_absent_in_rover_photos_latest_and_batch(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    latest = await client.get(f"{V2}/rovers/{C}/photos/latest")
    batch = await client.get(f"{V2}/photos/batch", params={"ids": str(world["c-k0"])})

    for items in (latest.json()["items"], batch.json()["items"]):
        assert items
        assert all("near_stop" not in item for item in items)


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_near_param_does_not_add_keys_to_v1(
    client: httpx.AsyncClient, world: dict[str, int], prefix: str
) -> None:
    response = await client.get(
        f"{prefix}/rovers/{C}/photos", params={"sol": 1002, "near": "-4.0,137.0"}
    )

    assert response.status_code == 200
    for photo in response.json()["photos"]:
        assert not {"near_stop", "local_time"} & set(photo)
