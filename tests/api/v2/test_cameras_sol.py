"""GET /api/v2/rovers/{rover}/cameras?sol=N (docs/API.md, Camera and /cameras).

With `sol` every camera of the rover is listed (sorted by `code`) and `total_photos` is the
number of the camera's photos of that sol (all sample types); cameras without photos that sol
have `0`; a sol without photos gives all zeros. Without `sol` the totals cover the whole
mission. Data: the shared `v2_world`.
"""

from typing import Any

import httpx
import pytest

from tests.api.v2.conftest import (
    CAMERA_KEYS,
    CAMERAS_COUNT,
    HUGE,
    INT32_MAX,
    V2,
    World,
    assert_json,
    assert_validation_error,
)

pytestmark = pytest.mark.api


def cameras_url(rover: str) -> str:
    return f"{V2}/rovers/{rover}/cameras"


def expected_items(world: World, rover: str, counts: dict[str, int]) -> dict[str, Any]:
    return {
        "items": [
            {"code": code, "name": name, "total_photos": counts.get(code, 0)}
            for code, name in sorted(world.cameras[rover].items())
        ]
    }


@pytest.mark.parametrize(
    ("rover", "sol", "counts"),
    [
        ("perseverance", "2031", {"NAVCAM_LEFT": 2, "NAVCAM_RIGHT": 2, "MCZ_LEFT": 1}),
        ("perseverance", "2030", {"NAVCAM_LEFT": 1, "SUPERCAM_RMI": 1}),
        (
            "curiosity",
            "1",
            {"FHAZ": 1, "FHAZ_LEFT_B": 2, "MAST_LEFT": 2, "CHEMCAM_RMI": 1},
        ),
        ("curiosity", "5", {"MAHLI": 1}),
        ("curiosity", "20", {"FHAZ": 1}),  # only a subframe: all sample types are counted
        ("opportunity", "10", {"PANCAM": 30}),
    ],
    ids=[
        "perseverance-2031",
        "perseverance-2030",
        "curiosity-1",
        "curiosity-5",
        "curiosity-20-subframe-only",
        "opportunity-10",
    ],
)
async def test_cameras_with_sol_return_all_cameras_with_counts_of_that_sol(
    client: httpx.AsyncClient, world: World, rover: str, sol: str, counts: dict[str, int]
) -> None:
    response = await client.get(cameras_url(rover), params={"sol": sol})

    assert response.status_code == 200
    body = response.json()
    assert_json(body, expected_items(world, rover, counts))
    assert len(body["items"]) == CAMERAS_COUNT[rover]  # cameras without photos are listed
    for camera in body["items"]:
        assert list(camera) == CAMERA_KEYS


@pytest.mark.parametrize(
    ("rover", "sol"),
    [
        ("perseverance", "0"),
        ("perseverance", "2032"),  # above the last sol
        ("curiosity", "7"),  # between sols 5 and 10
        ("curiosity", "0"),
        ("curiosity", str(INT32_MAX)),
        ("opportunity", "11"),
        ("spirit", "5"),  # a rover without data
    ],
    ids=[
        "perseverance-zero",
        "perseverance-after-last",
        "curiosity-gap",
        "curiosity-sol-0",
        "int32-max",
        "opportunity-other-sol",
        "spirit-no-data",
    ],
)
async def test_cameras_with_sol_without_photos_return_all_zeros(
    client: httpx.AsyncClient, world: World, rover: str, sol: str
) -> None:
    response = await client.get(cameras_url(rover), params={"sol": sol})

    assert response.status_code == 200
    assert_json(response.json(), expected_items(world, rover, {}))


async def test_cameras_without_sol_are_unchanged_whole_mission_totals(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(cameras_url("curiosity"))

    totals = {camera["code"]: camera["total_photos"] for camera in response.json()["items"]}
    assert totals == {
        "CHEMCAM": 0,
        "CHEMCAM_RMI": 1,
        "FHAZ": 2,
        "FHAZ_LEFT_B": 2,
        "MAHLI": 1,
        "MARDI": 0,
        "MAST": 0,
        "MAST_LEFT": 2,
        "NAVCAM": 1,
        "RHAZ": 0,
    }


async def test_cameras_sol_counts_sum_to_sol_total_in_photos_and_manifest(
    client: httpx.AsyncClient, world: World
) -> None:
    rover = "curiosity"
    for sol in (1, 5, 10, 20):
        cameras = (await client.get(cameras_url(rover), params={"sol": str(sol)})).json()
        listing = (
            await client.get(
                f"{V2}/rovers/{rover}/photos", params={"sol": str(sol), "per_page": "1"}
            )
        ).json()
        assert sum(c["total_photos"] for c in cameras["items"]) == listing["total"], sol


async def test_cameras_with_sol_sums_over_all_sols_equal_mission_totals(
    client: httpx.AsyncClient, world: World
) -> None:
    whole = {
        c["code"]: c["total_photos"]
        for c in (await client.get(cameras_url("perseverance"))).json()["items"]
    }
    summed: dict[str, int] = dict.fromkeys(whole, 0)
    for sol in (2030, 2031):
        body = (await client.get(cameras_url("perseverance"), params={"sol": str(sol)})).json()
        for camera in body["items"]:
            summed[camera["code"]] += camera["total_photos"]

    assert summed == whole


async def test_cameras_with_sol_slug_is_case_insensitive(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(cameras_url("PERSEVERANCE"), params={"sol": "2030"})

    assert response.status_code == 200
    assert_json(
        response.json(),
        expected_items(world, "perseverance", {"NAVCAM_LEFT": 1, "SUPERCAM_RMI": 1}),
    )


@pytest.mark.parametrize(
    "value",
    ["-1", "2147483648", "abc", "1.5", "+1", "1_0", " 1", "1.0", "1e3", HUGE],
    ids=[
        "negative",
        "int32-max-plus-1",
        "word",
        "decimal",
        "plus-sign",
        "underscore",
        "space",
        "decimal-zero",
        "exponent",
        "huge",
    ],
)
async def test_cameras_invalid_sol_returns_422_naming_the_field(
    client: httpx.AsyncClient, world: World, value: str
) -> None:
    response = await client.get(cameras_url("curiosity"), params={"sol": value})

    assert_validation_error(response, "sol")


async def test_cameras_unknown_rover_with_sol_returns_404(
    client: httpx.AsyncClient, world: World
) -> None:
    response = await client.get(cameras_url("pluto"), params={"sol": "1"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "rover_not_found"


async def test_cameras_etag_differs_between_sols(client: httpx.AsyncClient, world: World) -> None:
    first = await client.get(cameras_url("curiosity"), params={"sol": "1"})
    second = await client.get(cameras_url("curiosity"), params={"sol": "5"})
    whole = await client.get(cameras_url("curiosity"))

    assert len({first.headers["etag"], second.headers["etag"], whole.headers["etag"]}) == 3
