"""PhotoDetail.location (docs/API.md; stage 11, ADR-2/ADR-3) and the unchanged lists / v1.

Rule: the waypoint of the same rover with the same `site` and the greatest `drive` not greater
than the photo's; `exact` says whether the drive matched. `null` when the photo has no `site`
or `drive`, the rover has no location source, or no such waypoint exists (never crosses the
site boundary). Photo lists in v2 and the whole of v1 stay exactly as before (byte for byte).
"""

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests._locations import LOCATION_KEYS, Factory, insert_waypoints, waypoint
from tests.api.v2.conftest import (
    PHOTO_DETAIL_KEYS,
    PHOTO_KEYS,
    V2,
)
from tests.conftest import build_record

pytestmark = pytest.mark.api

P = "perseverance"
C = "curiosity"
PERSEVERANCE_STOPS = [
    waypoint(2030, 80, 1000, 18.0, 77.0, elev_geoid=-2000.5),
    waypoint(2031, 80, 1234, 18.1, 77.1, elev_geoid=-2001.5),
    waypoint(2032, 80, 1500, 18.2, 77.2),  # no elevation
    waypoint(2033, 81, 50, 18.3, 77.3, elev_geoid=-2003.5),
]
CURIOSITY_STOPS = [waypoint(4000, 12, 300, -4.0, 137.0, elev_geoid=-4000.5)]


def stop_json(row: dict[str, Any], exact: bool) -> dict[str, Any]:
    return {
        "sol": row["sol"],
        "site": row["site"],
        "drive": row["drive"],
        "lat": row["lat"],
        "lon": row["lon"],
        "elev_geoid": row.get("elev_geoid"),
        "exact": exact,
    }


def photo(source_id: str, site: int | None, drive: int | None, *, rover: str = P) -> Any:
    taken = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    return build_record(
        source_id=source_id,
        sol=2031 if rover == P else 4000,
        camera_code="NAVCAM_LEFT" if rover == P else "FHAZ",
        sample_type="full",
        taken_at=taken,
        earth_date=date(2026, 11, 6),
        site=site,
        drive=drive,
        img_full=f"https://images.example.test/{source_id}.png",
    )


PHOTOS = [
    ("exact", P, 80, 1234),
    ("after-stop", P, 80, 1400),  # between 1234 and 1500: the stop before it, not exact
    ("past-last", P, 80, 9999),  # after the last published stop of the site
    ("before-first", P, 80, 10),  # earlier than the first stop of the site
    ("first-stop", P, 80, 1000),
    ("no-stops-site", P, 79, 5),
    ("next-site-before-first", P, 81, 49),  # site 80 has stops, but the border is not crossed
    ("next-site-exact", P, 81, 50),
    ("site-null", P, None, 1234),
    ("drive-null", P, 80, None),
    ("both-null", P, None, None),
    ("c-exact", C, 12, 300),
    ("c-after", C, 12, 301),
    ("c-other-rover-stop", C, 80, 1234),  # only Perseverance has a stop on (80, 1234)
    ("p-other-rover-stop", P, 12, 300),  # only Curiosity has a stop on (12, 300)
]


@pytest.fixture
async def world(clean_db: Factory) -> dict[str, int]:
    """Photos of both rovers plus the waypoints; returns source_id -> photo id."""
    for rover in (P, C):
        records = [photo(sid, site, drive, rover=r) for sid, r, site, drive in PHOTOS if r == rover]
        async with clean_db() as session:
            await upsert_photos(session, rover, records)
            await recompute_sol_summaries(session, rover, {r.sol for r in records})
            await refresh_rover_stats(session, rover)
            await session.commit()
    await insert_waypoints(clean_db, P, PERSEVERANCE_STOPS)
    await insert_waypoints(clean_db, C, CURIOSITY_STOPS)
    async with clean_db() as session:
        found = await session.execute(text("SELECT source_id, id FROM photos"))
        return {source_id: photo_id for source_id, photo_id in found}


async def detail(
    client: httpx.AsyncClient, world: dict[str, int], source_id: str
) -> dict[str, Any]:
    response = await client.get(f"{V2}/photos/{world[source_id]}")
    assert response.status_code == 200, response.text
    return response.json()


# --- location values ---------------------------------------------------------------------------


async def test_photo_detail_location_exact_stop(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    body = await detail(client, world, "exact")

    assert body["location"] == stop_json(PERSEVERANCE_STOPS[1], True)
    assert list(body["location"]) == LOCATION_KEYS
    assert list(body) == PHOTO_DETAIL_KEYS
    assert list(body)[-5] == "location"  # followed by local_time, in_sol, flight, samples


async def test_photo_detail_location_first_stop_of_the_site_is_exact(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    body = await detail(client, world, "first-stop")

    assert body["location"] == stop_json(PERSEVERANCE_STOPS[0], True)


@pytest.mark.parametrize(
    ("source_id", "stop"),
    [
        pytest.param("after-stop", 1, id="between-stops"),
        pytest.param("past-last", 2, id="after-the-last-stop"),
    ],
)
async def test_photo_detail_location_later_drive_falls_back_to_the_previous_stop(
    client: httpx.AsyncClient, world: dict[str, int], source_id: str, stop: int
) -> None:
    body = await detail(client, world, source_id)

    assert body["location"] == stop_json(PERSEVERANCE_STOPS[stop], False)
    assert body["location"]["exact"] is False


async def test_photo_detail_location_stop_without_elevation_has_null_elev_geoid(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    body = await detail(client, world, "past-last")

    assert "elev_geoid" in body["location"] and body["location"]["elev_geoid"] is None


async def test_photo_detail_location_stop_of_the_next_site_is_exact(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    body = await detail(client, world, "next-site-exact")

    assert body["location"] == stop_json(PERSEVERANCE_STOPS[3], True)


async def test_photo_detail_location_curiosity_exact_and_fallback(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    assert (await detail(client, world, "c-exact"))["location"] == stop_json(
        CURIOSITY_STOPS[0], True
    )
    assert (await detail(client, world, "c-after"))["location"] == stop_json(
        CURIOSITY_STOPS[0], False
    )


# --- null cases --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_id",
    [
        pytest.param("before-first", id="drive-before-the-first-stop-of-the-site"),
        pytest.param("no-stops-site", id="site-without-stops"),
        pytest.param("next-site-before-first", id="border-of-the-site-is-not-crossed"),
        pytest.param("site-null", id="site-null"),
        pytest.param("drive-null", id="drive-null"),
        pytest.param("both-null", id="site-and-drive-null"),
        pytest.param("c-other-rover-stop", id="stop-of-another-rover-curiosity"),
        pytest.param("p-other-rover-stop", id="stop-of-another-rover-perseverance"),
    ],
)
async def test_photo_detail_location_is_null_when_no_stop_applies(
    client: httpx.AsyncClient, world: dict[str, int], source_id: str
) -> None:
    body = await detail(client, world, source_id)

    assert "location" in body and body["location"] is None
    assert list(body) == PHOTO_DETAIL_KEYS


async def test_photo_detail_location_is_null_for_a_rover_without_a_source(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    for rover in ("spirit", "opportunity"):
        record = photo(f"mer-{rover}", 80, 1234, rover=P)
        async with clean_db() as session:
            await upsert_photos(session, rover, [record])
            await recompute_sol_summaries(session, rover, {record.sol})
            await refresh_rover_stats(session, rover)
            await session.commit()
    # A matching stop exists for Spirit: the rover still has no location source.
    await insert_waypoints(clean_db, "spirit", [waypoint(2031, 80, 1234, 18.1, 77.1)])
    async with clean_db() as session:
        ids = [row[0] for row in await session.execute(text("SELECT id FROM photos ORDER BY id"))]

    assert len(ids) == 2
    for photo_id in ids:
        body = (await client.get(f"{V2}/photos/{photo_id}")).json()
        assert body["location"] is None
        assert list(body) == PHOTO_DETAIL_KEYS


async def test_photo_detail_location_appears_when_the_stop_is_published_later(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    record = photo("late", 90, 40)
    async with clean_db() as session:
        await upsert_photos(session, P, [record])
        await session.commit()
        photo_id = await session.scalar(text("SELECT id FROM photos WHERE source_id = 'late'"))
    assert (await client.get(f"{V2}/photos/{photo_id}")).json()["location"] is None

    await insert_waypoints(clean_db, P, [waypoint(2031, 90, 40, 18.9, 77.9)])

    location = (await client.get(f"{V2}/photos/{photo_id}")).json()["location"]
    assert location == {
        "sol": 2031,
        "site": 90,
        "drive": 40,
        "lat": 18.9,
        "lon": 77.9,
        "elev_geoid": None,
        "exact": True,
    }


# --- lists and v1 stay as they were ------------------------------------------------------------


async def test_v2_photo_lists_have_no_location_key(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    listing = (await client.get(f"{V2}/rovers/{P}/photos", params={"sol": "2031"})).json()
    latest = (await client.get(f"{V2}/rovers/{P}/photos/latest")).json()

    assert listing["items"] and latest["items"]
    for item in [*listing["items"], *latest["items"]]:
        assert list(item) == PHOTO_KEYS


V1_URLS = [
    "/api/v1/photos/{id}",
    "/mars-photos/api/v1/photos/{id}",
    "/api/v1/rovers/perseverance/photos?sol=2031",
    "/api/v1/rovers/perseverance/photos?sol=2031&page=1",
    "/api/v1/rovers/curiosity/photos?sol=4000",
    "/api/v1/rovers/perseverance/latest_photos",
    "/api/v1/manifests/perseverance",
]


@pytest.mark.parametrize("url", V1_URLS)
async def test_v1_responses_are_byte_identical_with_and_without_waypoints(
    client: httpx.AsyncClient, clean_db: Factory, url: str
) -> None:
    """Golden check: the same bytes (and ETag) before and after the location tables fill up."""
    records = [photo("v1-a", 80, 1234), photo("v1-b", 80, 1400), photo("v1-c", None, None)]
    async with clean_db() as session:
        await upsert_photos(session, P, records)
        await upsert_photos(session, C, [photo("v1-cur", 12, 300, rover=C)])
        await recompute_sol_summaries(session, P, {2031})
        await recompute_sol_summaries(session, C, {4000})
        await refresh_rover_stats(session, P)
        await refresh_rover_stats(session, C)
        await session.commit()
        photo_id = await session.scalar(text("SELECT id FROM photos WHERE source_id = 'v1-a'"))
    target = url.format(id=photo_id)
    before = await client.get(target)
    assert before.status_code == 200

    await insert_waypoints(clean_db, P, PERSEVERANCE_STOPS)
    await insert_waypoints(clean_db, C, CURIOSITY_STOPS)
    after = await client.get(target)

    assert after.status_code == 200
    assert after.content == before.content
    assert after.headers["etag"] == before.headers["etag"]
    assert b"location" not in after.content
    assert b"waypoint" not in after.content.lower()


async def test_v1_photo_json_keeps_its_legacy_shape(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    body = (await client.get(f"/api/v1/photos/{world['exact']}")).json()

    assert list(body) == ["photo"]
    assert "location" not in body["photo"]


async def test_image_redirect_endpoint_is_not_affected(
    client: httpx.AsyncClient, world: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/photos/{world['exact']}/image", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].endswith("/exact.png")
