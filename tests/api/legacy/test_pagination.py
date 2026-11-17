"""Pagination of /photos and /latest_photos (docs/LEGACY-API.md, rule 5 and deviations).

Without `page` everything is returned; with `page` 25 per page; `per_page` changes the size only
together with `page`, is capped at 200; page < 1 -> 1, per_page < 1 -> 25; non-integers -> 400.
"""

import httpx
import pytest

from tests.api.legacy.conftest import PAGED_COUNT, PAGED_SOL, World, paged_ids

pytestmark = pytest.mark.api

ENDPOINTS = {
    "photos": ("/rovers/curiosity/photos", {"sol": str(PAGED_SOL)}),
    "latest_photos": ("/rovers/curiosity/latest_photos", {}),
}


@pytest.fixture(params=list(ENDPOINTS))
def endpoint(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def fetch_ids(
    client: httpx.AsyncClient, prefix: str, endpoint: str, **params: str
) -> list[int]:
    path, base_params = ENDPOINTS[endpoint]
    response = await client.get(f"{prefix}{path}", params={**base_params, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == [endpoint]
    return [photo["id"] for photo in body[endpoint]]


@pytest.mark.parametrize(
    ("params", "window"),
    [
        ({}, slice(0, PAGED_COUNT)),
        ({"per_page": "30"}, slice(0, PAGED_COUNT)),
        ({"page": "1"}, slice(0, 25)),
        ({"page": "2"}, slice(25, 50)),
        ({"page": "1", "per_page": "30"}, slice(0, 30)),
        ({"page": "2", "per_page": "30"}, slice(30, 60)),
        ({"page": "9"}, slice(200, 205)),
        ({"page": "7", "per_page": "30"}, slice(180, 205)),
        ({"page": "10"}, slice(0, 0)),
        ({"page": "1", "per_page": "200"}, slice(0, 200)),
        ({"page": "1", "per_page": "201"}, slice(0, 200)),
        ({"page": "1", "per_page": "1000"}, slice(0, 200)),
        ({"page": "2", "per_page": "1000"}, slice(200, 205)),
        ({"page": "0"}, slice(0, 25)),
        ({"page": "-3"}, slice(0, 25)),
        ({"page": "1", "per_page": "0"}, slice(0, 25)),
        ({"page": "2", "per_page": "-5"}, slice(25, 50)),
    ],
    ids=[
        "no-page-all",
        "per-page-without-page-all",
        "page-1-default-25",
        "page-2-default-25",
        "per-page-30",
        "per-page-30-page-2",
        "last-page-remainder",
        "per-page-30-last-page-remainder",
        "page-after-end-empty",
        "per-page-200",
        "per-page-201-capped",
        "per-page-1000-capped",
        "capped-page-2",
        "page-0-is-page-1",
        "negative-page-is-page-1",
        "per-page-0-is-25",
        "negative-per-page-is-25",
    ],
)
async def test_pagination_window(
    client: httpx.AsyncClient,
    prefix: str,
    paged_world: World,
    endpoint: str,
    params: dict[str, str],
    window: slice,
) -> None:
    ids = await fetch_ids(client, prefix, endpoint, **params)

    assert ids == paged_ids(paged_world)[window]


async def test_pagination_page_returns_exact_photo_objects(
    client: httpx.AsyncClient, prefix: str, paged_world: World
) -> None:
    response = await client.get(
        f"{prefix}/rovers/curiosity/photos",
        params={"sol": str(PAGED_SOL), "page": "3", "per_page": "2"},
    )

    assert response.status_code == 200
    by_id = {photo_id: source_id for source_id, photo_id in paged_world.photo_ids.items()}
    expected = [by_id[photo_id] for photo_id in paged_ids(paged_world)[4:6]]
    assert response.json() == {"photos": paged_world.photos(expected)}


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"page": "abc"}, "Invalid page"),
        ({"page": "1.5"}, "Invalid page"),
        ({"page": "1", "per_page": "abc"}, "Invalid per_page"),
        ({"page": "1", "per_page": "2.5"}, "Invalid per_page"),
    ],
    ids=["page-text", "page-float", "per-page-text", "per-page-float"],
)
async def test_pagination_non_integer_returns_400(
    client: httpx.AsyncClient,
    prefix: str,
    paged_world: World,
    endpoint: str,
    params: dict[str, str],
    message: str,
) -> None:
    path, base_params = ENDPOINTS[endpoint]
    response = await client.get(f"{prefix}{path}", params={**base_params, **params})

    assert response.status_code == 400
    assert response.json() == {"errors": message}
