"""HTTP behaviour of /rovers/{rover}/location and /traverse (docs/API.md "Кэширование", stage 11).

`Cache-Control: public, max-age=3600` (data changes at most once a day), ETag with `304` on
`If-None-Match`, HEAD without a body, gzip for large bodies, `no-store` on errors; the
per-IP rate limit applies like on every other v2 path.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from tests._locations import (
    Factory,
    insert_dataset,
    insert_traverse,
    insert_waypoints,
    utc,
    waypoint,
)
from tests.api.v2._locations_data import seed_locations

pytestmark = pytest.mark.api

V2 = "/api/v2"
LOCATION = f"{V2}/rovers/perseverance/location"
TRAVERSE = f"{V2}/rovers/perseverance/traverse"
URLS = [
    pytest.param(LOCATION, id="location"),
    pytest.param(TRAVERSE, id="traverse"),
    pytest.param(f"{V2}/rovers/curiosity/location", id="location-curiosity"),
    pytest.param(f"{V2}/rovers/spirit/location", id="location-spirit"),
    pytest.param(f"{V2}/rovers/opportunity/traverse", id="traverse-opportunity"),
]
CACHE = "public, max-age=3600"


@pytest.fixture
async def seeded(clean_db: Factory) -> Factory:
    await seed_locations(clean_db)
    return clean_db


@pytest.fixture
async def big(clean_db: Factory) -> Factory:
    """Bodies well over 1 KB so that gzip applies."""
    await insert_waypoints(
        clean_db,
        "perseverance",
        [waypoint(sol, 3, sol, 18.0 + sol / 1000, 77.0) for sol in range(80)],
    )
    await insert_traverse(
        clean_db,
        "perseverance",
        [
            {
                "sol": 5,
                "length_m": 1.5,
                "coordinates": [[77.0 + i / 10000, 18.0 + i / 10000] for i in range(200)],
            }
        ],
    )
    for kind in ("waypoints", "traverse"):
        await insert_dataset(
            clean_db,
            "perseverance",
            kind,
            changed_at=utc(2026, 10, 28, 15, 5, 6),
            succeeded_at=utc(2026, 10, 28, 15, 5, 6),
            items=80,
            max_sol=79,
        )
    return clean_db


@pytest.mark.parametrize("url", URLS)
async def test_location_endpoints_have_one_hour_cache_control_and_an_etag(
    client: httpx.AsyncClient, seeded: Factory, url: str
) -> None:
    response = await client.get(url)

    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE
    assert response.headers["etag"]


@pytest.mark.parametrize("url", URLS)
async def test_location_endpoints_answer_304_on_a_matching_if_none_match(
    client: httpx.AsyncClient, seeded: Factory, url: str
) -> None:
    first = await client.get(url)
    etag = first.headers["etag"]

    for header in (etag, f"W/{etag.removeprefix('W/')}", f'"other", {etag}', "*"):
        again = await client.get(url, headers={"If-None-Match": header})

        assert again.status_code == 304, header
        assert again.content == b""
        assert again.headers["etag"] == etag
        assert again.headers["cache-control"] == CACHE


@pytest.mark.parametrize("url", [LOCATION, TRAVERSE])
async def test_location_etag_is_stable_and_changes_with_the_data(
    client: httpx.AsyncClient, seeded: Factory, url: str
) -> None:
    first = await client.get(url)
    second = await client.get(url)
    assert first.headers["etag"] == second.headers["etag"]

    await insert_waypoints(seeded, "perseverance", [waypoint(31, 6, 0, 18.7, 77.7)])
    await insert_traverse(
        seeded, "perseverance", [{"seq": 9, "coordinates": [[77.0, 18.0], [77.1, 18.1]]}]
    )
    changed = await client.get(url, headers={"If-None-Match": first.headers["etag"]})

    assert changed.status_code == 200
    assert changed.headers["etag"] != first.headers["etag"]


@pytest.mark.parametrize("url", URLS)
async def test_location_endpoints_support_head_without_a_body(
    client: httpx.AsyncClient, seeded: Factory, url: str
) -> None:
    get = await client.get(url)

    head = await client.head(url)

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["cache-control"] == CACHE
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-type"] == get.headers["content-type"]


async def test_traverse_head_keeps_the_geo_json_content_type(
    client: httpx.AsyncClient, seeded: Factory
) -> None:
    head = await client.head(TRAVERSE)

    assert head.headers["content-type"].split(";")[0] == "application/geo+json"


@pytest.mark.parametrize(
    ("url", "status"),
    [
        pytest.param(f"{V2}/rovers/pluto/location", 404, id="location-404"),
        pytest.param(f"{V2}/rovers/pluto/traverse", 404, id="traverse-404"),
        pytest.param(f"{LOCATION}?sol=abc", 422, id="location-422-word"),
        pytest.param(f"{LOCATION}?sol=1&from_sol=1", 422, id="location-422-conflict"),
    ],
)
async def test_location_errors_are_not_cached(
    client: httpx.AsyncClient, seeded: Factory, url: str, status: int
) -> None:
    response = await client.get(url)

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


@pytest.mark.parametrize("url", [LOCATION, TRAVERSE])
async def test_location_large_bodies_are_gzipped_when_the_client_accepts_it(
    client: httpx.AsyncClient, big: Factory, url: str
) -> None:
    plain = await client.get(url, headers={"Accept-Encoding": "identity"})
    zipped = await client.get(url, headers={"Accept-Encoding": "gzip"})

    assert len(plain.content) > 1000
    assert "content-encoding" not in plain.headers
    assert zipped.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in zipped.headers.get("vary", "").lower()
    assert zipped.content == plain.content  # httpx decodes gzip transparently
    assert zipped.headers["cache-control"] == CACHE
    assert zipped.headers["etag"] == plain.headers["etag"]


# --- rate limit --------------------------------------------------------------------------------


@asynccontextmanager
async def limited_app(settings: Settings, per_minute: int) -> AsyncIterator[FastAPI]:
    app = create_app(
        settings.model_copy(
            update={
                "rate_limit_enabled": True,
                "rate_limit_per_minute": per_minute,
                "rate_limit_per_hour": 1000,
            }
        )
    )
    try:
        async with app.router.lifespan_context(app):
            yield app
    finally:
        await app.state.engine.dispose()


@pytest.mark.parametrize("url", [LOCATION, TRAVERSE], ids=["location", "traverse"])
async def test_location_endpoints_are_rate_limited_per_ip(
    settings: Settings, migrated_db: str, url: str
) -> None:
    async with limited_app(settings, per_minute=2) as app:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.77", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = [(await client.get(url)).status_code for _ in range(2)]
            blocked = await client.get(url)
            head = await client.head(url)

    assert first == [200, 200]
    assert blocked.status_code == 429
    assert blocked.json() == {
        "error": {"code": "rate_limited", "message": "Rate limit exceeded", "details": None}
    }
    assert blocked.headers["cache-control"] == "no-store"
    assert int(blocked.headers["retry-after"]) >= 1
    assert head.status_code == 429


async def test_location_and_traverse_share_the_same_ip_budget(
    settings: Settings, migrated_db: str
) -> None:
    async with limited_app(settings, per_minute=2) as app:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.78", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            codes = [
                (await client.get(LOCATION)).status_code,
                (await client.get(TRAVERSE)).status_code,
                (await client.get(LOCATION)).status_code,
            ]

    assert codes == [200, 200, 429]
