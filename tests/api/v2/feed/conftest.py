"""Fixtures of the feed tests: committed datasets on a clean database."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from tests.api.v2._feed_data import ANCHOR, Factory, camera_names, record, seed_batch, seed_world


@pytest.fixture
async def world(clean_db: Factory) -> AsyncIterator[dict[str, int]]:
    """The dataset of `_feed_data.seed_world`; value: source_id -> photos.id."""
    yield await seed_world(clean_db)


@pytest.fixture
async def names(clean_db: Factory) -> dict[str, str]:
    """Perseverance camera code -> catalog name (read back, not hard-coded)."""
    return await camera_names(clean_db, "perseverance")


@pytest.fixture
async def many(clean_db: Factory) -> AsyncIterator[list[str]]:
    """55 perseverance photos, one per sol (100..154), one batch each, a minute apart.

    Returns source ids newest first.
    """
    ordered = []
    for n in range(55):
        source_id = f"M{n:03d}"
        await seed_batch(
            clean_db,
            "perseverance",
            [record(source_id, 100 + n, "NAVCAM_LEFT", ANCHOR - timedelta(days=3))],
            ANCHOR - timedelta(hours=1, minutes=55 - n),
        )
        ordered.append(source_id)
    yield list(reversed(ordered))


@pytest.fixture
async def big_batch(clean_db: Factory) -> AsyncIterator[list[str]]:
    """One batch of 8 photos (sol 3000); returns source ids in the expected feed order."""
    taken = ANCHOR - timedelta(days=1)
    records = [record(f"G{n}", 3000, "NAVCAM_LEFT", taken + timedelta(minutes=n)) for n in range(8)]
    await seed_batch(clean_db, "perseverance", records, ANCHOR - timedelta(hours=1))
    yield [f"G{n}" for n in reversed(range(8))]  # taken_at DESC


@pytest.fixture
async def order_batch(clean_db: Factory) -> AsyncIterator[list[str]]:
    """Newest batch whose ids run against the order: a larger id is an earlier frame.

    Inserted X1, X2, X3, X4 (ids ascending); the expected feed order (sol DESC, taken_at DESC,
    id DESC) is X3 (sol 3101), X1 (12:00), X4 and X2 (11:00 tie, larger id first).
    """
    noon = ANCHOR.replace(hour=12, minute=0, second=0) - timedelta(days=1)
    records = [
        record("X1", 3100, "NAVCAM_LEFT", noon),
        record("X2", 3100, "NAVCAM_LEFT", noon - timedelta(hours=1)),
        record("X3", 3101, "NAVCAM_LEFT", noon - timedelta(hours=3)),
        record("X4", 3100, "NAVCAM_LEFT", noon - timedelta(hours=1)),
    ]
    await seed_batch(clean_db, "perseverance", records, ANCHOR)
    yield ["X3", "X1", "X4", "X2"]
