"""GET /rovers/{rover}/latest_photos: sol = the rover's max sol of full photos."""

import httpx
import pytest

from tests.api.legacy.conftest import World

pytestmark = pytest.mark.api


async def test_latest_photos_returns_full_photos_of_max_full_sol(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    # Curiosity sol 900 has only hidden photos and sol 829 a hidden subframe: neither shows.
    response = await client.get(f"{prefix}/rovers/curiosity/latest_photos")

    assert response.status_code == 200
    assert response.json() == {"latest_photos": world.photos(["c829-mast-l"])}


async def test_latest_photos_perseverance_ignores_thumbnail_only_sol(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    response = await client.get(f"{prefix}/rovers/Perseverance/latest_photos")

    assert response.status_code == 200
    assert response.json() == {"latest_photos": world.photos(["p-left", "p-right"])}


@pytest.mark.parametrize(
    ("camera", "expected"),
    [("mast", ["c829-mast-l"]), ("MAST_LEFT", ["c829-mast-l"]), ("fhaz", []), ("xyz", [])],
    ids=["group", "exact", "camera-without-photos", "unknown-camera"],
)
async def test_latest_photos_camera_filter(
    client: httpx.AsyncClient, prefix: str, world: World, camera: str, expected: list[str]
) -> None:
    response = await client.get(
        f"{prefix}/rovers/curiosity/latest_photos", params={"camera": camera}
    )

    assert response.status_code == 200
    assert response.json() == {"latest_photos": world.photos(expected)}


@pytest.mark.parametrize("rover", ["opportunity", "OPPORTUNITY"])
async def test_latest_photos_rover_without_photos_returns_empty_list(
    client: httpx.AsyncClient, prefix: str, world: World, rover: str
) -> None:
    response = await client.get(f"{prefix}/rovers/{rover}/latest_photos")

    assert response.status_code == 200
    assert response.json() == {"latest_photos": []}


async def test_latest_photos_api_key_is_ignored(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    response = await client.get(
        f"{prefix}/rovers/curiosity/latest_photos", params={"api_key": "DEMO_KEY"}
    )

    assert response.status_code == 200
    assert response.json() == {"latest_photos": world.photos(["c829-mast-l"])}
