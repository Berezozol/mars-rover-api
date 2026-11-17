"""GET /photos/{id} (docs/LEGACY-API.md, paths and deviations table)."""

import httpx
import pytest

from tests.api.legacy.conftest import PHOTO_KEYS, World

pytestmark = pytest.mark.api

NOT_FOUND = {"errors": "Photo not found"}


@pytest.mark.parametrize("source_id", ["c1-fhaz-lb", "c829-mast-l", "p-left", "p-right", "s-fhaz"])
async def test_photo_by_id_returns_exact_photo(
    client: httpx.AsyncClient, prefix: str, world: World, source_id: str
) -> None:
    response = await client.get(f"{prefix}/photos/{world.photo_ids[source_id]}")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["photo"]
    assert list(body["photo"]) == PHOTO_KEYS
    assert body == {"photo": world.photo(source_id)}


@pytest.mark.parametrize(
    "source_id", ["c1-fhaz-lb-sub", "c1-mast-l-down", "c900-nav-thumb", "p-left-sub"]
)
async def test_photo_by_id_non_full_photo_returns_404(
    client: httpx.AsyncClient, prefix: str, world: World, source_id: str
) -> None:
    response = await client.get(f"{prefix}/photos/{world.photo_ids[source_id]}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


async def test_photo_by_id_missing_returns_404(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    missing = max(world.photo_ids.values()) + 1000

    response = await client.get(f"{prefix}/photos/{missing}")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("photo_id", ["abc", "1.5"])
async def test_photo_by_id_non_integer_returns_400_invalid_id(
    client: httpx.AsyncClient, prefix: str, world: World, photo_id: str
) -> None:
    response = await client.get(f"{prefix}/photos/{photo_id}")

    assert response.status_code == 400
    assert response.json() == {"errors": "Invalid id"}


async def test_photo_by_id_api_key_is_ignored(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    photo_id = world.photo_ids["c1-mahli"]

    response = await client.get(f"{prefix}/photos/{photo_id}", params={"api_key": "DEMO_KEY"})

    assert response.status_code == 200
    assert response.json() == {"photo": world.photo("c1-mahli")}
