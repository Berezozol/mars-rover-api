"""Photos near a point: `near=LAT,LON` and `radius_km` (docs/API.md, stage 18B-3).

Covers `GET /api/v2/photos` and `GET /api/v2/rovers/{rover}/photos`. A photo is bound to the
stop of its own site with the greatest `drive` not greater than the photo's (the rule of
`PhotoDetail.location`); the photo is found when that stop lies within `radius_km` of the point
(haversine, R = 3389.5 km, so 0.01 degree of latitude = 0.5916 km). Stops are synthetic and all
radii keep a margin of at least ~0.003 km from the stop distances.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests._locations import Factory, insert_waypoints, waypoint
from tests.api.v2._meta import seconds_of
from tests.api.v2.conftest import V2, assert_validation_error
from tests.conftest import build_record

pytestmark = pytest.mark.api

P = "perseverance"
C = "curiosity"

# --- Perseverance: binding rule -----------------------------------------------------------------
# Site 80: A (drive 100) at the query point, B (drive 200) ~11.8 km north. Site 81: C (drive 50)
# ~23.7 km north. Site 79 has no stops.
POINT_A = "18.0,77.0"
POINT_B = "18.1,77.0"
POINT_C = "18.2,77.0"
P_STOPS = [
    waypoint(2030, 80, 100, 18.0, 77.0),
    waypoint(2031, 80, 200, 18.1, 77.0),
    waypoint(2031, 81, 50, 18.2, 77.0),
]
# source_id -> (site, drive)
P_PHOTOS: dict[str, tuple[int | None, int | None]] = {
    "p-at-a": (80, 100),
    "p-between": (80, 150),  # between A and B: bound to A
    "p-at-b": (80, 200),
    "p-after-b": (80, 9999),  # after the last stop of the site: bound to B
    "p-before-first": (80, 50),  # before the first stop of its site
    "p-site81-at": (81, 50),
    "p-site81-before": (81, 10),
    "p-no-stops-site": (79, 5),
    "p-no-site": (None, 100),
    "p-no-drive": (80, None),
    "p-no-both": (None, None),
}

# --- Curiosity: radius boundaries ---------------------------------------------------------------
# Point (-4.0, 137.0). Site 12 stops 0.01 degree of latitude apart: 0, 0.5916, 1.1832, 2.9580 km.
# Site 13 has one stop 0.01 degree of longitude east: 0.5902 km.
C_POINT = "-4.0,137.0"
C_STOPS = [
    waypoint(1000, 12, 10, -4.0, 137.0),
    waypoint(1001, 12, 20, -4.01, 137.0),
    waypoint(1002, 12, 30, -4.02, 137.0),
    waypoint(1003, 12, 60, -4.05, 137.0),
    waypoint(1007, 13, 1, -4.0, 137.01),
]
# source_id -> (sol, camera, site, drive, minutes after the sol base time)
C_PHOTOS: dict[str, tuple[int, str, int, int, int]] = {
    "c-k0": (1000, "FHAZ", 12, 10, 0),
    "c-k0-nav": (1000, "NAVCAM", 12, 10, 1),
    "c-k0-late": (1005, "FHAZ", 12, 15, 0),  # between stops 10 and 20: bound to 10
    "c-k1": (1001, "FHAZ", 12, 20, 0),
    "c-k2": (1002, "FHAZ", 12, 30, 0),
    "c-k5": (1003, "FHAZ", 12, 60, 0),
    "c-east": (1007, "FHAZ", 13, 1, 0),
}
C_DEFAULT = {"c-east", "c-k0-late", "c-k1", "c-k0-nav", "c-k0"}  # radius 1 km
# Default order: sol DESC, taken_at DESC, id DESC.
C_ORDER_R12 = ["c-east", "c-k0-late", "c-k2", "c-k1", "c-k0-nav", "c-k0"]


def _taken(sol: int, minutes: int = 0) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=sol - 900, minutes=minutes)


async def _seed(factory: Factory, rover: str, records: list[Any]) -> None:
    async with factory() as session:
        await upsert_photos(session, rover, records)
        await recompute_sol_summaries(session, rover, {r.sol for r in records})
        await refresh_rover_stats(session, rover)
        await session.commit()


@pytest.fixture
async def world(clean_db: Factory) -> dict[str, int]:
    """Both datasets plus their stops; returns source_id -> photo id."""
    p_records = [
        build_record(
            source_id=sid,
            sol=2031,
            camera_code="NAVCAM_LEFT",
            sample_type="full",
            taken_at=_taken(2031),
            earth_date=_taken(2031).date(),
            site=site,
            drive=drive,
            img_full=f"https://images.example.test/{sid}.png",
        )
        for sid, (site, drive) in P_PHOTOS.items()
    ]
    c_records = [
        build_record(
            source_id=sid,
            sol=sol,
            camera_code=camera,
            sample_type="full",
            taken_at=_taken(sol, minutes),
            earth_date=_taken(sol, minutes).date(),
            site=site,
            drive=drive,
            img_full=f"https://images.example.test/{sid}.jpg",
        )
        for sid, (sol, camera, site, drive, minutes) in C_PHOTOS.items()
    ]
    await _seed(clean_db, P, p_records)
    await _seed(clean_db, C, c_records)
    await insert_waypoints(clean_db, P, P_STOPS)
    await insert_waypoints(clean_db, C, C_STOPS)
    async with clean_db() as session:
        return dict((await session.execute(text("SELECT source_id, id FROM photos"))).all())


def _by_id(world: dict[str, int]) -> dict[int, str]:
    return {v: k for k, v in world.items()}


async def found(
    client: httpx.AsyncClient, world: dict[str, int], path: str, **params: Any
) -> tuple[list[str], dict[str, Any]]:
    response = await client.get(f"{V2}{path}", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    names = _by_id(world)
    return [names[item["id"]] for item in body["items"]], body


async def found_set(
    client: httpx.AsyncClient, world: dict[str, int], path: str = "/photos", **params: Any
) -> set[str]:
    params.setdefault("per_page", 100)
    if path == "/photos":
        params.setdefault("rovers", C)
    names, body = await found(client, world, path, **params)
    assert len(names) == len(set(names)), "duplicates in the list"
    assert body["total"] == len(names)
    return set(names)


PHOTOS_C = {"path": "/photos", "rovers": C}
ROVER_C = {"path": f"/rovers/{C}/photos"}
ENDPOINTS = pytest.mark.parametrize(
    "path, extra",
    [("/photos", {"rovers": C}), (f"/rovers/{C}/photos", {})],
    ids=["photos", "rover-photos"],
)
ENDPOINTS_P = pytest.mark.parametrize(
    "path, extra",
    [("/photos", {"rovers": P}), (f"/rovers/{P}/photos", {})],
    ids=["photos", "rover-photos"],
)


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"radius_km": "1"},
        {"radius_km": "0.5"},
    ],
    ids=["radius-1", "radius-0.5"],
)
@ENDPOINTS
async def test_near_radius_without_near_returns_422(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict, params: dict
) -> None:
    response = await client.get(f"{V2}{path}", params={**extra, **params})
    assert_validation_error(response, "radius_km")


@pytest.mark.parametrize(
    "near",
    [
        "18.0",
        "1,2,3",
        "abc",
        "a,b",
        "nan,1",
        "1,nan",
        "inf,1",
        "1,-inf",
        "inf,inf",
        "18.0, 77.0",
        " 18.0,77.0",
        "18.0,77.0 ",
        "18.0,",
        ",77.0",
        ",",
        "",
        "18.0;77.0",
        "1e400,1",
    ],
    ids=[
        "one-number",
        "three-numbers",
        "text",
        "two-texts",
        "nan-lat",
        "nan-lon",
        "inf-lat",
        "minus-inf-lon",
        "inf-both",
        "space-after-comma",
        "leading-space",
        "trailing-space",
        "missing-lon",
        "missing-lat",
        "only-comma",
        "empty",
        "semicolon",
        "overflow",
    ],
)
@ENDPOINTS
async def test_near_malformed_returns_422(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict, near: str
) -> None:
    response = await client.get(f"{V2}{path}", params={**extra, "near": near})
    assert_validation_error(response, "near")


@pytest.mark.parametrize(
    "near",
    ["90.1,0", "-90.1,0", "0,180.1", "0,-180.1", "91,181", "1000,0"],
    ids=["lat-high", "lat-low", "lon-high", "lon-low", "both", "far-lat"],
)
@ENDPOINTS
async def test_near_out_of_range_returns_422(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict, near: str
) -> None:
    response = await client.get(f"{V2}{path}", params={**extra, "near": near})
    assert_validation_error(response, "near")


@pytest.mark.parametrize(
    "near",
    ["90,180", "-90,-180", "0,0", "-4,137", "+18.5,77", "18,77.000"],
    ids=["max", "min", "zero", "integers", "plus-sign", "trailing-zeros"],
)
@ENDPOINTS
async def test_near_valid_formats_and_range_edges_return_200(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict, near: str
) -> None:
    response = await client.get(f"{V2}{path}", params={**extra, "near": near})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    "radius",
    ["0", "0.009", "0.0099", "50.01", "51", "-1", "abc", "nan", "inf", "-inf", "", "1,5"],
    ids=[
        "zero",
        "below-min",
        "just-below-min",
        "just-above-max",
        "above-max",
        "negative",
        "text",
        "nan",
        "inf",
        "minus-inf",
        "empty",
        "comma",
    ],
)
@ENDPOINTS
async def test_near_radius_out_of_range_returns_422(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict, radius: str
) -> None:
    response = await client.get(
        f"{V2}{path}", params={**extra, "near": C_POINT, "radius_km": radius}
    )
    assert_validation_error(response, "radius_km")


@pytest.mark.parametrize("radius", ["0.01", "50", "50.0", "1", "0.5", "49.99"])
@ENDPOINTS
async def test_near_radius_in_range_returns_200(
    client: httpx.AsyncClient, clean_db: Factory, path: str, extra: dict, radius: str
) -> None:
    response = await client.get(
        f"{V2}{path}", params={**extra, "near": C_POINT, "radius_km": radius}
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "rovers",
    ["spirit", "opportunity", "ingenuity", "curiosity,perseverance", "spirit,curiosity"],
    ids=["spirit", "opportunity", "ingenuity", "two-rovers", "mer-and-curiosity"],
)
async def test_near_photos_unsupported_rover_set_returns_422(
    client: httpx.AsyncClient, clean_db: Factory, rovers: str
) -> None:
    response = await client.get(f"{V2}/photos", params={"near": C_POINT, "rovers": rovers})
    assert_validation_error(response, "near")


async def test_near_photos_without_rovers_returns_422(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    response = await client.get(f"{V2}/photos", params={"near": C_POINT})
    assert_validation_error(response, "near")


@pytest.mark.parametrize("rovers", ["Curiosity", "curiosity,curiosity", "CURIOSITY,curiosity"])
async def test_near_photos_single_rover_variants_return_200(
    client: httpx.AsyncClient, clean_db: Factory, rovers: str
) -> None:
    """Case-insensitive slug and collapsed repeats still mean exactly one rover."""
    response = await client.get(f"{V2}/photos", params={"near": C_POINT, "rovers": rovers})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("rover", ["spirit", "opportunity", "ingenuity"])
async def test_near_rover_photos_unsupported_rover_returns_422(
    client: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/photos", params={"near": C_POINT})
    assert_validation_error(response, "near")


@pytest.mark.parametrize("rover", [C, P])
async def test_near_rover_photos_supported_rover_returns_200(
    client: httpx.AsyncClient, clean_db: Factory, rover: str
) -> None:
    response = await client.get(f"{V2}/rovers/{rover}/photos", params={"near": C_POINT})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "page, per_page, status",
    [(401, 25, 422), (101, 100, 422), (201, 50, 422), (100, 100, 200), (400, 25, 200)],
    ids=["401x25", "101x100", "201x50", "100x100-edge", "400x25-edge"],
)
@ENDPOINTS
async def test_near_page_times_per_page_cap(
    client: httpx.AsyncClient,
    clean_db: Factory,
    path: str,
    extra: dict,
    page: int,
    per_page: int,
    status: int,
) -> None:
    params = {**extra, "near": C_POINT, "page": page, "per_page": per_page}
    response = await client.get(f"{V2}{path}", params=params)
    if status == 422:
        assert_validation_error(response, "page")
    else:
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []


async def test_rover_photos_without_near_has_no_page_cap(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """Contrast: the per-rover list has no page x per_page ceiling without `near`."""
    response = await client.get(f"{V2}/rovers/{C}/photos", params={"page": 101, "per_page": 100})
    assert response.status_code == 200, response.text


# --- radius ----------------------------------------------------------


@ENDPOINTS
async def test_near_default_radius_is_one_km(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    """Stops at 0 / 0.5902 / 0.5916 km are in; 1.1832 and 2.9580 km are out."""
    got = await found_set(client, world, path, near=C_POINT, **extra)
    assert got == C_DEFAULT


@ENDPOINTS
async def test_near_default_radius_equals_explicit_one(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    default = await found_set(client, world, path, near=C_POINT, **extra)
    explicit = await found_set(client, world, path, near=C_POINT, radius_km=1, **extra)
    assert default == explicit


@pytest.mark.parametrize(
    "radius, expected",
    [
        ("0.01", {"c-k0", "c-k0-nav", "c-k0-late"}),
        ("0.5", {"c-k0", "c-k0-nav", "c-k0-late"}),  # 0.5902 / 0.5916 are out
        ("0.6", C_DEFAULT),  # 0.5902 and 0.5916 are in, 1.1832 is out
        ("1.1", C_DEFAULT),
        ("1.2", C_DEFAULT | {"c-k2"}),
        ("2", C_DEFAULT | {"c-k2"}),
        ("3", C_DEFAULT | {"c-k2", "c-k5"}),
        ("50", C_DEFAULT | {"c-k2", "c-k5"}),
    ],
    ids=["0.01", "0.5", "0.6", "1.1", "1.2", "2", "3", "50"],
)
@ENDPOINTS
async def test_near_radius_boundaries(
    client: httpx.AsyncClient,
    world: dict[str, int],
    path: str,
    extra: dict,
    radius: str,
    expected: set[str],
) -> None:
    got = await found_set(client, world, path, near=C_POINT, radius_km=radius, **extra)
    assert got == expected


async def test_near_distance_uses_longitude_and_haversine(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    """From a point 0.01 degree east of the site-13 stop (0.5902 km): in at 0.6, out at 0.5."""
    point = "-4.0,137.02"
    assert "c-east" in await found_set(client, world, near=point, radius_km="0.6")
    assert "c-east" not in await found_set(client, world, near=point, radius_km="0.5")


async def test_near_empty_point_returns_total_zero(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    names, body = await found(client, world, "/photos", rovers=C, near="-80,0", radius_km=50)
    assert names == []
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1


async def test_near_rover_photos_empty_point_returns_nearest_sols_null(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/rovers/{C}/photos", params={"near": "-80,0"})
    body = response.json()
    assert (body["total"], body["items"]) == (0, [])
    assert body["nearest_sols"] is None


async def test_near_does_not_leak_other_rover(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    """Curiosity and Perseverance stops are matched only within the requested rover."""
    got = await found_set(client, world, near=POINT_A, radius_km=50, rovers=C)
    assert got == set()
    got = await found_set(client, world, near=C_POINT, radius_km=50, rovers=P)
    assert got == set()


# --- binding rule ----------------------------------------------------------


@ENDPOINTS_P
async def test_near_photo_between_stops_binds_to_earlier_stop(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    """A is in radius, B is not: photos at A and between A and B are found."""
    got = await found_set(client, world, path, near=POINT_A, **extra)
    assert got == {"p-at-a", "p-between"}


@ENDPOINTS_P
async def test_near_photo_between_stops_not_found_when_only_next_stop_in_radius(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    """B is in radius, A is not: `p-between` (bound to A) is not found."""
    got = await found_set(client, world, path, near=POINT_B, **extra)
    assert got == {"p-at-b", "p-after-b"}
    assert "p-between" not in got


@ENDPOINTS_P
async def test_near_last_stop_of_site_takes_all_larger_drives(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    got = await found_set(client, world, path, near=POINT_B, **extra)
    assert "p-after-b" in got
    assert "p-after-b" not in await found_set(client, world, path, near=POINT_A, **extra)


@ENDPOINTS_P
async def test_near_site_boundary_is_not_crossed(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    """Site 81 has a stop at drive 50 only: drive 10 is before it; site 80 stops do not apply."""
    got = await found_set(client, world, path, near=POINT_C, **extra)
    assert got == {"p-site81-at"}


@pytest.mark.parametrize("point", [POINT_A, POINT_B, POINT_C, "18.1,77.0"])
@ENDPOINTS_P
async def test_near_unbound_photos_are_never_found(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict, point: str
) -> None:
    """Before the first stop, without site or drive, or in a site without stops."""
    got = await found_set(client, world, path, near=point, radius_km=50, **extra)
    unbound = {
        "p-before-first",
        "p-site81-before",
        "p-no-stops-site",
        "p-no-site",
        "p-no-drive",
        "p-no-both",
    }
    assert not got & unbound


@ENDPOINTS_P
async def test_near_wide_radius_returns_each_bound_photo_once(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    """All three stops in radius: every bound photo appears once (no duplicates by stop)."""
    names, body = await found(
        client, world, path, near=POINT_B, radius_km=50, per_page=100, **extra
    )
    assert sorted(names) == sorted(["p-at-a", "p-between", "p-at-b", "p-after-b", "p-site81-at"])
    assert body["total"] == 5


# --- combination with other filters, sort, paging -------------------------------------------------


@ENDPOINTS
async def test_near_with_camera_filter(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    got = await found_set(client, world, path, near=C_POINT, camera="navcam", **extra)
    assert got == {"c-k0-nav"}


@ENDPOINTS
async def test_near_with_camera_list(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    got = await found_set(client, world, path, near=C_POINT, camera="NAVCAM,FHAZ", **extra)
    assert got == C_DEFAULT


@pytest.mark.parametrize(
    "bounds, expected",
    [
        ({"sol_min": 1001}, {"c-east", "c-k0-late", "c-k1"}),
        ({"sol_max": 1000}, {"c-k0", "c-k0-nav"}),
        ({"sol_min": 1001, "sol_max": 1005}, {"c-k0-late", "c-k1"}),
        ({"sol": 1000}, {"c-k0", "c-k0-nav"}),
    ],
    ids=["sol-min", "sol-max", "sol-range", "sol"],
)
async def test_near_with_sol_filters(
    client: httpx.AsyncClient, world: dict[str, int], bounds: dict, expected: set[str]
) -> None:
    got = await found_set(client, world, near=C_POINT, **bounds)
    assert got == expected


async def test_near_with_sol_filter_on_rover_photos(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    got = await found_set(client, world, f"/rovers/{C}/photos", near=C_POINT, sol=1005)
    assert got == {"c-k0-late"}


async def test_near_with_site_filter_narrows_to_site(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    got = await found_set(client, world, near=C_POINT, site=13)
    assert got == {"c-east"}


@pytest.mark.parametrize(
    "sort, expected",
    [
        ("-sol", C_ORDER_R12),
        ("sol", ["c-k0", "c-k0-nav", "c-k1", "c-k2", "c-k0-late", "c-east"]),
        ("-taken_at", C_ORDER_R12),
        ("taken_at", ["c-k0", "c-k0-nav", "c-k1", "c-k2", "c-k0-late", "c-east"]),
    ],
    ids=["-sol", "sol", "-taken_at", "taken_at"],
)
@ENDPOINTS
async def test_near_sort_orders_result(
    client: httpx.AsyncClient,
    world: dict[str, int],
    path: str,
    extra: dict,
    sort: str,
    expected: list[str],
) -> None:
    names, body = await found(
        client, world, path, near=C_POINT, radius_km="1.2", sort=sort, **extra
    )
    assert names == expected
    assert body["total"] == 6


@ENDPOINTS
async def test_near_default_sort_is_sol_desc(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    names, _ = await found(client, world, path, near=C_POINT, radius_km="1.2", **extra)
    assert names == C_ORDER_R12


@ENDPOINTS
async def test_near_paging_total_is_whole_result_and_pages_do_not_overlap(
    client: httpx.AsyncClient, world: dict[str, int], path: str, extra: dict
) -> None:
    pages = []
    for page in (1, 2, 3, 4):
        names, body = await found(
            client, world, path, near=C_POINT, radius_km="1.2", per_page=2, page=page, **extra
        )
        assert body["total"] == 6
        assert body["page"] == page
        assert body["per_page"] == 2
        pages.append(names)
    assert pages == [C_ORDER_R12[0:2], C_ORDER_R12[2:4], C_ORDER_R12[4:6], []]


async def test_near_response_envelope_matches_photo_list(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    """`/photos` has no `nearest_sols`; items are regular Photo objects."""
    response = await client.get(f"{V2}/photos", params={"rovers": C, "near": C_POINT})
    body = response.json()
    assert list(body) == ["items", "page", "per_page", "total"]
    assert {item["rover"] for item in body["items"]} == {C}
    assert body["per_page"] == 25
    assert body["total"] == len(C_DEFAULT)


async def test_near_rover_photos_envelope_has_nearest_sols(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/rovers/{C}/photos", params={"near": C_POINT})
    body = response.json()
    assert list(body) == ["items", "page", "per_page", "total", "nearest_sols"]
    assert body["nearest_sols"] is None


async def test_photos_without_near_are_unchanged_by_stops(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    """Without `near` unbound photos stay in the list."""
    got = await found_set(client, world, rovers=P)
    assert got == set(P_PHOTOS)


# --- near combined with every other filter ---------------------------------------------------
# One dataset per rover. Sites 80, 81 and 82 each have a stop at drive 100 AT the query point;
# site 90 has a stop ~30 km away. Photos `a`..`h` are bound to a stop at the point (near set);
# `far*` are bound to the far stop, `pre` precedes the first stop of its site, `nosite` has none.
# Every filter case removes at least one photo of the near set; `far*` match many filters.


@dataclass(frozen=True)
class ComboPhoto:
    name: str
    sol: int
    day: int  # days after 2026-01-01, taken at 12:00 UTC
    clock: str | None  # local mean solar time HH:MM:SS
    size: tuple[int, int] | None  # frame size (only Perseverance stores it)
    sample_type: str
    site: int | None
    drive: int | None


COMBO_PHOTOS = [
    ComboPhoto("a", 2000, 0, "05:45:00", (1920, 1200), "full", 80, 100),
    ComboPhoto("b", 2000, 0, "12:00:00", (640, 480), "full", 80, 150),
    ComboPhoto("c", 2001, 1, "17:30:00", (1920, 1200), "subframe", 81, 100),
    ComboPhoto("d", 2001, 1, "23:30:00", (1920, 1199), "full", 81, 120),
    ComboPhoto("e", 2002, 2, "00:30:00", (800, 1300), "downsampled", 82, 100),
    ComboPhoto("f", 2002, 2, None, None, "full", 82, 100),
    ComboPhoto("g", 2003, 3, "18:45:00", (1920, 1200), "full", 80, 100),
    ComboPhoto("h", 2003, 3, "06:30:00", (1280, 720), "full", 81, 200),
    ComboPhoto("far", 2001, 1, "17:30:00", (1920, 1200), "full", 90, 100),
    ComboPhoto("far-sub", 2000, 0, "05:45:00", (1920, 1200), "subframe", 90, 150),
    ComboPhoto("pre", 2001, 1, "17:30:00", (1920, 1200), "full", 80, 50),
    ComboPhoto("nosite", 2001, 1, "05:45:00", (1920, 1200), "full", None, None),
]
NEAR_SET = {"a", "b", "c", "d", "e", "f", "g", "h"}
COMBO_ROVERS = {P: (18.0, 77.0, "NAVCAM_LEFT"), C: (-4.0, 137.0, "FHAZ")}


def _combo_extra(rover: str, photo: ComboPhoto) -> dict[str, Any]:
    stamp = "UNK" if photo.clock is None else f"Sol-{photo.sol:05d}M{photo.clock}.000"
    if rover == C:
        return {"extended": {"lmst": stamp, "dimension": "(1920,1200)"}}  # size must be ignored
    dimension = "UNK" if photo.size is None else f"({photo.size[0]},{photo.size[1]})"
    return {"date_taken_mars": stamp, "extended": {"dimension": dimension}}


def _taken_on(day: int) -> datetime:
    return datetime(2026, 1, 1, 12, tzinfo=UTC) + timedelta(days=day)


@pytest.fixture
async def combo_world(clean_db: Factory) -> dict[str, int]:
    """Both rovers with the combination dataset; returns source_id (`p-a`, `c-far`) -> photo id."""
    for rover, (lat, lon, camera) in COMBO_ROVERS.items():
        records = [
            build_record(
                source_id=f"{rover[0]}-{photo.name}",
                sol=photo.sol,
                camera_code=camera,
                sample_type=photo.sample_type,
                taken_at=_taken_on(photo.day),
                earth_date=_taken_on(photo.day).date(),
                site=photo.site,
                drive=photo.drive,
                extra=_combo_extra(rover, photo),
            )
            for photo in COMBO_PHOTOS
        ]
        await _seed(clean_db, rover, records)
        stops = [waypoint(2000, site, 100, lat, lon) for site in (80, 81, 82)]
        stops.append(waypoint(2000, 90, 100, lat + 0.5, lon))
        await insert_waypoints(clean_db, rover, stops)
    async with clean_db() as session:
        return dict((await session.execute(text("SELECT source_id, id FROM photos"))).all())


def _in_window(photo: ComboPhoto, lo: str | None, hi: str | None) -> bool:
    """`lo <= t < hi`, past midnight when lo > hi; photos without time never match."""
    if photo.clock is None:
        return False
    t = seconds_of(photo.clock)
    a = None if lo is None else seconds_of(lo)
    b = None if hi is None else seconds_of(hi)
    if a is not None and b is not None and a > b:
        return t >= a or t < b
    return (a is None or t >= a) and (b is None or t < b)


def _golden(photo: ComboPhoto) -> bool:
    return _in_window(photo, "05:30", "07:00") or _in_window(photo, "17:00", "18:30")


def _size(photo: ComboPhoto, width: int = 0, height: int = 0) -> bool:
    return photo.size is not None and photo.size[0] >= width and photo.size[1] >= height


def _site(photo: ComboPhoto, lo: int, hi: int) -> bool:
    return photo.site is not None and lo <= photo.site <= hi


def _drive(photo: ComboPhoto, site: int, lo: int, hi: int) -> bool:
    return photo.site == site and photo.drive is not None and lo <= photo.drive <= hi


Pred = Callable[[ComboPhoto], bool]
BOTH = (P, C)
ENDS = ("photos", "rover")
# id, query parameters, reference predicate, rovers the case applies to
COMBOS: list[tuple[str, dict[str, Any], Pred, tuple[str, ...]]] = [
    ("date-min", {"date_min": "2026-01-02"}, lambda p: p.day >= 1, BOTH),
    ("date-max", {"date_max": "2026-01-02"}, lambda p: p.day <= 1, BOTH),
    (
        "date-range",
        {"date_min": "2026-01-02", "date_max": "2026-01-03"},
        lambda p: 1 <= p.day <= 2,
        BOTH,
    ),
    ("earth-date", {"earth_date": "2026-01-03"}, lambda p: p.day == 2, BOTH),
    (
        "local-time-min",
        {"local_time_min": "12:00"},
        lambda p: _in_window(p, "12:00", None),
        BOTH,
    ),
    (
        "local-time-max",
        {"local_time_max": "06:00"},
        lambda p: _in_window(p, None, "06:00"),
        BOTH,
    ),
    (
        "local-time-range",
        {"local_time_min": "05:00", "local_time_max": "07:00"},
        lambda p: _in_window(p, "05:00", "07:00"),
        BOTH,
    ),
    (
        "local-time-wrap-midnight",
        {"local_time_min": "23:00", "local_time_max": "01:00"},
        lambda p: _in_window(p, "23:00", "01:00"),
        BOTH,
    ),
    (
        "local-time-wrap-long",
        {"local_time_min": "17:00", "local_time_max": "06:00"},
        lambda p: _in_window(p, "17:00", "06:00"),
        BOTH,
    ),
    ("golden-hour", {"golden_hour": "true"}, _golden, BOTH),
    (
        "golden-hour-and-sol-min",
        {"golden_hour": "true", "sol_min": 2001},
        lambda p: _golden(p) and p.sol >= 2001,
        BOTH,
    ),
    ("min-width", {"min_width": 1920}, lambda p: _size(p, 1920), (P,)),
    ("min-height", {"min_height": 1200}, lambda p: _size(p, 0, 1200), (P,)),
    (
        "min-width-and-height",
        {"min_width": 1920, "min_height": 1200},
        lambda p: _size(p, 1920, 1200),
        (P,),
    ),
    (
        "min-width-and-camera",
        {"min_width": 1920, "camera": "navcam_left"},
        lambda p: _size(p, 1920),
        (P,),
    ),
    (
        "sample-type-subframe",
        {"sample_type": "subframe"},
        lambda p: p.sample_type == "subframe",
        BOTH,
    ),
    ("sample-type-full", {"sample_type": "full"}, lambda p: p.sample_type == "full", BOTH),
    (
        "sample-type-downsampled",
        {"sample_type": "downsampled"},
        lambda p: p.sample_type == "downsampled",
        BOTH,
    ),
    ("site-min", {"site_min": 81}, lambda p: _site(p, 81, 10**6), BOTH),
    ("site-max", {"site_max": 81}, lambda p: _site(p, 0, 81), BOTH),
    ("site-range", {"site_min": 81, "site_max": 81}, lambda p: _site(p, 81, 81), BOTH),
    (
        "site-and-drive-from",
        {"site": 81, "drive_from": 110},
        lambda p: _drive(p, 81, 110, 10**6),
        BOTH,
    ),
    (
        "site-and-drive-to",
        {"site": 81, "drive_to": 110},
        lambda p: _drive(p, 81, 0, 110),
        BOTH,
    ),
    (
        "site-and-drive-range",
        {"site": 81, "drive_from": 100, "drive_to": 120},
        lambda p: _drive(p, 81, 100, 120),
        BOTH,
    ),
    (
        "site-and-exact-drive",
        {"site": 80, "drive_from": 100, "drive_to": 100},
        lambda p: _drive(p, 80, 100, 100),
        BOTH,
    ),
]
COMBO_CASES = [
    pytest.param(rover, endpoint, params, pred, id=f"{rover}-{endpoint}-{case_id}")
    for case_id, params, pred, rovers in COMBOS
    for rover in rovers
    for endpoint in ENDS
]


def _combo_target(rover: str, endpoint: str) -> tuple[str, dict[str, str]]:
    if endpoint == "photos":
        return "/photos", {"rovers": rover}
    return f"/rovers/{rover}/photos", {}


@pytest.mark.parametrize("rover, endpoint, params, pred", COMBO_CASES)
async def test_near_combined_with_filter_returns_exact_set(
    client: httpx.AsyncClient,
    combo_world: dict[str, int],
    rover: str,
    endpoint: str,
    params: dict[str, Any],
    pred: Pred,
) -> None:
    """`near` AND the filter: exactly the bound photos that match, `total` equals the item count."""
    lat, lon, _ = COMBO_ROVERS[rover]
    path, extra = _combo_target(rover, endpoint)
    near = f"{lat},{lon}"
    near_only = await found_set(client, combo_world, path, near=near, **extra)
    assert near_only == {f"{rover[0]}-{n}" for n in NEAR_SET}
    expected = {f"{rover[0]}-{p.name}" for p in COMBO_PHOTOS if p.name in NEAR_SET and pred(p)}
    assert expected, "the case must select something"
    assert expected < near_only, "the case must exclude something of the near set"
    got = await found_set(client, combo_world, path, near=near, **params, **extra)
    assert got == expected
    # Without `near` the filter finds a superset: `near` only narrows, it never adds photos.
    without_near = await found_set(client, combo_world, path, **params, **extra)
    assert expected <= without_near


# --- near equals the card ---------------------------------------------------------------------


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 3389.5 * math.asin(math.sqrt(a))


CARD_QUERIES = [
    (P, "18.0,77.0", [0.5, 5.0, 15.0, 30.0]),
    (P, "18.1,77.0", [0.5, 5.0, 15.0, 30.0]),
    (P, "18.2,77.0", [0.5, 5.0, 15.0, 30.0]),
    (P, "18.05,77.0", [3.0, 8.0, 15.0, 40.0]),
    (C, "-4.0,137.0", [0.3, 0.9, 2.0, 4.0]),
    (C, "-4.0,137.02", [0.3, 0.9, 1.5, 4.0]),
    (C, "-4.03,137.0", [0.3, 1.0, 2.5, 4.0]),
]


@pytest.mark.parametrize(
    "rover, point, radii", CARD_QUERIES, ids=[f"{r}-{p}" for r, p, _ in CARD_QUERIES]
)
@pytest.mark.parametrize("endpoint", ENDS)
async def test_near_equals_card_location_within_radius(
    client: httpx.AsyncClient,
    world: dict[str, int],
    rover: str,
    point: str,
    radii: list[float],
    endpoint: str,
) -> None:
    """The set found at each radius equals the photos whose card `location` is within it."""
    lat, lon = (float(v) for v in point.split(","))
    distances: dict[str, float | None] = {}
    for photo_id, source_id in _by_id(world).items():
        card = await client.get(f"{V2}/photos/{photo_id}")
        assert card.status_code == 200, card.text
        body = card.json()
        if body["rover"] != rover:
            continue
        location = body["location"]
        distances[source_id] = (
            None if location is None else _haversine_km(lat, lon, location["lat"], location["lon"])
        )
    assert distances
    path, extra = _combo_target(rover, endpoint)
    for radius in radii:
        # A stop distance within 3 m of the radius would make the expectation fragile.
        assert all(d is None or abs(d - radius) > 0.003 for d in distances.values()), radius
        expected = {s for s, d in distances.items() if d is not None and d <= radius}
        got = await found_set(client, world, path, near=point, radius_km=radius, **extra)
        assert got == expected, f"radius {radius}"
