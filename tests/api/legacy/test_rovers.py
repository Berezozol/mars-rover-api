"""GET /rovers and /rovers/{rover} (docs/LEGACY-API.md, "Rover")."""

import httpx
import pytest

from tests.api.legacy.conftest import (
    CURIOSITY_NEW_CODES,
    ROVER_CAMERA_KEYS,
    ROVER_KEYS,
    SEED_CAMERAS,
    World,
)

pytestmark = pytest.mark.api

ROVER_NAME_ORDER = ["curiosity", "opportunity", "perseverance", "spirit"]


@pytest.mark.usefixtures("clean_db")
async def test_rovers_empty_database_returns_four_seed_rovers_by_name(
    client: httpx.AsyncClient, prefix: str
) -> None:
    response = await client.get(f"{prefix}/rovers")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["rovers"]
    assert [rover["name"] for rover in body["rovers"]] == [
        "Curiosity",
        "Opportunity",
        "Perseverance",
        "Spirit",
    ]
    for rover_id, rover in zip(ROVER_NAME_ORDER, body["rovers"], strict=True):
        assert rover["max_sol"] is None
        assert rover["max_date"] is None
        assert rover["total_photos"] == 0
        assert [camera["name"] for camera in rover["cameras"]] == SEED_CAMERAS[rover_id]


async def test_rovers_with_data_returns_exact_rover_objects(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    response = await client.get(f"{prefix}/rovers")

    assert response.status_code == 200
    assert response.json() == {"rovers": [world.rover(rover) for rover in ROVER_NAME_ORDER]}


@pytest.mark.parametrize("name", ["curiosity", "Curiosity", "CURIOSITY", "cUrIoSiTy"])
async def test_rover_name_any_case_returns_rover(
    client: httpx.AsyncClient, prefix: str, world: World, name: str
) -> None:
    response = await client.get(f"{prefix}/rovers/{name}")

    assert response.status_code == 200
    assert response.json() == {"rover": world.rover("curiosity")}


async def test_rover_key_order_and_types_match_legacy_serializer(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    rover = (await client.get(f"{prefix}/rovers/curiosity")).json()["rover"]

    assert list(rover) == ROVER_KEYS
    assert all(list(camera) == ROVER_CAMERA_KEYS for camera in rover["cameras"])
    assert isinstance(rover["id"], int)
    assert isinstance(rover["max_sol"], int)
    assert isinstance(rover["total_photos"], int)


async def test_rover_counters_count_only_full_photos(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    # Curiosity has hidden photos on sols 1, 829 and only hidden ones on sol 900.
    rover = (await client.get(f"{prefix}/rovers/curiosity")).json()["rover"]

    assert (rover["max_sol"], rover["max_date"], rover["total_photos"]) == (
        829,
        "2014-12-05",
        15,
    )


async def test_rover_cameras_seed_order_then_new_codes_with_full_names(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    rover = (await client.get(f"{prefix}/rovers/curiosity")).json()["rover"]

    assert [camera["name"] for camera in rover["cameras"]] == (
        SEED_CAMERAS["curiosity"] + CURIOSITY_NEW_CODES
    )
    assert rover["cameras"][0] == {"name": "FHAZ", "full_name": "Front Hazard Avoidance Camera"}
    assert {"name": "MAST_LEFT", "full_name": "Mast Camera - Left"} in rover["cameras"]


@pytest.mark.parametrize("rover", ["opportunity", "spirit", "perseverance"])
async def test_rover_other_rovers_return_exact_objects(
    client: httpx.AsyncClient, prefix: str, world: World, rover: str
) -> None:
    response = await client.get(f"{prefix}/rovers/{rover}")

    assert response.status_code == 200
    assert response.json() == {"rover": world.rover(rover)}
