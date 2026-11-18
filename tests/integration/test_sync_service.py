"""SyncService (app/services/sync.py): ranges, chunks, partial failures, lock, run journal.

Contract: docs/ARCHITECTURE.md "Синхронизация" and the stage-3 shared contract.
The service commits, so every test uses `clean_db` (committed-data isolation).
The source is a FakeAdapter, except the end-to-end test through M2020Adapter + respx.
"""

import asyncio
import copy
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.errors import SyncInProgress
from app.core.legacy_dates import legacy_earth_date
from app.services.sync import SYNC_LOCK_NAMESPACE, SyncResult, SyncService
from app.sources import http
from app.sources.m2020 import M2020Adapter
from tests.integration._fakes import (
    FakeAdapter,
    earth_date_for,
    legacy_id,
    photo_count,
    photo_sols,
    rover_row,
    scalar,
    sol_records,
    summaries,
    sync_runs,
)
from tests.unit._upstream import (
    M2020_URL,
    FakeClock,
    load_fixture,
    make_settings,
    paged_responder,
    query,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]
OVERLAP = 7
CHUNK = 10
URL = "https://feed.example.test/"


def upstream_error(message: str = "boom") -> http.UpstreamError:
    return http.UpstreamError(URL, 500, message)


@pytest.fixture
def sync_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"sync_overlap_sols": OVERLAP, "sync_chunk_sols": CHUNK})


@pytest.fixture
def service(session_factory: SessionFactory, sync_settings: Settings) -> SyncService:
    return SyncService(session_factory, sync_settings)


async def set_rover_max_sol(factory: SessionFactory, rover_id: str, max_sol: int) -> None:
    async with factory() as session:
        await session.execute(
            text("UPDATE rovers SET max_sol = :sol WHERE id = :rover"),
            {"sol": max_sol, "rover": rover_id},
        )
        await session.commit()


def chunks(from_sol: int, to_sol: int, size: int = CHUNK) -> list[tuple[int, int]]:
    """Expected descending chunks: (to-size+1, to), ... down to from."""
    result = []
    upper = to_sol
    while upper >= from_sol:
        lower = max(from_sol, upper - size + 1)
        result.append((lower, upper))
        upper = lower - 1
    return result


def test_chunks_helper_matches_contract_example() -> None:
    assert chunks(100, 124) == [(115, 124), (105, 114), (100, 104)]
    assert chunks(0, 0) == [(0, 0)]


def test_sync_lock_namespace_is_pinned() -> None:
    assert SYNC_LOCK_NAMESPACE == 7_250_001


# --- ranges ------------------------------------------------------------------------------


async def test_run_incremental_empty_db_starts_at_latest_minus_overlap(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance", latest=500)

    result = await service.run(adapter)

    assert (result.mode, result.from_sol, result.to_sol) == ("incremental", 493, 500)
    assert adapter.calls[0] == ("latest_sol",)
    assert adapter.fetch_calls == chunks(493, 500)
    assert result.status == "success"
    assert result.failed_sols == []
    [run] = await sync_runs(session_factory)
    assert (run["mode"], run["from_sol"], run["to_sol"]) == ("incremental", 493, 500)
    assert run["id"] == result.run_id


async def test_run_incremental_with_data_starts_at_rover_max_sol_minus_overlap(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await set_rover_max_sol(session_factory, "curiosity", 480)
    adapter = FakeAdapter("curiosity", latest=500)

    result = await service.run(adapter)

    assert (result.from_sol, result.to_sol) == (473, 500)
    assert adapter.fetch_calls == [(491, 500), (481, 490), (473, 480)]
    [run] = await sync_runs(session_factory)
    assert (run["from_sol"], run["to_sol"]) == (473, 500)


@pytest.mark.parametrize(
    ("max_sol", "latest", "expected"),
    [
        pytest.param(None, 3, (0, 3), id="empty-db-latest-below-overlap"),
        pytest.param(2, 4, (0, 4), id="max-sol-below-overlap"),
        pytest.param(None, 0, (0, 0), id="latest-zero"),
    ],
)
async def test_run_incremental_range_clamped_at_zero(
    service: SyncService,
    session_factory: SessionFactory,
    max_sol: int | None,
    latest: int,
    expected: tuple[int, int],
) -> None:
    if max_sol is not None:
        await set_rover_max_sol(session_factory, "curiosity", max_sol)
    adapter = FakeAdapter("curiosity", latest=latest)

    result = await service.run(adapter)

    assert (result.from_sol, result.to_sol) == expected
    assert adapter.fetch_calls == chunks(*expected)


async def test_run_backfill_uses_given_range_without_latest_sol(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity", latest=9999)

    result = await service.run(adapter, from_sol=100, to_sol=124)

    assert (result.mode, result.from_sol, result.to_sol) == ("backfill", 100, 124)
    assert ("latest_sol",) not in adapter.calls
    assert adapter.fetch_calls == [(115, 124), (105, 114), (100, 104)]
    [run] = await sync_runs(session_factory)
    assert (run["mode"], run["from_sol"], run["to_sol"]) == ("backfill", 100, 124)


@pytest.mark.parametrize(
    "bounds",
    [
        pytest.param({"from_sol": 5}, id="only-from"),
        pytest.param({"to_sol": 5}, id="only-to"),
        pytest.param({"from_sol": 6, "to_sol": 5}, id="from-greater-than-to"),
        pytest.param({"from_sol": -1, "to_sol": 5}, id="negative-from"),
        pytest.param({"from_sol": -3, "to_sol": -1}, id="negative-both"),
    ],
)
async def test_run_invalid_bounds_raise_value_error_before_any_db_row(
    service: SyncService, session_factory: SessionFactory, bounds: dict[str, int]
) -> None:
    adapter = FakeAdapter("curiosity", latest=10)

    with pytest.raises(ValueError):
        await service.run(adapter, **bounds)

    assert adapter.calls == []
    assert await sync_runs(session_factory) == []


async def test_run_chunk_size_setting_is_respected(
    session_factory: SessionFactory, sync_settings: Settings
) -> None:
    service = SyncService(session_factory, sync_settings.model_copy(update={"sync_chunk_sols": 3}))
    adapter = FakeAdapter("curiosity")

    await service.run(adapter, from_sol=10, to_sol=17)

    assert adapter.fetch_calls == [(15, 17), (12, 14), (10, 11)]


# --- idempotency -------------------------------------------------------------------------


async def test_run_twice_same_data_creates_no_duplicates(
    service: SyncService, session_factory: SessionFactory
) -> None:
    def make_adapter() -> FakeAdapter:
        adapter = FakeAdapter("curiosity")
        adapter.add(sol_records("curiosity", 104, 3))
        adapter.add(sol_records("curiosity", 101, 2))
        adapter.add(sol_records("curiosity", 101, 1, sample_type="subframe"))
        return adapter

    first = await service.run(make_adapter(), from_sol=100, to_sol=105)
    stored_after_first = await photo_sols(session_factory, "curiosity")
    second = await service.run(make_adapter(), from_sol=100, to_sol=105)

    assert (first.status, second.status) == ("success", "success")
    assert first.photos_upserted == 6
    assert second.photos_upserted == 6
    assert stored_after_first == {104: 3, 101: 3}
    assert await photo_sols(session_factory, "curiosity") == {104: 3, 101: 3}
    duplicates = await scalar(
        session_factory,
        "SELECT count(*) - count(DISTINCT (rover_id, source_id)) FROM photos",
    )
    assert duplicates == 0
    runs = await sync_runs(session_factory)
    assert [(r["status"], r["photos_upserted"]) for r in runs] == [("success", 6), ("success", 6)]
    assert second.run_id != first.run_id


async def test_run_overlap_merges_new_photos_of_known_sol(
    service: SyncService, session_factory: SessionFactory
) -> None:
    """Photos of a sol keep arriving for days: a re-run adds them, keeps the old ones."""
    await service.run(
        FakeAdapter("curiosity").add(sol_records("curiosity", 50, 2)), from_sol=50, to_sol=50
    )
    later = FakeAdapter("curiosity").add(sol_records("curiosity", 50, 5))

    result = await service.run(later, from_sol=50, to_sol=50)

    assert result.photos_upserted == 5
    assert await photo_sols(session_factory, "curiosity") == {50: 5}
    assert (await summaries(session_factory, "curiosity"))[50]["total_photos"] == 5


# --- partial failures --------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(upstream_error("HTTP 500"), id="upstream-error"),
        pytest.param(http.PageLimitExceeded(URL, None, "page limit reached"), id="page-limit"),
        pytest.param(http.IncompleteResponse(URL, None, "empty page 3"), id="incomplete"),
    ],
)
async def test_run_chunk_failure_marks_unyielded_sols_failed_and_continues(
    service: SyncService, session_factory: SessionFactory, error: http.UpstreamError
) -> None:
    # Chunks: 115..124, 105..114, 100..104. The middle chunk fails at sol 111 after
    # yielding 114, 113, 112; sols 111..105 (incl. 110 and 107 with data) are not yielded.
    adapter = FakeAdapter("curiosity", fail_at={111: error})
    for sol, count in {124: 2, 120: 1, 114: 1, 113: 1, 112: 2, 110: 3, 107: 1, 101: 2}.items():
        adapter.add(sol_records("curiosity", sol, count))

    result = await service.run(adapter, from_sol=100, to_sol=124)

    assert adapter.fetch_calls == [(115, 124), (105, 114), (100, 104)]
    assert result.status == "partial"
    assert sorted(result.failed_sols) == list(range(105, 112))
    assert await photo_sols(session_factory, "curiosity") == {
        124: 2,
        120: 1,
        114: 1,
        113: 1,
        112: 2,
        101: 2,
    }
    assert result.photos_upserted == 9
    assert result.error is not None
    assert result.error.startswith("failed sols:")
    assert "105-111" in result.error
    assert type(error).__name__ in result.error
    [run] = await sync_runs(session_factory)
    assert (run["status"], run["photos_upserted"], run["error"]) == (
        "partial",
        9,
        result.error,
    )
    assert run["finished_at"] is not None
    assert run["failed_sols"] == result.failed_sols == list(range(105, 112))


async def test_run_error_text_lists_compact_ranges_of_all_failed_chunks(
    service: SyncService,
) -> None:
    # Chunk 115..124 fails at once; chunk 105..114 yields 114..111 and fails at 110;
    # chunk 100..104 is stored.
    adapter = FakeAdapter(
        "curiosity",
        fail_at={124: upstream_error("first"), 110: upstream_error("second")},
    )
    for sol in (114, 113, 112, 111, 100):
        adapter.add(sol_records("curiosity", sol, 1))

    result = await service.run(adapter, from_sol=100, to_sol=124)

    assert result.status == "partial"
    assert sorted(result.failed_sols) == [*range(105, 111), *range(115, 125)]
    assert "105-110" in result.error
    assert "115-124" in result.error
    # The last error is the one of the later (lower) chunk.
    assert "second" in result.error


async def test_run_chunk_failure_marks_whole_chunk_minus_yielded_sols(
    service: SyncService, session_factory: SessionFactory
) -> None:
    """Owner's contract: failed = chunk minus yielded sols, incl. sols the descending
    iterator already passed without photos (they may still arrive on the failed page)."""
    # Chunk 105..114: sols 114 and 113 have no photos, 112 is yielded, then failure at 111.
    adapter = FakeAdapter("curiosity", fail_at={111: upstream_error()})
    adapter.add(sol_records("curiosity", 112, 1))

    result = await service.run(adapter, from_sol=105, to_sol=114)

    expected = [sol for sol in range(105, 115) if sol != 112]
    assert result.status == "partial"
    assert result.failed_sols == expected
    assert "105-111" in result.error
    assert "113-114" in result.error
    [run] = await sync_runs(session_factory)
    assert run["failed_sols"] == expected


async def test_run_db_failure_on_one_sol_is_partial_and_other_sols_stored(
    service: SyncService, session_factory: SessionFactory
) -> None:
    # PostgreSQL rejects NUL characters in text: the upsert of sol 103 fails in the DB.
    adapter = FakeAdapter("curiosity")
    adapter.add(sol_records("curiosity", 105, 2))
    adapter.add(sol_records("curiosity", 103, 2, title="broken\x00title"))
    adapter.add(sol_records("curiosity", 101, 1))

    result = await service.run(adapter, from_sol=100, to_sol=105)

    assert result.status == "partial"
    assert result.failed_sols == [103]
    assert result.photos_upserted == 3
    assert await photo_sols(session_factory, "curiosity") == {105: 2, 101: 1}
    assert set(await summaries(session_factory, "curiosity")) == {105, 101}
    assert "103" in result.error
    [run] = await sync_runs(session_factory)
    assert (run["status"], run["failed_sols"]) == ("partial", [103])


async def test_run_everything_fails_is_failed_without_last_synced(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter(
        "curiosity",
        fail_at={109: upstream_error("down"), 119: upstream_error("down")},
    )
    adapter.add(sol_records("curiosity", 115, 1)).add(sol_records("curiosity", 105, 1))

    result = await service.run(adapter, from_sol=100, to_sol=119)

    assert result.status == "failed"
    assert sorted(result.failed_sols) == list(range(100, 120))
    assert result.photos_upserted == 0
    assert "100-119" in result.error
    assert await photo_count(session_factory) == 0
    [run] = await sync_runs(session_factory)
    assert (run["status"], run["photos_upserted"]) == ("failed", 0)
    assert run["failed_sols"] == list(range(100, 120))
    assert run["finished_at"] is not None
    assert (await rover_row(session_factory, "curiosity"))["last_synced_at"] is None


async def test_run_every_sol_db_failure_is_failed(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity").add(sol_records("curiosity", 3, 1, title="x\x00"))

    result = await service.run(adapter, from_sol=0, to_sol=5)

    assert result.status == "failed"
    assert result.failed_sols == [3]
    assert await photo_count(session_factory) == 0


async def test_run_latest_sol_failure_finishes_run_as_failed_without_raising(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance", latest_error=upstream_error("latest is down"))

    result = await service.run(adapter)

    assert isinstance(result, SyncResult)
    assert result.status == "failed"
    assert result.photos_upserted == 0
    assert adapter.fetch_calls == []
    [run] = await sync_runs(session_factory)
    assert (run["mode"], run["status"]) == ("incremental", "failed")
    assert (run["from_sol"], run["to_sol"], run["failed_sols"]) == (None, None, [])
    assert run["finished_at"] is not None
    assert "latest is down" in run["error"]
    assert (await rover_row(session_factory, "perseverance"))["last_synced_at"] is None


async def test_run_sols_without_records_are_not_failures(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity").add(sol_records("curiosity", 7, 1))

    result = await service.run(adapter, from_sol=0, to_sol=29)

    assert (result.status, result.failed_sols, result.error) == ("success", [], None)
    assert set(await summaries(session_factory, "curiosity")) == {7}


# --- sync_runs lifecycle -----------------------------------------------------------------


async def test_run_journal_row_is_running_during_sync_and_finished_after(
    service: SyncService, session_factory: SessionFactory
) -> None:
    seen: dict[str, list[dict[str, Any]]] = {}

    async def on_latest() -> None:
        seen["at_latest"] = await sync_runs(session_factory)

    async def on_fetch(from_sol: int, to_sol: int) -> None:
        seen.setdefault("at_fetch", await sync_runs(session_factory))

    adapter = FakeAdapter("perseverance", latest=20, on_latest=on_latest, on_fetch=on_fetch)
    adapter.add(sol_records("perseverance", 18, 2))

    result = await service.run(adapter)

    [at_latest] = seen["at_latest"]
    assert at_latest["status"] == "running"
    assert at_latest["mode"] == "incremental"
    assert at_latest["finished_at"] is None
    assert at_latest["started_at"] is not None
    [at_fetch] = seen["at_fetch"]
    assert (at_fetch["status"], at_fetch["from_sol"], at_fetch["to_sol"]) == ("running", 13, 20)
    assert at_fetch["finished_at"] is None

    [final] = await sync_runs(session_factory)
    assert final["id"] == result.run_id == at_latest["id"]
    assert final["rover_id"] == "perseverance"
    assert (final["status"], final["photos_upserted"], final["error"]) == ("success", 2, None)
    assert final["failed_sols"] == []
    assert at_latest["failed_sols"] == []
    assert final["finished_at"] is not None
    assert final["finished_at"] >= final["started_at"]


async def test_run_result_fields(service: SyncService) -> None:
    adapter = FakeAdapter("curiosity").add(sol_records("curiosity", 2, 4))

    result = await service.run(adapter, from_sol=0, to_sol=3)

    assert result == SyncResult(
        run_id=result.run_id,
        rover_id="curiosity",
        mode="backfill",
        from_sol=0,
        to_sol=3,
        status="success",
        photos_upserted=4,
        failed_sols=[],
        error=None,
    )


# --- sol_summaries and rover cache -------------------------------------------------------


async def test_run_updates_sol_summaries_of_synced_sols(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity")
    adapter.add(sol_records("curiosity", 21, 2, camera_code="NAVCAM"))
    adapter.add(sol_records("curiosity", 21, 1, sample_type="subframe", camera_code="MAST"))
    adapter.add(sol_records("curiosity", 20, 1))

    await service.run(adapter, from_sol=20, to_sol=21)

    rows = await summaries(session_factory, "curiosity")
    assert set(rows) == {20, 21}
    assert (rows[21]["total_photos"], rows[21]["full_photos"]) == (3, 2)
    assert rows[21]["cameras"] == ["MAST", "NAVCAM"]
    assert rows[21]["full_cameras"] == ["NAVCAM"]
    assert rows[21]["earth_date"] == earth_date_for(21)
    assert (rows[20]["total_photos"], rows[20]["full_photos"]) == (1, 1)


@pytest.mark.parametrize("partial", [False, True], ids=["success", "partial"])
async def test_run_refreshes_rover_cache_and_last_synced(
    service: SyncService, session_factory: SessionFactory, partial: bool
) -> None:
    # Sol 45 has only a subframe: max_sol 45, but full_max_sol 44.
    adapter = FakeAdapter("curiosity", fail_at={39: upstream_error()} if partial else None)
    adapter.add(sol_records("curiosity", 45, 1, sample_type="subframe"))
    adapter.add(sol_records("curiosity", 44, 3))
    adapter.add(sol_records("curiosity", 41, 2))

    result = await service.run(adapter, from_sol=30, to_sol=49)

    assert result.status == ("partial" if partial else "success")
    rover = await rover_row(session_factory, "curiosity")
    assert rover["max_sol"] == 45
    assert rover["max_date"] == earth_date_for(45)
    assert rover["total_photos"] == 6
    assert rover["full_max_sol"] == 44
    assert rover["full_max_date"] == legacy_earth_date(rover["landing_date"], 44)
    assert rover["full_total_photos"] == 5
    assert rover["last_synced_at"] is not None
    # Other rovers are untouched.
    other = await rover_row(session_factory, "perseverance")
    assert (other["max_sol"], other["total_photos"], other["last_synced_at"]) == (None, 0, None)


async def test_run_failed_keeps_previous_last_synced(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await service.run(
        FakeAdapter("curiosity").add(sol_records("curiosity", 5, 1)), from_sol=5, to_sol=5
    )
    before = (await rover_row(session_factory, "curiosity"))["last_synced_at"]
    assert before is not None

    result = await service.run(
        FakeAdapter("curiosity", fail_at={9: upstream_error()}), from_sol=6, to_sol=9
    )

    assert result.status == "failed"
    after = await rover_row(session_factory, "curiosity")
    assert after["last_synced_at"] == before
    assert (after["max_sol"], after["total_photos"]) == (5, 1)


# --- advisory lock -----------------------------------------------------------------------


class HeldLock:
    """Holds pg_advisory_lock(namespace, legacy_id) on a separate connection."""

    def __init__(self, factory: SessionFactory, rover_id: str) -> None:
        self.factory = factory
        self.rover_id = rover_id

    async def __aenter__(self) -> "HeldLock":
        key = await legacy_id(self.factory, self.rover_id)
        engine = self.factory.kw["bind"]
        self.connection = await engine.connect()
        await self.connection.execute(
            text("SELECT pg_advisory_lock(:ns, :key)"), {"ns": 7_250_001, "key": key}
        )
        self.key = key
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.connection.execute(
            text("SELECT pg_advisory_unlock(:ns, :key)"), {"ns": 7_250_001, "key": self.key}
        )
        await self.connection.close()


async def lock_is_free(factory: SessionFactory, rover_id: str) -> bool:
    """Try the rover lock from another connection (and release it right away)."""
    key = await legacy_id(factory, rover_id)
    async with factory.kw["bind"].connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:ns, :key)"), {"ns": 7_250_001, "key": key}
        )
        if acquired:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:ns, :key)"), {"ns": 7_250_001, "key": key}
            )
        return bool(acquired)


async def test_run_lock_held_raises_sync_in_progress_without_run_row(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity", latest=10)

    async with HeldLock(session_factory, "curiosity"):
        with pytest.raises(SyncInProgress) as exc_info:
            await service.run(adapter)

    assert exc_info.value.rover_id == "curiosity"
    assert adapter.calls == []
    assert await sync_runs(session_factory) == []


async def test_run_lock_of_other_rover_does_not_block(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("perseverance").add(sol_records("perseverance", 3, 1))

    async with HeldLock(session_factory, "curiosity"):
        result = await service.run(adapter, from_sol=0, to_sol=5)

    assert result.status == "success"
    assert await photo_count(session_factory, "perseverance") == 1


async def test_run_parallel_run_of_same_rover_is_rejected(
    service: SyncService, session_factory: SessionFactory
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block(from_sol: int, to_sol: int) -> None:
        entered.set()
        await release.wait()

    first_adapter = FakeAdapter("curiosity", on_fetch=block).add(sol_records("curiosity", 2, 1))
    first = asyncio.create_task(service.run(first_adapter, from_sol=0, to_sol=5))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        second_adapter = FakeAdapter("curiosity")

        with pytest.raises(SyncInProgress):
            await service.run(second_adapter, from_sol=0, to_sol=5)
        assert second_adapter.calls == []
    finally:
        release.set()
        result = await asyncio.wait_for(first, timeout=10)

    assert result.status == "success"
    runs = await sync_runs(session_factory)
    assert [(r["id"], r["status"]) for r in runs] == [(result.run_id, "success")]
    assert await lock_is_free(session_factory, "curiosity")


async def test_run_releases_lock_after_normal_run(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)

    assert await lock_is_free(session_factory, "curiosity")
    again = await service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)
    assert again.status == "success"


async def test_run_releases_lock_after_failed_latest_sol(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await service.run(FakeAdapter("curiosity", latest_error=upstream_error()))

    assert await lock_is_free(session_factory, "curiosity")


async def test_run_unexpected_exception_fails_run_releases_lock_and_reraises(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity", fail_at={4: RuntimeError("adapter bug")})
    adapter.add(sol_records("curiosity", 5, 1))

    with pytest.raises(RuntimeError, match="adapter bug"):
        await service.run(adapter, from_sol=0, to_sol=5)

    [run] = await sync_runs(session_factory)
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    assert "RuntimeError" in run["error"]
    assert "adapter bug" in run["error"]
    assert await lock_is_free(session_factory, "curiosity")
    # The service is usable again.
    again = await service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)
    assert again.status == "success"


# --- end to end through the real M2020 adapter -------------------------------------------


def range_pages(sizes: list[int]) -> dict[int, Any]:
    """Pages of sol 1000 built from the recorded range fixtures; total_results = sum(sizes)."""
    recorded = {
        0: load_fixture("m2020/range_1000_page0.json"),
        3: load_fixture("m2020/range_1000_page3.json"),
    }
    total = sum(sizes)
    pages = {}
    for k, size in enumerate(sizes):
        source = recorded.get(k, recorded[0])
        images = copy.deepcopy(source["images"][:size]) if k in recorded else []
        n = 0
        while len(images) < size:
            clone = copy.deepcopy(source["images"][n % len(source["images"])])
            clone["imageid"] = f"{clone['imageid']}_P{k}_{n}"
            images.append(clone)
            n += 1
        pages[k] = {**source, "page": k if k == 0 else str(k), "images": images}
        pages[k]["total_results"] = total
    return pages


@pytest.fixture
async def m2020_adapter() -> AsyncIterator[M2020Adapter]:
    clock = FakeClock()
    upstream = make_settings(m2020_page_size=100)
    client = http.UpstreamClient(upstream, sleep=clock.sleep, clock=clock)
    try:
        yield M2020Adapter(client, upstream)
    finally:
        await client.aclose()


async def test_run_end_to_end_m2020_adapter_stores_recorded_photos(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    m2020_adapter: M2020Adapter,
) -> None:
    pages = range_pages([100, 100, 100, 85])  # live: sol 1000 -> total_results 385
    route = upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))

    result = await service.run(m2020_adapter, from_sol=1000, to_sol=1000)
    again = await service.run(m2020_adapter, from_sol=1000, to_sol=1000)

    assert (result.status, result.photos_upserted, result.failed_sols) == ("success", 385, [])
    assert again.status == "success"
    assert [query(c.request)["page"] for c in route.calls] == ["0", "1", "2", "3"] * 2
    assert {query(c.request)["condition_2"] for c in route.calls} == {"1000:sol:gte"}
    assert await photo_sols(session_factory, "perseverance") == {1000: 385}
    navcam = await scalar(
        session_factory,
        "SELECT c.code FROM photos p JOIN cameras c ON c.id = p.camera_id "
        "WHERE p.source_id = 'NLF_1000_0755730345_897ECM_N0474404NCAM00501_01_295J'",
    )
    assert navcam == "NAVCAM_LEFT"
    summary = (await summaries(session_factory, "perseverance"))[1000]
    assert (summary["total_photos"], summary["full_photos"]) == (385, 385)
    rover = await rover_row(session_factory, "perseverance")
    assert (rover["max_sol"], rover["full_max_sol"], rover["total_photos"]) == (1000, 1000, 385)
    assert rover["full_max_date"] == legacy_earth_date(rover["landing_date"], 1000)


async def test_run_end_to_end_m2020_incomplete_response_is_failed(
    service: SyncService,
    session_factory: SessionFactory,
    upstream_router: respx.MockRouter,
    m2020_adapter: M2020Adapter,
) -> None:
    pages = range_pages([100, 100])
    for page in pages.values():
        page["total_results"] = 385
    pages[2] = {**pages[1], "page": "2", "images": []}
    upstream_router.get(M2020_URL).mock(side_effect=paged_responder(pages))

    result = await service.run(m2020_adapter, from_sol=1000, to_sol=1000)

    assert result.status == "failed"
    assert result.failed_sols == [1000]
    assert "IncompleteResponse" in result.error
    assert await photo_count(session_factory) == 0


# --- incremental picks up failed sols of the previous incremental run --------------------


def hole_adapter(**kwargs: Any) -> FakeAdapter:
    """Curiosity with photos at sols 125, 120, 110 and 100; latest sol 125."""
    adapter = FakeAdapter("curiosity", latest=125, **kwargs)
    for sol in (125, 120, 110, 100):
        adapter.add(sol_records("curiosity", sol, 1))
    return adapter


async def make_hole(service: SyncService, session_factory: SessionFactory) -> SyncResult:
    """max_sol 100, latest 125: range 93..125, chunk 106..115 fails, the rest is stored."""
    await set_rover_max_sol(session_factory, "curiosity", 100)
    result = await service.run(hole_adapter(fail_at={115: upstream_error("hole")}))
    assert (result.from_sol, result.to_sol) == (93, 125)
    assert result.status == "partial"
    assert result.failed_sols == list(range(106, 116))
    assert 110 not in await photo_sols(session_factory, "curiosity")
    return result


async def test_run_incremental_fills_hole_of_previous_partial_run(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await make_hole(service, session_factory)
    assert (await rover_row(session_factory, "curiosity"))["max_sol"] == 125

    # Next run: lowest = min(max_sol 125, latest 125, pending 106) -> from 106 - 7 = 99.
    refill = hole_adapter()
    second = await service.run(refill)

    assert (second.from_sol, second.to_sol) == (99, 125)
    assert refill.fetch_calls == chunks(99, 125)
    assert (second.status, second.failed_sols) == ("success", [])
    assert set(await photo_sols(session_factory, "curiosity")) == {125, 120, 110, 100}

    # The hole is filled: the run after that starts at max_sol - overlap again.
    third = await service.run(hole_adapter())
    assert (third.from_sol, third.to_sol) == (118, 125)


async def test_run_incremental_pending_survives_latest_sol_failure(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await make_hole(service, session_factory)
    failed = await service.run(FakeAdapter("curiosity", latest_error=upstream_error()))
    assert failed.status == "failed"

    # The failed run has no range: it must not hide the pending sols of the run before.
    result = await service.run(hole_adapter())

    assert (result.from_sol, result.to_sol) == (99, 125)


async def test_run_incremental_pending_kept_after_another_partial(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await make_hole(service, session_factory)
    # The second run fails again on the same chunk -> pending stays 106..115.
    again = await service.run(hole_adapter(fail_at={115: upstream_error()}))
    assert (again.from_sol, again.status) == (99, "partial")

    result = await service.run(hole_adapter())

    assert result.from_sol == 99


async def test_run_incremental_ignores_failed_sols_of_backfill(
    service: SyncService, session_factory: SessionFactory
) -> None:
    # Backfill 190..209: chunk 200..209 stores sol 205, chunk 190..199 fails.
    backfill = FakeAdapter("curiosity", fail_at={199: upstream_error()})
    backfill.add(sol_records("curiosity", 205, 1))
    first = await service.run(backfill, from_sol=190, to_sol=209)
    assert (first.status, first.failed_sols) == ("partial", list(range(190, 200)))

    result = await service.run(FakeAdapter("curiosity", latest=210))

    # max_sol 205 - overlap; the backfill hole (190..199) is not picked up.
    assert (result.from_sol, result.to_sol) == (198, 210)


async def test_run_incremental_latest_below_max_sol_keeps_overlap(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await set_rover_max_sol(session_factory, "curiosity", 130)
    adapter = FakeAdapter("curiosity", latest=120)

    result = await service.run(adapter)

    assert (result.from_sol, result.to_sol) == (113, 120)
    assert adapter.fetch_calls == chunks(113, 120)


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(asyncio.CancelledError(), id="cancelled"),
        pytest.param(RuntimeError("adapter bug"), id="unexpected-error"),
    ],
)
async def test_run_incremental_hole_survives_crashed_run(
    service: SyncService, session_factory: SessionFactory, error: BaseException
) -> None:
    await make_hole(service, session_factory)

    # Run B crashes in its first chunk (116..125) before storing anything.
    with pytest.raises(type(error)):
        await service.run(hole_adapter(fail_at={125: error}))

    crashed = (await sync_runs(session_factory))[-1]
    assert (crashed["mode"], crashed["status"]) == ("incremental", "failed")
    assert (crashed["from_sol"], crashed["to_sol"]) == (99, 125)
    assert crashed["failed_sols"] == list(range(99, 126))

    # Run C: pending = B's whole range, so it starts at B's from_sol - overlap.
    after = await service.run(hole_adapter())
    assert after.from_sol == crashed["from_sol"] - OVERLAP
    assert (after.to_sol, after.status) == (125, "success")
    # C's range covers the whole original hole 106..115.
    assert after.from_sol <= 106 and after.to_sol >= 115
    assert 110 in await photo_sols(session_factory, "curiosity")


async def test_run_incremental_hole_survives_abandoned_run(
    service: SyncService, session_factory: SessionFactory
) -> None:
    await make_hole(service, session_factory)
    # A killed process left a newer 'running' incremental run with a known range.
    async with session_factory() as session:
        stale = await session.scalar(
            text(
                "INSERT INTO sync_runs (rover_id, mode, from_sol, to_sol, status, started_at) "
                "VALUES ('curiosity', 'incremental', 99, 125, 'running', now()) RETURNING id"
            )
        )
        await session.commit()

    result = await service.run(hole_adapter())

    stale_row = await run_by_id(session_factory, stale)
    assert stale_row["status"] == "failed"
    assert stale_row["error"].startswith("abandoned")
    assert stale_row["failed_sols"] == list(range(99, 126))
    assert result.from_sol == stale_row["from_sol"] - OVERLAP
    assert (result.to_sol, result.status) == (125, "success")
    # The range covers the whole original hole 106..115.
    assert result.from_sol <= 106 and result.to_sol >= 115
    assert 110 in await photo_sols(session_factory, "curiosity")


# --- abandoned 'running' rows ------------------------------------------------------------


async def insert_running_run(factory: SessionFactory, rover_id: str) -> int:
    async with factory() as session:
        run_id = await session.scalar(
            text(
                "INSERT INTO sync_runs (rover_id, mode, from_sol, to_sol, status, started_at) "
                "VALUES (:rover, 'incremental', 1, 2, 'running', now() - interval '1 day') "
                "RETURNING id"
            ),
            {"rover": rover_id},
        )
        await session.commit()
    return run_id


async def run_by_id(factory: SessionFactory, run_id: int) -> dict[str, Any]:
    return {run["id"]: run for run in await sync_runs(factory)}[run_id]


async def test_run_marks_leftover_running_row_of_rover_abandoned(
    service: SyncService, session_factory: SessionFactory
) -> None:
    stale = await insert_running_run(session_factory, "curiosity")
    other = await insert_running_run(session_factory, "perseverance")

    result = await service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)

    assert result.status == "success"
    stale_row = await run_by_id(session_factory, stale)
    assert stale_row["status"] == "failed"
    assert stale_row["error"].startswith("abandoned")
    assert stale_row["finished_at"] is not None
    assert (await run_by_id(session_factory, other))["status"] == "running"
    assert (await run_by_id(session_factory, result.run_id))["status"] == "success"


async def test_run_rejected_by_lock_does_not_touch_running_rows(
    service: SyncService, session_factory: SessionFactory
) -> None:
    running = await insert_running_run(session_factory, "curiosity")

    async with HeldLock(session_factory, "curiosity"):
        with pytest.raises(SyncInProgress):
            await service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)

    row = await run_by_id(session_factory, running)
    assert (row["status"], row["error"], row["finished_at"]) == ("running", None, None)


# --- lock release on failures ------------------------------------------------------------


async def advisory_lock_holders(factory: SessionFactory, rover_id: str) -> int:
    """Number of sessions holding the rover's sync lock (pg_locks, any connection)."""
    key = await legacy_id(factory, rover_id)
    return await scalar(
        factory,
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
        "AND classid = :ns AND objid = :key AND objsubid = 2",
        ns=SYNC_LOCK_NAMESPACE,
        key=key,
    )


async def assert_lock_released(
    factory: SessionFactory, rover_id: str, *, deadline: float = 2.0, step: float = 0.05
) -> None:
    """Poll pg_locks: after ``conn.invalidate()`` the server frees the lock asynchronously."""
    loop = asyncio.get_running_loop()
    until = loop.time() + deadline
    while (holders := await advisory_lock_holders(factory, rover_id)) != 0:
        assert loop.time() < until, f"{holders} session(s) still hold the {rover_id} sync lock"
        await asyncio.sleep(step)


@pytest.fixture
async def pooled_service(
    migrated_db: str, sync_settings: Settings, clean_db: SessionFactory
) -> AsyncIterator[SyncService]:
    """Service on a POOLED engine: a leaked lock would survive in the pool."""
    engine = create_async_engine(migrated_db)
    try:
        yield SyncService(async_sessionmaker(engine, expire_on_commit=False), sync_settings)
    finally:
        await engine.dispose()


def fail_unlock(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    """Make every ``pg_advisory_unlock`` statement raise ``error``."""
    original = AsyncConnection.execute

    async def execute(self: AsyncConnection, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if "pg_advisory_unlock" in str(statement):
            raise error
        return await original(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncConnection, "execute", execute)


async def test_run_unlock_error_is_logged_connection_discarded_result_kept(
    pooled_service: SyncService,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An ordinary unlock error must not mask the run result; the lock dies with the
    invalidated connection instead of going back to the pool."""
    caplog.set_level(logging.WARNING, logger="app.services.sync")
    fail_unlock(monkeypatch, RuntimeError("unlock failed"))
    adapter = FakeAdapter("curiosity").add(sol_records("curiosity", 1, 1))

    result = await pooled_service.run(adapter, from_sol=0, to_sol=1)

    monkeypatch.undo()
    assert result.status == "success"
    assert any(
        "unlock failed" in (r.getMessage() + str(r.exc_info and r.exc_info[1]))
        for r in caplog.records
        if r.name == "app.services.sync"
    ), [r.getMessage() for r in caplog.records]
    await assert_lock_released(session_factory, "curiosity")
    again = await pooled_service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)
    assert again.status == "success"
    await assert_lock_released(session_factory, "curiosity")


async def test_run_unlock_cancelled_is_reraised_and_connection_discarded(
    pooled_service: SyncService,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fail_unlock(monkeypatch, asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await pooled_service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)

    monkeypatch.undo()
    await assert_lock_released(session_factory, "curiosity")
    again = await pooled_service.run(FakeAdapter("curiosity"), from_sol=0, to_sol=1)
    assert again.status == "success"
    await assert_lock_released(session_factory, "curiosity")


async def test_run_cancelled_marks_run_failed_releases_lock_and_reraises(
    service: SyncService, session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.services.sync")
    entered = asyncio.Event()

    async def block(from_sol: int, to_sol: int) -> None:
        entered.set()
        await asyncio.Event().wait()

    adapter = FakeAdapter("curiosity", on_fetch=block)
    task = asyncio.create_task(service.run(adapter, from_sol=0, to_sol=5))
    await asyncio.wait_for(entered.wait(), timeout=10)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    [run] = await sync_runs(session_factory)
    assert run["status"] == "failed"
    assert run["finished_at"] is not None
    await assert_lock_released(session_factory, "curiosity")
    assert await lock_is_free(session_factory, "curiosity")
    sync_logs = [r for r in caplog.records if r.name == "app.services.sync"]
    assert any(r.levelno == logging.WARNING for r in sync_logs)
    assert not [r for r in sync_logs if r.levelno >= logging.ERROR], [
        r.getMessage() for r in sync_logs
    ]
