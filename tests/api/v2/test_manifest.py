"""GET /api/v2/rovers/{rover}/manifest (docs/API.md)."""

from typing import Any

import httpx
import pytest

from tests.api.v2.conftest import (
    HUGE,
    INT32_MAX,
    MANIFEST_KEYS,
    MANIFEST_SOL_KEYS,
    V2,
    World,
    assert_json,
    assert_validation_error,
)

pytestmark = pytest.mark.api

# Per-sol summaries over ALL sample types; earth_date is the earliest real date of the sol.
CURIOSITY_SOLS: list[dict[str, Any]] = [
    {"sol": 20, "earth_date": "2012-08-26", "total_photos": 1, "cameras": ["FHAZ"]},
    {"sol": 10, "earth_date": "2012-08-16", "total_photos": 1, "cameras": ["NAVCAM"]},
    {"sol": 5, "earth_date": "2012-08-11", "total_photos": 1, "cameras": ["MAHLI"]},
    {
        "sol": 1,
        "earth_date": "2012-08-07",
        "total_photos": 6,
        "cameras": ["CHEMCAM_RMI", "FHAZ", "FHAZ_LEFT_B", "MAST_LEFT"],
    },
]
PERSEVERANCE_SOLS: list[dict[str, Any]] = [
    {
        "sol": 2031,
        "earth_date": "2026-11-06",
        "total_photos": 5,
        "cameras": ["MCZ_LEFT", "NAVCAM_LEFT", "NAVCAM_RIGHT"],
    },
    {
        "sol": 2030,
        "earth_date": "2026-11-05",
        "total_photos": 2,
        "cameras": ["NAVCAM_LEFT", "SUPERCAM_RMI"],
    },
]


def manifest_url(rover: str) -> str:
    return f"{V2}/rovers/{rover}/manifest"


@pytest.mark.parametrize(
    ("rover", "sols"),
    [("curiosity", CURIOSITY_SOLS), ("perseverance", PERSEVERANCE_SOLS), ("spirit", [])],
    ids=["curiosity", "perseverance", "spirit-empty"],
)
async def test_manifest_returns_exact_json_all_types_sol_desc(
    client: httpx.AsyncClient, world: World, rover: str, sols: list[dict[str, Any]]
) -> None:
    response = await client.get(manifest_url(rover))

    assert response.status_code == 200
    body = response.json()
    assert list(body) == MANIFEST_KEYS
    for item in body["sols"]:
        assert list(item) == MANIFEST_SOL_KEYS
    assert_json(body, {"rover": world.rover(rover), "sols": sols})


async def test_manifest_rover_slug_case_insensitive(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(manifest_url("CuRiOsItY"))).json()

    assert body["rover"]["id"] == "curiosity"
    assert [item["sol"] for item in body["sols"]] == [20, 10, 5, 1]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"from_sol": "5"}, [20, 10, 5]),
        ({"to_sol": "5"}, [5, 1]),
        ({"from_sol": "5", "to_sol": "10"}, [10, 5]),
        ({"from_sol": "1", "to_sol": "1"}, [1]),
        ({"from_sol": "2", "to_sol": "4"}, []),
        ({"from_sol": "21"}, []),
        ({"from_sol": "0", "to_sol": str(INT32_MAX)}, [20, 10, 5, 1]),
    ],
    ids=["from", "to", "both", "equal", "gap", "above-max", "full-range"],
)
async def test_manifest_from_to_bounds_are_inclusive(
    client: httpx.AsyncClient, world: World, params: dict[str, str], expected: list[int]
) -> None:
    response = await client.get(manifest_url("curiosity"), params=params)

    assert response.status_code == 200
    body = response.json()
    assert [item["sol"] for item in body["sols"]] == expected
    assert_json(body["rover"], world.rover("curiosity"))  # the rover is never range-limited


async def test_manifest_from_greater_than_to_returns_422(
    client: httpx.AsyncClient, world: World
) -> None:
    params = {"from_sol": "10", "to_sol": "5"}
    response = await client.get(manifest_url("curiosity"), params=params)

    assert_validation_error(response, "from_sol", "to_sol")


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("from_sol", "-1"),
        ("from_sol", str(INT32_MAX + 1)),
        ("from_sol", "abc"),
        ("from_sol", HUGE),
        ("to_sol", "-1"),
        ("to_sol", str(INT32_MAX + 1)),
        ("to_sol", "1.5"),
        ("to_sol", HUGE),
    ],
    ids=lambda value: value if len(value) < 20 else f"{len(value)}-digits",
)
async def test_manifest_invalid_bound_returns_422(
    client: httpx.AsyncClient, world: World, param: str, value: str
) -> None:
    response = await client.get(manifest_url("curiosity"), params={param: value})

    assert_validation_error(response, param)


async def test_manifest_has_no_entries_for_sols_without_photos(
    client: httpx.AsyncClient, world: World
) -> None:
    """Gaps between sols 1, 5, 10 and 20 are not listed (no zero-photo entries)."""
    body = (await client.get(manifest_url("curiosity"))).json()

    assert [entry["sol"] for entry in body["sols"]] == [20, 10, 5, 1]
    assert all(entry["total_photos"] > 0 for entry in body["sols"])


async def test_manifest_rover_has_end_date_and_data_source(
    client: httpx.AsyncClient, world: World
) -> None:
    body = (await client.get(manifest_url("spirit"))).json()

    assert body["rover"]["end_date"] == "2011-05-25"
    assert body["rover"]["data_source"] == "archive"
