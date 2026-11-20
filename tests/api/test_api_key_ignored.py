"""`api_key` is accepted and ignored on every v1 and v2 endpoint (docs/API.md, general rules).

For a representative set of endpoints the response with `?api_key=...` must have the same status,
content type and body as the response without it, and the key must not leak into the body.
"""

import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

Factory = async_sessionmaker[AsyncSession]

KEYS = ["DEMO_KEY", "some-other-key", ""]


@pytest.fixture
async def photo_id(clean_db: Factory, make_record: Callable[..., Any]) -> int:
    records = [
        make_record(source_id="key-1", sol=1, camera_code="FHAZ"),
        make_record(source_id="key-2", sol=1, camera_code="MAHLI"),
        make_record(source_id="key-3", sol=2, camera_code="FHAZ"),
    ]
    async with clean_db() as session:
        await upsert_photos(session, "curiosity", records)
        await recompute_sol_summaries(session, "curiosity", {1, 2})
        await refresh_rover_stats(session, "curiosity")
        await session.commit()
    async with clean_db() as session:
        value = await session.scalar(text("SELECT min(id) FROM photos"))
    assert value is not None
    return int(value)


def _paths(photo_id: int) -> list[Any]:
    return [
        pytest.param("/api/v1/rovers", id="v1-rovers"),
        pytest.param("/mars-photos/api/v1/rovers", id="v1-old-prefix-rovers"),
        pytest.param("/api/v1/rovers/curiosity", id="v1-rover"),
        pytest.param("/api/v1/rovers/curiosity/photos?sol=1", id="v1-photos"),
        pytest.param("/api/v1/rovers/curiosity/photos?sol=1&camera=fhaz", id="v1-photos-camera"),
        pytest.param("/api/v1/rovers/curiosity/latest_photos", id="v1-latest"),
        pytest.param("/api/v1/manifests/curiosity", id="v1-manifest"),
        pytest.param(f"/api/v1/photos/{photo_id}", id="v1-photo-by-id"),
        pytest.param("/api/v1/rovers/nope", id="v1-unknown-rover-404"),
        pytest.param("/api/v2/rovers", id="v2-rovers"),
        pytest.param("/api/v2/rovers/curiosity", id="v2-rover"),
        pytest.param("/api/v2/rovers/curiosity/cameras", id="v2-cameras"),
        pytest.param("/api/v2/rovers/curiosity/photos?sol=1", id="v2-photos"),
        pytest.param("/api/v2/rovers/curiosity/photos/latest", id="v2-latest"),
        pytest.param("/api/v2/rovers/curiosity/manifest", id="v2-manifest"),
        pytest.param(f"/api/v2/photos/{photo_id}", id="v2-photo-by-id"),
        pytest.param("/api/v2/rovers/curiosity/location", id="v2-location"),
        pytest.param("/api/v2/rovers/perseverance/location?latest=true", id="v2-location-latest"),
        pytest.param("/api/v2/rovers/perseverance/traverse", id="v2-traverse"),
        pytest.param("/api/v2/rovers/ingenuity/flights", id="v2-flights"),
        pytest.param("/api/v2/rovers/ingenuity/flights/1", id="v2-flight"),
        pytest.param("/api/v2/rovers/perseverance/samples", id="v2-samples"),
        pytest.param("/api/v2/rovers/curiosity/feed.xml", id="v2-feed"),
        pytest.param("/api/v2/rovers/nope/photos", id="v2-unknown-rover-404"),
        pytest.param("/api/v2/rovers/curiosity/photos?sol=abc", id="v2-validation-422"),
    ]


def _with_key(path: str, key: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}api_key={key}"


def _normalize(body: str) -> str:
    """Drop the build timestamp of the RSS feed (it is the only time-dependent part)."""
    return re.sub(r"<lastBuildDate>[^<]*</lastBuildDate>", "<lastBuildDate/>", body)


@pytest.mark.parametrize("key", KEYS, ids=["demo-key", "other-key", "empty-key"])
async def test_api_key_does_not_change_the_response(
    client: httpx.AsyncClient, photo_id: int, key: str
) -> None:
    for param in _paths(photo_id):
        path = param.values[0]
        plain = await client.get(path)
        keyed = await client.get(_with_key(path, key))

        assert keyed.status_code == plain.status_code, path
        assert keyed.headers["content-type"] == plain.headers["content-type"], path
        assert _normalize(keyed.text) == _normalize(plain.text), path
        if key:
            assert key not in keyed.text, f"{path}: the key leaked into the body"


async def test_api_key_in_the_data_is_really_there_sanity(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    """Guard against a vacuous comparison: the seeded endpoints return real data."""
    body = (await client.get("/api/v1/rovers/curiosity/photos?sol=1&api_key=DEMO_KEY")).json()

    assert [photo["sol"] for photo in body["photos"]] == [1, 1]
    detail = await client.get(f"/api/v2/photos/{photo_id}?api_key=DEMO_KEY")
    assert detail.status_code == 200
    assert detail.json()["id"] == photo_id


async def test_api_key_may_be_repeated_and_mixed_with_real_parameters(
    client: httpx.AsyncClient, photo_id: int
) -> None:
    plain = await client.get("/api/v1/rovers/curiosity/photos?sol=1&camera=mahli&page=1")
    keyed = await client.get(
        "/api/v1/rovers/curiosity/photos?api_key=A&sol=1&api_key=B&camera=mahli&page=1"
    )

    assert keyed.status_code == plain.status_code == 200
    assert keyed.json() == plain.json()
    assert len(keyed.json()["photos"]) == 1
