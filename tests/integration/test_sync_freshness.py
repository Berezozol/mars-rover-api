"""Data freshness for `/health/sync` and the alerts (docs/API.md "GET /health/sync"; stage 9).

``get_sync_freshness(session, settings, now)`` per synced rover (curiosity, perseverance;
never spirit/opportunity):
- ``last_success_at`` -- ``finished_at`` of the latest ``success`` run of any mode, else None;
- ``newest_photo_date`` -- the latest photo ``earth_date``, else None;
- ``sync_stale`` -- no success or older than SYNC_STALE_HOURS (24);
- ``photos_stale`` -- no photos or older than PHOTOS_STALE_DAYS (21): exactly 21 days is
  still fresh, 22 is stale.

The result shape is not pinned (dict, dataclass or pydantic model): it is normalized by
``tests.integration._freshness.to_jsonable``. ``now`` is fixed; nothing waits.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.health import get_sync_freshness
from tests.integration._freshness import (
    SYNCED_ROVERS,
    add_photos,
    add_run,
    by_rover,
    overall_status,
    to_jsonable,
)

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
TODAY = NOW.date()


async def freshness(factory: Factory, settings: Settings, now: datetime = NOW) -> Any:
    async with factory() as session:
        return await get_sync_freshness(session, settings, now)


def as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def as_date_str(value: Any) -> str | None:
    return None if value is None else str(value)[:10]


async def make_fresh(factory: Factory, rover_id: str) -> None:
    await add_run(factory, rover_id, finished_at=NOW - timedelta(hours=1))
    await add_photos(factory, rover_id, [TODAY - timedelta(days=1)])


# --- rovers --------------------------------------------------------------------------------


async def test_freshness_lists_only_synced_rovers_in_id_order(
    clean_db: Factory, settings: Settings
) -> None:
    await add_photos(clean_db, "spirit", [date(2010, 3, 21)])
    await add_photos(clean_db, "opportunity", [date(2018, 6, 10)])

    result = await freshness(clean_db, settings)

    data = to_jsonable(result)
    rovers = data["rovers"] if isinstance(data, dict) else data
    assert [item["rover"] for item in rovers] == SYNCED_ROVERS


async def test_freshness_all_fresh_has_no_flags(clean_db: Factory, settings: Settings) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)

    result = await freshness(clean_db, settings)

    for rover in SYNCED_ROVERS:
        item = by_rover(result)[rover]
        assert item["sync_stale"] is False, item
        assert item["photos_stale"] is False, item
        assert as_datetime(item["last_success_at"]) == NOW - timedelta(hours=1)
        assert as_date_str(item["newest_photo_date"]) == (TODAY - timedelta(days=1)).isoformat()
    assert overall_status(result) in (None, "ok")


async def test_freshness_empty_database_marks_everything_stale(
    clean_db: Factory, settings: Settings
) -> None:
    result = await freshness(clean_db, settings)

    for rover in SYNCED_ROVERS:
        item = by_rover(result)[rover]
        assert item["last_success_at"] is None
        assert item["newest_photo_date"] is None
        assert item["sync_stale"] is True
        assert item["photos_stale"] is True
    assert overall_status(result) in (None, "stale")


async def test_freshness_old_mer_data_does_not_make_status_stale(
    clean_db: Factory, settings: Settings
) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)
    await add_photos(clean_db, "spirit", [date(2010, 3, 21)])
    await add_run(clean_db, "opportunity", status="failed", finished_at=NOW - timedelta(hours=1))

    result = await freshness(clean_db, settings)

    assert set(by_rover(result)) == set(SYNCED_ROVERS)
    assert overall_status(result) in (None, "ok")


# --- sync_stale ----------------------------------------------------------------------------

H = timedelta(hours=1)

RUN_CASES = [
    pytest.param([], None, True, id="no-runs"),
    pytest.param([("success", 1 * H, "incremental")], 1 * H, False, id="success-1h"),
    pytest.param([("success", 23 * H, "incremental")], 23 * H, False, id="success-23h"),
    pytest.param(
        [("success", 24 * H, "incremental")], 24 * H, False, id="success-24h-boundary-fresh"
    ),
    pytest.param([("success", 25 * H, "incremental")], 25 * H, True, id="success-25h"),
    pytest.param([("success", 2 * H, "backfill")], 2 * H, False, id="backfill-success-counts"),
    pytest.param([("failed", 1 * H, "incremental")], None, True, id="only-failed"),
    pytest.param([("partial", 1 * H, "incremental")], None, True, id="only-partial"),
    pytest.param([("running", None, "incremental")], None, True, id="only-running"),
    pytest.param(
        [("success", 30 * H, "incremental"), ("failed", 1 * H, "incremental")],
        30 * H,
        True,
        id="failed-after-old-success",
    ),
    pytest.param(
        [("success", 30 * H, "incremental"), ("partial", 1 * H, "incremental")],
        30 * H,
        True,
        id="partial-after-old-success",
    ),
    pytest.param(
        [("success", 2 * H, "incremental"), ("failed", 1 * H, "incremental")],
        2 * H,
        False,
        id="failed-after-recent-success",
    ),
    pytest.param(
        [("success", 40 * H, "incremental"), ("success", 3 * H, "backfill")],
        3 * H,
        False,
        id="latest-of-two-successes",
    ),
]


@pytest.mark.parametrize(("runs", "success_age", "stale"), RUN_CASES)
async def test_freshness_sync_stale_by_runs(
    clean_db: Factory,
    settings: Settings,
    runs: list[tuple[str, timedelta | None, str]],
    success_age: timedelta | None,
    stale: bool,
) -> None:
    for status, age, mode in runs:
        finished = None if age is None else NOW - age
        started = NOW - (age or H) - timedelta(minutes=5)
        await add_run(
            clean_db,
            "curiosity",
            status=status,
            finished_at=finished,
            started_at=started,
            mode=mode,
        )
    await add_photos(clean_db, "curiosity", [TODAY])

    item = by_rover(await freshness(clean_db, settings))["curiosity"]

    expected_success = None if success_age is None else NOW - success_age
    assert as_datetime(item["last_success_at"]) == expected_success
    assert item["sync_stale"] is stale
    assert item["photos_stale"] is False


async def test_freshness_runs_of_other_rover_are_ignored(
    clean_db: Factory, settings: Settings
) -> None:
    await add_run(clean_db, "perseverance", finished_at=NOW - H)

    item = by_rover(await freshness(clean_db, settings))["curiosity"]

    assert item["last_success_at"] is None
    assert item["sync_stale"] is True


async def test_freshness_sync_stale_hours_setting_is_used(
    clean_db: Factory, settings: Settings
) -> None:
    await add_run(clean_db, "curiosity", finished_at=NOW - 3 * H)
    strict = settings.model_copy(update={"sync_stale_hours": 2})

    item = by_rover(await freshness(clean_db, strict))["curiosity"]

    assert item["sync_stale"] is True


async def test_freshness_uses_given_now(clean_db: Factory, settings: Settings) -> None:
    await add_run(clean_db, "curiosity", finished_at=NOW - H)
    await add_photos(clean_db, "curiosity", [TODAY])

    later = by_rover(await freshness(clean_db, settings, NOW + timedelta(days=30)))["curiosity"]

    assert later["sync_stale"] is True
    assert later["photos_stale"] is True


# --- photos_stale --------------------------------------------------------------------------

D = timedelta(days=1)

PHOTO_CASES = [
    pytest.param([], None, True, id="no-photos"),
    pytest.param([0 * D], 0 * D, False, id="today"),
    pytest.param([1 * D], 1 * D, False, id="1-day"),
    pytest.param([9 * D], 9 * D, False, id="9-days"),
    pytest.param([11 * D], 11 * D, False, id="11-days"),
    pytest.param([21 * D], 21 * D, False, id="21-days-boundary-fresh"),
    pytest.param([22 * D], 22 * D, True, id="22-days-stale"),
    pytest.param([400 * D, 30 * D], 30 * D, True, id="newest-30-days"),
    pytest.param([400 * D, 30 * D, 2 * D], 2 * D, False, id="newest-of-many"),
]


@pytest.mark.parametrize(("ages", "newest_age", "stale"), PHOTO_CASES)
async def test_freshness_photos_stale_by_newest_earth_date(
    clean_db: Factory,
    settings: Settings,
    ages: list[timedelta],
    newest_age: timedelta | None,
    stale: bool,
) -> None:
    await add_run(clean_db, "perseverance", finished_at=NOW - H)
    if ages:
        await add_photos(clean_db, "perseverance", [TODAY - age for age in ages])

    item = by_rover(await freshness(clean_db, settings))["perseverance"]

    expected = None if newest_age is None else (TODAY - newest_age).isoformat()
    assert as_date_str(item["newest_photo_date"]) == expected
    assert item["photos_stale"] is stale
    assert item["sync_stale"] is False


async def test_freshness_photos_of_other_rover_are_ignored(
    clean_db: Factory, settings: Settings
) -> None:
    await add_photos(clean_db, "curiosity", [TODAY])

    item = by_rover(await freshness(clean_db, settings))["perseverance"]

    assert item["newest_photo_date"] is None
    assert item["photos_stale"] is True


async def test_freshness_photos_stale_days_setting_is_used(
    clean_db: Factory, settings: Settings
) -> None:
    await add_photos(clean_db, "curiosity", [TODAY - 7 * D])
    strict = settings.model_copy(update={"photos_stale_days": 5})

    item = by_rover(await freshness(clean_db, strict))["curiosity"]

    assert item["photos_stale"] is True


async def test_freshness_one_stale_flag_makes_status_stale(
    clean_db: Factory, settings: Settings
) -> None:
    await make_fresh(clean_db, "curiosity")
    await add_run(clean_db, "perseverance", finished_at=NOW - H)
    await add_photos(clean_db, "perseverance", [TODAY - 30 * D])

    result = await freshness(clean_db, settings)

    assert by_rover(result)["curiosity"]["photos_stale"] is False
    assert by_rover(result)["perseverance"]["photos_stale"] is True
    assert overall_status(result) in (None, "stale")
