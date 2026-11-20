"""`photos.feed_at` (stage 16, docs/proposals/stage-16-rss.md ADR-3, section 5).

- `feed_at` is set (= `created_at`) only when the regular `incremental` sync INSERTS a photo
  (`upsert_photos(..., mark_new=True)`); an upsert of an existing photo never changes it;
  `backfill`, imports and a plain `upsert_photos` leave it NULL.
- A sol is committed by one transaction, so all photos of a sol stored by one run share one
  `feed_at` (a "batch" = equal `(feed_at, sol)`); a sol delivered again later gives a new batch.
- Late photos of old sols (inside the overlap) are new too.

The service commits, so every test uses `clean_db`. The source is a FakeAdapter.
"""

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.repositories.photos import upsert_photos
from app.services.sync import SyncService
from tests.conftest import build_record
from tests.integration._fakes import FakeAdapter, fetch_all, sol_records

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def service(session_factory: SessionFactory, settings: Settings) -> SyncService:
    return SyncService(
        session_factory, settings.model_copy(update={"sync_overlap_sols": 7, "sync_chunk_sols": 10})
    )


async def photo_rows(factory: SessionFactory, rover: str = "perseverance") -> dict[str, dict]:
    rows = await fetch_all(
        factory,
        "SELECT source_id, sol, title, feed_at, created_at FROM photos WHERE rover_id = :r",
        r=rover,
    )
    return {row["source_id"]: row for row in rows}


async def batches(
    factory: SessionFactory, rover: str = "perseverance"
) -> list[tuple[Any, int, int]]:
    """(feed_at, sol, photos) of the feed batches, oldest first."""
    rows = await fetch_all(
        factory,
        "SELECT feed_at, sol, count(*) AS n FROM photos "
        "WHERE rover_id = :r AND feed_at IS NOT NULL GROUP BY feed_at, sol ORDER BY feed_at, sol",
        r=rover,
    )
    return [(row["feed_at"], row["sol"], row["n"]) for row in rows]


# --- incremental sync -----------------------------------------------------------------------


async def test_incremental_sync_sets_feed_at_equal_to_created_at_on_insert(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance", latest=100)
    adapter.add(sol_records("perseverance", 100, 3))

    result = await service.run(adapter)

    assert result.mode == "incremental"
    rows = await photo_rows(session_factory)
    assert len(rows) == 3
    for row in rows.values():
        assert row["feed_at"] is not None
        assert row["feed_at"] == row["created_at"]


async def test_incremental_sync_second_run_with_same_photos_keeps_feed_at(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance", latest=100)
    adapter.add(sol_records("perseverance", 100, 3))
    await service.run(adapter)
    before = await photo_rows(session_factory)

    await service.run(adapter)

    assert await photo_rows(session_factory) == before
    assert len(await batches(session_factory)) == 1


async def test_incremental_sync_upsert_of_changed_photo_keeps_feed_at(
    service: SyncService, session_factory: SessionFactory
) -> None:
    first = FakeAdapter("perseverance", latest=100)
    first.add(sol_records("perseverance", 100, 2, title="old title"))
    await service.run(first)
    before = await photo_rows(session_factory)

    second = FakeAdapter("perseverance", latest=100)
    second.add(sol_records("perseverance", 100, 2, title="new title"))
    await service.run(second)

    after = await photo_rows(session_factory)
    assert {row["title"] for row in after.values()} == {"new title"}
    for source_id, row in after.items():
        assert row["feed_at"] == before[source_id]["feed_at"]
        assert row["created_at"] == before[source_id]["created_at"]


async def test_incremental_sync_two_sols_in_one_run_give_two_batches(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance", latest=101)
    adapter.add(sol_records("perseverance", 100, 2))
    adapter.add(sol_records("perseverance", 101, 3))

    await service.run(adapter)

    found = await batches(session_factory)
    assert sorted((sol, count) for _, sol, count in found) == [(100, 2), (101, 3)]
    assert found[0][0] != found[1][0]


async def test_incremental_sync_sol_delivered_again_with_new_photos_gives_a_new_batch(
    service: SyncService, session_factory: SessionFactory
) -> None:
    first = FakeAdapter("perseverance", latest=100)
    first.add(sol_records("perseverance", 100, 2))
    await service.run(first)
    [(first_feed_at, _, _)] = await batches(session_factory)

    second = FakeAdapter("perseverance", latest=100)
    second.add(sol_records("perseverance", 100, 2))  # the same two photos
    second.add(
        [build_record(source_id="perseverance-100-late", sol=100, camera_code="NAVCAM_LEFT")]
    )
    await service.run(second)

    found = await batches(session_factory)
    assert [(sol, count) for _, sol, count in found] == [(100, 2), (100, 1)]
    assert found[0][0] == first_feed_at  # the first batch is unchanged
    assert found[1][0] > first_feed_at


async def test_incremental_sync_late_photo_of_an_old_sol_gets_feed_at(
    service: SyncService, session_factory: SessionFactory
) -> None:
    first = FakeAdapter("perseverance", latest=100)
    first.add(sol_records("perseverance", 100, 2))
    first.add(sol_records("perseverance", 97, 1))
    await service.run(first)

    second = FakeAdapter("perseverance", latest=100)
    second.add(sol_records("perseverance", 97, 1))
    second.add([build_record(source_id="perseverance-97-late", sol=97, camera_code="NAVCAM_LEFT")])
    await service.run(second)

    rows = await photo_rows(session_factory)
    assert rows["perseverance-97-late"]["feed_at"] is not None
    assert rows["perseverance-97-late"]["sol"] == 97
    assert rows["perseverance-97-0"]["feed_at"] == rows["perseverance-97-0"]["created_at"]
    found = await batches(session_factory)
    assert sorted((sol, count) for _, sol, count in found) == [(97, 1), (97, 1), (100, 2)]
    assert max(found)[0] == rows["perseverance-97-late"]["feed_at"]  # the late batch is the newest


# --- backfill and imports -------------------------------------------------------------------


async def test_backfill_leaves_feed_at_null(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance", latest=500)
    adapter.add(sol_records("perseverance", 120, 3))

    result = await service.run(adapter, from_sol=100, to_sol=124)

    assert result.mode == "backfill"
    rows = await photo_rows(session_factory)
    assert len(rows) == 3
    assert all(row["feed_at"] is None for row in rows.values())
    assert all(row["created_at"] is not None for row in rows.values())


async def test_incremental_sync_over_backfilled_sol_marks_only_new_photos(
    service: SyncService, session_factory: SessionFactory
) -> None:
    backfill = FakeAdapter("perseverance", latest=100)
    backfill.add(sol_records("perseverance", 100, 2))
    await service.run(backfill, from_sol=100, to_sol=100)

    adapter = FakeAdapter("perseverance", latest=100)
    adapter.add(sol_records("perseverance", 100, 2, title="changed"))  # known photos, changed
    adapter.add(
        [build_record(source_id="perseverance-100-new", sol=100, camera_code="NAVCAM_LEFT")]
    )
    await service.run(adapter)

    rows = await photo_rows(session_factory)
    assert rows["perseverance-100-0"]["feed_at"] is None
    assert rows["perseverance-100-1"]["feed_at"] is None
    assert rows["perseverance-100-0"]["title"] == "changed"
    assert rows["perseverance-100-new"]["feed_at"] == rows["perseverance-100-new"]["created_at"]


async def test_backfill_over_feed_photos_keeps_their_feed_at(
    service: SyncService, session_factory: SessionFactory
) -> None:
    incremental = FakeAdapter("curiosity", latest=100)
    incremental.add(sol_records("curiosity", 100, 2))
    await service.run(incremental)
    before = await photo_rows(session_factory, "curiosity")

    backfill = FakeAdapter("curiosity", latest=100)
    backfill.add(sol_records("curiosity", 100, 2, title="reprocessed"))
    await service.run(backfill, from_sol=100, to_sol=100)

    after = await photo_rows(session_factory, "curiosity")
    assert {row["title"] for row in after.values()} == {"reprocessed"}
    assert {k: v["feed_at"] for k, v in after.items()} == {
        k: v["feed_at"] for k, v in before.items()
    }
    assert all(row["feed_at"] is not None for row in after.values())


# --- upsert_photos(mark_new=...) ------------------------------------------------------------


async def test_upsert_photos_default_leaves_feed_at_null(db_session: AsyncSession) -> None:
    await upsert_photos(db_session, "curiosity", [build_record(source_id="imp-1", sol=5)])

    rows = await fetch_rows(db_session, "imp-1")

    assert rows == [{"feed_at": None}]


async def test_upsert_photos_mark_new_sets_feed_at_to_created_at(db_session: AsyncSession) -> None:
    await upsert_photos(
        db_session, "curiosity", [build_record(source_id="new-1", sol=5)], mark_new=True
    )

    rows = await fetch_rows(db_session, "new-1", "feed_at = created_at AS same")

    assert rows == [{"feed_at": rows[0]["feed_at"], "same": True}]
    assert isinstance(rows[0]["feed_at"], datetime)
    assert rows[0]["feed_at"].tzinfo is not None


async def test_upsert_photos_mark_new_does_not_touch_existing_rows(
    db_session: AsyncSession,
) -> None:
    await upsert_photos(db_session, "curiosity", [build_record(source_id="a", sol=5)])
    await upsert_photos(
        db_session, "curiosity", [build_record(source_id="b", sol=5, title="first")], mark_new=True
    )
    [b_before] = await fetch_rows(db_session, "b")

    await upsert_photos(
        db_session,
        "curiosity",
        [
            build_record(source_id="a", sol=5, title="changed"),
            build_record(source_id="b", sol=5, title="changed"),
        ],
        mark_new=True,
    )

    assert await fetch_rows(db_session, "a") == [{"feed_at": None}]
    assert await fetch_rows(db_session, "b") == [b_before]


async def test_upsert_photos_without_mark_new_keeps_existing_feed_at(
    db_session: AsyncSession,
) -> None:
    await upsert_photos(
        db_session, "curiosity", [build_record(source_id="b", sol=5)], mark_new=True
    )
    [before] = await fetch_rows(db_session, "b")

    await upsert_photos(db_session, "curiosity", [build_record(source_id="b", sol=5, title="x")])

    assert await fetch_rows(db_session, "b") == [before]
    assert before["feed_at"] is not None


async def fetch_rows(session: AsyncSession, source_id: str, extra: str = "") -> list[dict]:
    columns = "feed_at" + (f", {extra}" if extra else "")
    result = await session.execute(
        text(f"SELECT {columns} FROM photos WHERE source_id = :s"), {"s": source_id}
    )
    return [dict(row) for row in result.mappings()]
