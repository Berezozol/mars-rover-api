"""Ingenuity is invisible in API v1 (docs/proposals/stage-12-ingenuity.md, A1).

v1 stays byte-for-byte what it was: `/rovers` lists the same four rovers (with the same
bytes) even when Ingenuity has loaded photos; Ingenuity is an unknown rover for every
rover-scoped endpoint; a photo of the helicopter is a hidden photo (404) in `/photos/{id}`.
"""

import httpx
import pytest
from sqlalchemy import text

from app.services.legacy import LEGACY_ROVERS
from tests.api.legacy.conftest import ROVER_KEYS, Factory, World, seed
from tests.api.legacy.test_common import ROVER_ENDPOINT_IDS, ROVER_ENDPOINTS
from tests.api.legacy.test_photo_detail import NOT_FOUND

pytestmark = pytest.mark.api

ROVER_NAME_ORDER = ["curiosity", "opportunity", "perseverance", "spirit"]
INVALID_ROVER = {"errors": "Invalid Rover Name"}


def test_legacy_rovers_are_the_four_original_slugs() -> None:
    assert isinstance(LEGACY_ROVERS, frozenset)
    assert {"curiosity", "opportunity", "perseverance", "spirit"} == LEGACY_ROVERS


@pytest.fixture
async def with_ingenuity(world: World, clean_db: Factory, make_record) -> list[int]:
    """Ingenuity photos (sols with and without flights) on top of the usual world."""
    records = [
        make_record(source_id="h-58-nav", sol=58, camera_code="HELI_NAV"),
        make_record(source_id="h-58-rte", sol=58, camera_code="HELI_RTE"),
        make_record(source_id="h-1069-nav", sol=1069, camera_code="HELI_NAV"),
    ]
    await seed(clean_db, "ingenuity", records, one_by_one=True)
    async with clean_db() as session:
        rows = await session.execute(text("SELECT id FROM photos WHERE rover_id = 'ingenuity'"))
        return sorted(rows.scalars())


async def test_rovers_list_has_four_rovers_without_ingenuity(
    client: httpx.AsyncClient, prefix: str, world: World, with_ingenuity: list[int]
) -> None:
    response = await client.get(f"{prefix}/rovers")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["rovers"]
    assert [r["name"] for r in body["rovers"]] == [
        "Curiosity",
        "Opportunity",
        "Perseverance",
        "Spirit",
    ]
    assert body == {"rovers": [world.rover(rover) for rover in ROVER_NAME_ORDER]}
    assert all(list(r) == ROVER_KEYS for r in body["rovers"])
    assert 9 not in [r["id"] for r in body["rovers"]]


async def test_rovers_list_bytes_do_not_change_when_ingenuity_data_appears(
    client: httpx.AsyncClient, prefix: str, world: World, clean_db: Factory, make_record
) -> None:
    before = await client.get(f"{prefix}/rovers")

    await seed(
        clean_db,
        "ingenuity",
        [make_record(source_id="h-bytes", sol=58, camera_code="HELI_NAV")],
    )
    after = await client.get(f"{prefix}/rovers")

    assert before.status_code == after.status_code == 200
    assert before.content == after.content
    assert b"ngenuity" not in after.content and b"HELI" not in after.content


async def test_rovers_list_with_empty_database_still_has_four_rovers(
    client: httpx.AsyncClient, prefix: str, clean_db: Factory
) -> None:
    body = (await client.get(f"{prefix}/rovers")).json()

    assert [r["name"] for r in body["rovers"]] == [
        "Curiosity",
        "Opportunity",
        "Perseverance",
        "Spirit",
    ]


@pytest.mark.parametrize(("template", "params"), ROVER_ENDPOINTS, ids=ROVER_ENDPOINT_IDS)
@pytest.mark.parametrize("name", ["ingenuity", "Ingenuity", "INGENUITY"])
async def test_ingenuity_is_an_unknown_rover_in_every_rover_endpoint(
    client: httpx.AsyncClient,
    prefix: str,
    with_ingenuity: list[int],
    template: str,
    params: dict[str, str],
    name: str,
) -> None:
    response = await client.get(prefix + template.format(rover=name), params=params)

    assert response.status_code == 400
    assert response.json() == INVALID_ROVER


async def test_ingenuity_photos_with_a_flight_sol_are_not_listed(
    client: httpx.AsyncClient, prefix: str, with_ingenuity: list[int]
) -> None:
    response = await client.get(f"{prefix}/rovers/ingenuity/photos", params={"sol": "58"})

    assert response.status_code == 400
    assert response.json() == INVALID_ROVER


async def test_photo_of_ingenuity_by_id_returns_404_like_a_hidden_photo(
    client: httpx.AsyncClient, prefix: str, with_ingenuity: list[int]
) -> None:
    assert with_ingenuity
    for photo_id in with_ingenuity:
        response = await client.get(f"{prefix}/photos/{photo_id}")

        assert response.status_code == 404, photo_id
        assert response.json() == NOT_FOUND


async def test_photo_of_ingenuity_returns_the_same_404_body_as_a_missing_photo(
    client: httpx.AsyncClient, prefix: str, world: World, with_ingenuity: list[int]
) -> None:
    missing = await client.get(f"{prefix}/photos/{max(world.photo_ids.values()) + 1000}")
    hidden = await client.get(f"{prefix}/photos/{with_ingenuity[0]}")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.content == missing.content


async def test_other_rovers_are_unaffected_by_ingenuity_data(
    client: httpx.AsyncClient, prefix: str, world: World, with_ingenuity: list[int]
) -> None:
    body = (await client.get(f"{prefix}/rovers/curiosity/latest_photos")).json()
    manifest = (await client.get(f"{prefix}/manifests/perseverance")).json()

    assert body == {"latest_photos": world.photos(["c829-mast-l"])}
    assert manifest["photo_manifest"]["total_photos"] == 2
