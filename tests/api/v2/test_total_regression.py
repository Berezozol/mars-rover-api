"""Regression (stage 5 review round 2): `total` of /photos must not come from rovers.total_photos.

rovers.total_photos is refreshed only at the end of a sync (and stays stale after a crashed
one). Here photos and sol_summaries are committed WITHOUT refresh_rover_stats, so the rover
cache still says 0 photos; the list must count the real photos anyway.

Uses its own data via `clean_db` (never `v2_world`).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from tests.api.v2.conftest import V2, Factory
from tests.conftest import build_record

pytestmark = pytest.mark.api

ROVER = "perseverance"
BASE = datetime(2026, 11, 1, tzinfo=UTC)
# 5 photos on sol 300 and 2 on sol 301 -> 7 in total.
SOL_COUNTS = {300: 5, 301: 2}
TOTAL = sum(SOL_COUNTS.values())


def _records() -> list[Any]:
    records = []
    for sol, count in SOL_COUNTS.items():
        for n in range(count):
            taken_at = BASE + timedelta(days=sol - 300, minutes=n)
            records.append(
                build_record(
                    source_id=f"stale-{sol}-{n}",
                    sol=sol,
                    camera_code="NAVCAM_LEFT",
                    taken_at=taken_at,
                    earth_date=taken_at.date(),
                )
            )
    return records


# Newest first: sol DESC, then taken_at DESC.
EXPECTED_ORDER = [f"stale-301-{n}" for n in (1, 0)] + [f"stale-300-{n}" for n in (4, 3, 2, 1, 0)]


@pytest.fixture
async def stale_rover(clean_db: Factory) -> dict[str, int]:
    """Photos and sol summaries committed; the rover cache (total_photos) is NOT refreshed."""
    async with clean_db() as session:
        await upsert_photos(session, ROVER, _records())
        await recompute_sol_summaries(session, ROVER, SOL_COUNTS)
        await session.commit()
        cached = await session.scalar(
            text("SELECT total_photos FROM rovers WHERE id = :id"), {"id": ROVER}
        )
        rows = await session.execute(text("SELECT source_id, id FROM photos"))
        ids = {source_id: photo_id for source_id, photo_id in rows}
    assert cached == 0  # precondition: the cache lags behind the photos
    return ids


async def test_photos_total_without_filters_counts_photos_not_rover_cache(
    client: httpx.AsyncClient, stale_rover: dict[str, int]
) -> None:
    response = await client.get(f"{V2}/rovers/{ROVER}/photos")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == TOTAL
    assert [photo["id"] for photo in body["items"]] == [stale_rover[s] for s in EXPECTED_ORDER]


async def test_photos_last_page_with_stale_rover_cache_is_not_empty(
    client: httpx.AsyncClient, stale_rover: dict[str, int]
) -> None:
    # 7 photos, per_page 2 -> page 4 holds the oldest photo only.
    params = {"page": "4", "per_page": "2"}
    body = (await client.get(f"{V2}/rovers/{ROVER}/photos", params=params)).json()

    assert (body["page"], body["per_page"], body["total"]) == (4, 2, TOTAL)
    assert [photo["id"] for photo in body["items"]] == [stale_rover["stale-300-0"]]


@pytest.mark.parametrize("sol", sorted(SOL_COUNTS))
async def test_photos_total_with_sol_counts_photos(
    client: httpx.AsyncClient, stale_rover: dict[str, int], sol: int
) -> None:
    params = {"sol": str(sol)}
    body = (await client.get(f"{V2}/rovers/{ROVER}/photos", params=params)).json()

    assert body["total"] == SOL_COUNTS[sol]
    expected = [s for s in EXPECTED_ORDER if s.startswith(f"stale-{sol}-")]
    assert [photo["id"] for photo in body["items"]] == [stale_rover[s] for s in expected]
