"""GET /api/v2/photos: HTTP behaviour around the list (stage 18B-1, AC-11).

Cache-Control, ETag/304, HEAD, errors with no-store, X-RateLimit-* headers, the untouched
`/photos/{id}` route and an unchanged v1.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from tests.api.v2._multi import Multi
from tests.api.v2.conftest import PHOTO_DETAIL_KEYS, V2, assert_error

pytestmark = pytest.mark.api

URL = f"{V2}/photos"
SHORT = "public, max-age=300"


def weak(etag: str) -> str:
    return etag if etag.startswith("W/") else f"W/{etag}"


# --- Cache-Control, ETag, HEAD ------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [{}, {"rovers": "curiosity,spirit", "sort": "-taken_at"}, {"sol_min": "9999"}],
    ids=["plain", "filtered", "empty"],
)
async def test_ac11_list_is_cached_for_five_minutes(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params=params)

    assert response.status_code == 200
    assert response.headers["cache-control"] == SHORT


async def test_ac11_etag_is_stable_and_differs_between_different_queries(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    first = await client.get(URL, params={"rovers": "spirit"})
    again = await client.get(URL, params={"rovers": "spirit"})
    other = await client.get(URL, params={"rovers": "opportunity"})

    assert first.headers["etag"]
    assert first.headers["etag"] == again.headers["etag"]
    assert first.headers["etag"] != other.headers["etag"]


async def test_ac11_matching_if_none_match_gets_304_without_a_body(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    first = await client.get(URL, params={"rovers": "spirit"})
    etag = first.headers["etag"]

    again = await client.get(URL, params={"rovers": "spirit"}, headers={"If-None-Match": etag})
    weak_again = await client.get(
        URL, params={"rovers": "spirit"}, headers={"If-None-Match": weak(etag)}
    )
    stale = await client.get(URL, params={"rovers": "spirit"}, headers={"If-None-Match": '"nope"'})

    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == etag
    assert again.headers["cache-control"] == SHORT
    assert weak_again.status_code == 304
    assert stale.status_code == 200


async def test_ac11_head_has_the_headers_and_no_body(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    get = await client.get(URL, params={"rovers": "spirit"})
    head = await client.head(URL, params={"rovers": "spirit"})

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["cache-control"] == SHORT
    assert head.headers["etag"] == get.headers["etag"]


@pytest.mark.parametrize(
    "params",
    [
        {"rovers": "mars"},
        {"sort": "asc"},
        {"page": "101", "per_page": "100"},
        {"sol": "1", "sol_min": "1"},
    ],
    ids=["rovers", "sort", "window", "mix"],
)
async def test_ac11_errors_are_not_cached(
    client: httpx.AsyncClient, multi_world: Multi, params: dict[str, str]
) -> None:
    response = await client.get(URL, params=params)

    assert_error(response, 422, "validation_error")
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


# --- rate-limit headers ---------------------------------------------------------------------------


@pytest.fixture
async def limited_client(settings: Settings, migrated_db: str) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(
        settings.model_copy(
            update={
                "rate_limit_enabled": True,
                "rate_limit_per_minute": 5,
                "rate_limit_per_hour": 1000,
            }
        )
    )
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    try:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.77", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    finally:
        try:
            await context.__aexit__(None, None, None)
        finally:
            await app.state.engine.dispose()


async def test_ac11_list_carries_rate_limit_headers_that_count_down(
    limited_client: httpx.AsyncClient,
) -> None:
    first = await limited_client.get(URL, params={"per_page": "1"})
    second = await limited_client.get(URL, params={"per_page": "1"})

    assert first.status_code == second.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "5"
    assert first.headers["x-ratelimit-remaining"] == "4"
    assert second.headers["x-ratelimit-remaining"] == "3"
    assert first.headers["x-ratelimit-reset"].isdigit()


async def test_ac11_list_errors_and_head_also_count_against_the_limit(
    limited_client: httpx.AsyncClient,
) -> None:
    bad = await limited_client.get(URL, params={"rovers": "mars"})
    head = await limited_client.head(URL)

    assert bad.status_code == 422 and bad.headers["x-ratelimit-remaining"] == "4"
    assert head.status_code == 200 and head.headers["x-ratelimit-remaining"] == "3"


async def test_ac11_sixth_request_in_a_minute_is_429(limited_client: httpx.AsyncClient) -> None:
    for _ in range(5):
        assert (await limited_client.get(URL, params={"per_page": "1"})).status_code == 200

    response = await limited_client.get(URL, params={"per_page": "1"})

    assert_error(response, 429, "rate_limited")
    assert response.headers["x-ratelimit-remaining"] == "0"


# --- the card route is untouched ---


async def test_ac11_photo_card_route_still_works_next_to_the_list(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    row = multi_world.select(rovers=["spirit"])[0]

    response = await client.get(f"{URL}/{row.id}")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == PHOTO_DETAIL_KEYS
    assert body["id"] == row.id
    assert body["rover"] == "spirit"
    assert response.headers["cache-control"] == "public, max-age=3600"


async def test_ac11_photo_card_unknown_id_is_still_404(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get(f"{URL}/999999999")

    assert_error(response, 404, "photo_not_found")


# --- v1 is unchanged ---


async def test_ac11_v1_ignores_the_new_v2_params(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    url = "/api/v1/rovers/curiosity/photos"
    plain = await client.get(url, params={"sol": "101"})
    noisy = await client.get(
        url,
        params={
            "sol": "101",
            "rovers": "spirit",
            "sort": "taken_at",
            "sol_min": "1",
            "date_max": "2000-01-01",
            "site_min": "5",
        },
    )

    assert plain.status_code == noisy.status_code == 200
    assert plain.json() == noisy.json()
    assert list(plain.json()) == ["photos"]
    assert plain.json()["photos"]


async def test_ac11_v1_has_no_cross_rover_list(
    client: httpx.AsyncClient, multi_world: Multi
) -> None:
    response = await client.get("/api/v1/photos", params={"sol": "101"})

    assert response.status_code == 404
