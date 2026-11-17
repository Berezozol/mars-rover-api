"""GET /manifests/{rover} (docs/LEGACY-API.md, "Manifest")."""

import httpx
import pytest

from tests.api.legacy.conftest import (
    CURIOSITY_SOL1_FULL_CODES,
    MANIFEST_KEYS,
    MANIFEST_SOL_KEYS,
    ROVERS,
    World,
)

pytestmark = pytest.mark.api


def manifest_head(rover_id: str, max_sol: int | None, max_date: str | None, total: int) -> dict:
    static = {key: ROVERS[rover_id][key] for key in ("name", "landing_date", "launch_date")}
    return {
        **static,
        "status": ROVERS[rover_id]["status"],
        "max_sol": max_sol,
        "max_date": max_date,
        "total_photos": total,
    }


@pytest.mark.parametrize("name", ["curiosity", "Curiosity", "CURIOSITY"])
async def test_manifest_curiosity_exact_with_legacy_date_vectors(
    client: httpx.AsyncClient, prefix: str, world: World, name: str
) -> None:
    response = await client.get(f"{prefix}/manifests/{name}")

    assert response.status_code == 200
    # Sol 900 has only hidden photos: not listed and not counted; sol 829 counts only
    # its full photo. Dates are the original's test vectors, not photos.earth_date.
    assert response.json() == {
        "photo_manifest": {
            **manifest_head("curiosity", 829, "2014-12-05", 15),
            "photos": [
                {
                    "sol": 1,
                    "earth_date": "2012-08-07",
                    "total_photos": 11,
                    "cameras": CURIOSITY_SOL1_FULL_CODES,
                },
                {"sol": 30, "earth_date": "2012-09-05", "total_photos": 2,
                 "cameras": ["NAV_LEFT_B"]},
                {"sol": 100, "earth_date": "2012-11-16", "total_photos": 1,
                 "cameras": ["MAHLI"]},
                {"sol": 829, "earth_date": "2014-12-05", "total_photos": 1,
                 "cameras": ["MAST_LEFT"]},
            ],
        }
    }  # fmt: skip


async def test_manifest_key_order_matches_legacy_serializer(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    body = (await client.get(f"{prefix}/manifests/curiosity")).json()

    assert list(body) == ["photo_manifest"]
    assert list(body["photo_manifest"]) == MANIFEST_KEYS
    assert all(list(entry) == MANIFEST_SOL_KEYS for entry in body["photo_manifest"]["photos"])


async def test_manifest_perseverance_skips_sols_without_full_photos(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    response = await client.get(f"{prefix}/manifests/perseverance")

    assert response.status_code == 200
    assert response.json() == {
        "photo_manifest": {
            **manifest_head("perseverance", 100, "2021-05-31", 2),
            "photos": [
                {
                    "sol": 100,
                    "earth_date": "2021-05-31",
                    "total_photos": 2,
                    "cameras": ["NAVCAM_LEFT", "NAVCAM_RIGHT"],
                }
            ],
        }
    }


async def test_manifest_rover_without_photos_has_empty_photos(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    response = await client.get(f"{prefix}/manifests/opportunity")

    assert response.status_code == 200
    assert response.json() == {
        "photo_manifest": {**manifest_head("opportunity", None, None, 0), "photos": []}
    }


async def test_manifest_api_key_is_ignored(
    client: httpx.AsyncClient, prefix: str, world: World
) -> None:
    url = f"{prefix}/manifests/spirit"
    with_key = await client.get(url, params={"api_key": "DEMO_KEY"})

    assert with_key.status_code == 200
    assert with_key.json() == (await client.get(url)).json()
    assert with_key.json()["photo_manifest"]["photos"] == [
        {"sol": 5, "earth_date": "2004-01-09", "total_photos": 2,
         "cameras": ["FHAZ", "FHAZ_LEFT_B"]},
    ]  # fmt: skip
