"""sync_runs repository and the rover cache helpers (refresh_rover_stats, touch_last_synced).

Repositories never commit, so these tests use the rolled-back `db_session`.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.legacy_dates import legacy_earth_date
from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories import sync_runs as runs_repo

pytestmark = pytest.mark.integration

MakeRecord = Callable[..., Any]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def run_row(session: AsyncSession, run_id: int) -> dict[str, Any]:
    result = await session.execute(text("SELECT * FROM sync_runs WHERE id = :id"), {"id": run_id})
    return dict(result.mappings().one())


async def rover_row(session: AsyncSession, rover_id: str) -> dict[str, Any]:
    result = await session.execute(text("SELECT * FROM rovers WHERE id = :id"), {"id": rover_id})
    return dict(result.mappings().one())


async def set_started_at(session: AsyncSession, run_id: int, started_at: datetime) -> None:
    await session.execute(
        text("UPDATE sync_runs SET started_at = :at WHERE id = :id"),
        {"at": started_at, "id": run_id},
    )


@pytest.fixture(autouse=True)
async def empty_sync_tables(db_session: AsyncSession) -> None:
    """Start from empty sync tables inside the rolled-back transaction (robust to leftovers)."""
    for table in ("photos", "sol_summaries", "sync_runs"):
        await db_session.execute(text(f"DELETE FROM {table}"))


# --- create / update / finish ------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "from_sol", "to_sol"),
    [
        pytest.param("incremental", None, None, id="incremental-range-unknown"),
        pytest.param("backfill", 10, 20, id="backfill"),
    ],
)
async def test_create_run_inserts_running_row(
    db_session: AsyncSession, mode: str, from_sol: int | None, to_sol: int | None
) -> None:
    run = await runs_repo.create_run(db_session, "curiosity", mode, from_sol, to_sol)

    assert run.id is not None
    row = await run_row(db_session, run.id)
    assert row["rover_id"] == "curiosity"
    assert row["mode"] == mode
    assert (row["from_sol"], row["to_sol"]) == (from_sol, to_sol)
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["finished_at"] is None
    assert row["photos_upserted"] == 0
    assert row["error"] is None
    assert row["failed_sols"] == []


async def test_update_run_range_sets_bounds(db_session: AsyncSession) -> None:
    run = await runs_repo.create_run(db_session, "perseverance", "incremental", None, None)

    await runs_repo.update_run_range(db_session, run.id, 2024, 2031)

    row = await run_row(db_session, run.id)
    assert (row["from_sol"], row["to_sol"], row["status"]) == (2024, 2031, "running")


@pytest.mark.parametrize(
    ("status", "photos", "error", "failed_sols", "stored"),
    [
        pytest.param("success", 42, None, [], [], id="success"),
        pytest.param(
            "partial",
            7,
            "failed sols: 3-5 ; last error: X: y",
            [5, 3, 4, 5],
            [3, 4, 5],
            id="partial-sorted-unique",
        ),
        pytest.param("failed", 0, "UpstreamError('down')", [1, 2, 3, 4, 5], [1, 2, 3, 4, 5],
                     id="failed"),
    ],
)  # fmt: skip
async def test_finish_run_sets_status_counts_error_and_finished_at(
    db_session: AsyncSession,
    status: str,
    photos: int,
    error: str | None,
    failed_sols: list[int],
    stored: list[int],
) -> None:
    run = await runs_repo.create_run(db_session, "curiosity", "backfill", 1, 5)

    await runs_repo.finish_run(
        db_session,
        run.id,
        status=status,
        photos_upserted=photos,
        error=error,
        failed_sols=failed_sols,
    )

    row = await run_row(db_session, run.id)
    assert (row["status"], row["photos_upserted"], row["error"]) == (status, photos, error)
    assert row["failed_sols"] == stored
    assert row["finished_at"] is not None
    assert row["finished_at"] >= row["started_at"]
    assert (row["from_sol"], row["to_sol"]) == (1, 5)


async def test_finish_run_touches_only_given_run(db_session: AsyncSession) -> None:
    first = await runs_repo.create_run(db_session, "curiosity", "backfill", 1, 5)
    second = await runs_repo.create_run(db_session, "curiosity", "backfill", 6, 9)

    await runs_repo.finish_run(
        db_session, first.id, status="success", photos_upserted=1, error=None, failed_sols=[]
    )

    assert (await run_row(db_session, second.id))["status"] == "running"
    assert (await run_row(db_session, second.id))["finished_at"] is None


# --- pending_failed_sols -----------------------------------------------------------------


async def add_run(
    session: AsyncSession,
    rover_id: str,
    mode: str,
    status: str,
    *,
    started_at: datetime,
    failed_sols: list[int] | None = None,
    sol_range: tuple[int, int] | None = (10, 20),
) -> int:
    """Insert a run row directly (any status, range and start time)."""
    finished = status != "running"
    return await session.scalar(
        text(
            "INSERT INTO sync_runs (rover_id, mode, from_sol, to_sol, status, started_at, "
            "finished_at, failed_sols) VALUES (:rover, :mode, :from_sol, :to_sol, :status, "
            ":started_at, :finished_at, :failed_sols) RETURNING id"
        ),
        {
            "rover": rover_id,
            "mode": mode,
            "from_sol": sol_range[0] if sol_range else None,
            "to_sol": sol_range[1] if sol_range else None,
            "status": status,
            "started_at": started_at,
            "finished_at": started_at + timedelta(minutes=5) if finished else None,
            "failed_sols": failed_sols or [],
        },
    )


def at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


async def test_pending_failed_sols_no_runs_is_empty(db_session: AsyncSession) -> None:
    assert await runs_repo.pending_failed_sols(db_session, "curiosity") == []


@pytest.mark.parametrize("status", ["partial", "failed"])
async def test_pending_failed_sols_from_last_finished_incremental(
    db_session: AsyncSession, status: str
) -> None:
    await add_run(
        db_session, "curiosity", "incremental", "partial", started_at=at(0), failed_sols=[1, 2]
    )
    await add_run(db_session, "curiosity", "incremental", status, started_at=at(1),
                  failed_sols=[12, 15])  # fmt: skip

    assert await runs_repo.pending_failed_sols(db_session, "curiosity") == [12, 15]


async def test_pending_failed_sols_last_incremental_success_clears(
    db_session: AsyncSession,
) -> None:
    await add_run(
        db_session, "curiosity", "incremental", "partial", started_at=at(0), failed_sols=[12]
    )
    await add_run(db_session, "curiosity", "incremental", "success", started_at=at(1))  # fmt: skip

    assert await runs_repo.pending_failed_sols(db_session, "curiosity") == []


@pytest.mark.parametrize(
    ("mode", "status", "sol_range"),
    [
        pytest.param("incremental", "running", (10, 20), id="running-not-finished"),
        pytest.param("backfill", "success", (10, 20), id="backfill-success"),
        pytest.param("backfill", "partial", (10, 20), id="backfill-partial"),
        pytest.param("incremental", "failed", None, id="latest-sol-failed-null-range"),
    ],
)
async def test_pending_failed_sols_skips_runs_that_do_not_count(
    db_session: AsyncSession, mode: str, status: str, sol_range: tuple[int, int] | None
) -> None:
    await add_run(
        db_session, "curiosity", "incremental", "partial", started_at=at(0), failed_sols=[12, 13]
    )
    await add_run(db_session, "curiosity", mode, status, started_at=at(1), sol_range=sol_range,
                  failed_sols=[99] if status != "running" else [])  # fmt: skip

    assert await runs_repo.pending_failed_sols(db_session, "curiosity") == [12, 13]


async def test_pending_failed_sols_other_rover_ignored(db_session: AsyncSession) -> None:
    await add_run(
        db_session, "curiosity", "incremental", "partial", started_at=at(0), failed_sols=[12]
    )
    await add_run(db_session, "perseverance", "incremental", "success", started_at=at(1))
    await add_run(db_session, "perseverance", "incremental", "partial", started_at=at(-1),
                  failed_sols=[7])  # fmt: skip

    assert await runs_repo.pending_failed_sols(db_session, "curiosity") == [12]
    assert await runs_repo.pending_failed_sols(db_session, "perseverance") == []
    assert await runs_repo.pending_failed_sols(db_session, "spirit") == []


async def test_abandon_running_runs_fills_full_range_only_for_incremental_with_range(
    db_session: AsyncSession,
) -> None:
    incremental = await add_run(
        db_session, "curiosity", "incremental", "running", started_at=at(0), sol_range=(10, 14)
    )
    no_range = await add_run(
        db_session, "curiosity", "incremental", "running", started_at=at(1), sol_range=None
    )
    backfill = await add_run(
        db_session, "curiosity", "backfill", "running", started_at=at(2), sol_range=(30, 33)
    )
    finished = await add_run(
        db_session, "curiosity", "incremental", "partial", started_at=at(-1), failed_sols=[3]
    )
    other = await add_run(db_session, "perseverance", "incremental", "running",
                          started_at=at(0), sol_range=(5, 6))  # fmt: skip

    abandoned = await runs_repo.abandon_running_runs(db_session, "curiosity")

    assert abandoned == 3
    rows = {run_id: await run_row(db_session, run_id)
            for run_id in (incremental, no_range, backfill, finished, other)}  # fmt: skip
    for run_id in (incremental, no_range, backfill):
        assert rows[run_id]["status"] == "failed"
        assert rows[run_id]["error"].startswith("abandoned")
        assert rows[run_id]["finished_at"] is not None
    assert rows[incremental]["failed_sols"] == [10, 11, 12, 13, 14]
    assert rows[no_range]["failed_sols"] == []
    assert rows[backfill]["failed_sols"] == []
    assert (rows[finished]["status"], rows[finished]["failed_sols"]) == ("partial", [3])
    assert (rows[other]["status"], rows[other]["failed_sols"]) == ("running", [])


# --- list / count ------------------------------------------------------------------------


@pytest.fixture
async def runs(db_session: AsyncSession) -> dict[str, int]:
    """Five runs with explicit start times; two of them share one start time."""
    spec = [
        ("c_old", "curiosity", T0),
        ("p_mid", "perseverance", T0 + timedelta(hours=1)),
        ("c_tie_a", "curiosity", T0 + timedelta(hours=2)),
        ("c_tie_b", "curiosity", T0 + timedelta(hours=2)),
        ("p_new", "perseverance", T0 + timedelta(hours=3)),
    ]
    ids = {}
    for name, rover, started_at in spec:
        run = await runs_repo.create_run(db_session, rover, "backfill", 0, 1)
        await set_started_at(db_session, run.id, started_at)
        ids[name] = run.id
    assert ids["c_tie_b"] > ids["c_tie_a"]
    return ids


async def test_list_runs_newest_first_ties_by_id_desc(
    db_session: AsyncSession, runs: dict[str, int]
) -> None:
    result = await runs_repo.list_runs(db_session)

    assert [r.id for r in result] == [
        runs["p_new"],
        runs["c_tie_b"],
        runs["c_tie_a"],
        runs["p_mid"],
        runs["c_old"],
    ]


async def test_list_runs_filter_by_rover(db_session: AsyncSession, runs: dict[str, int]) -> None:
    result = await runs_repo.list_runs(db_session, rover_id="curiosity")

    assert [r.id for r in result] == [runs["c_tie_b"], runs["c_tie_a"], runs["c_old"]]
    assert {r.rover_id for r in result} == {"curiosity"}


@pytest.mark.parametrize(
    ("limit", "offset", "expected"),
    [
        pytest.param(2, 0, ["p_new", "c_tie_b"], id="first-page"),
        pytest.param(2, 2, ["c_tie_a", "p_mid"], id="second-page"),
        pytest.param(2, 4, ["c_old"], id="last-page"),
        pytest.param(25, 5, [], id="past-the-end"),
    ],
)
async def test_list_runs_limit_offset(
    db_session: AsyncSession,
    runs: dict[str, int],
    limit: int,
    offset: int,
    expected: list[str],
) -> None:
    result = await runs_repo.list_runs(db_session, limit=limit, offset=offset)

    assert [r.id for r in result] == [runs[name] for name in expected]


async def test_list_runs_default_limit_is_25(db_session: AsyncSession) -> None:
    for _ in range(27):
        await runs_repo.create_run(db_session, "curiosity", "backfill", 0, 1)

    assert len(await runs_repo.list_runs(db_session)) == 25


@pytest.mark.parametrize(
    ("rover_id", "expected"),
    [
        pytest.param(None, 5, id="all"),
        pytest.param("curiosity", 3, id="curiosity"),
        pytest.param("perseverance", 2, id="perseverance"),
        pytest.param("spirit", 0, id="no-runs"),
    ],
)
async def test_count_runs(
    db_session: AsyncSession, runs: dict[str, int], rover_id: str | None, expected: int
) -> None:
    assert await runs_repo.count_runs(db_session, rover_id=rover_id) == expected


# --- refresh_rover_stats / touch_last_synced ---------------------------------------------


async def store(session: AsyncSession, rover_id: str, records: list[Any]) -> None:
    await photos_repo.upsert_photos(session, rover_id, records)
    await photos_repo.recompute_sol_summaries(session, rover_id, {r.sol for r in records})


async def test_refresh_rover_stats_from_sol_summaries(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    # Sol 12: subframes only (max_sol); sol 11: 2 full + 1 subframe; sol 3: 1 full.
    records = [
        make_record(sol=12, sample_type="subframe", earth_date=date(2020, 2, 12)),
        make_record(sol=12, sample_type="subframe", earth_date=date(2020, 2, 13)),
        make_record(sol=11, earth_date=date(2020, 2, 11)),
        make_record(sol=11, earth_date=date(2020, 2, 11)),
        make_record(sol=11, sample_type="subframe", earth_date=date(2020, 2, 11)),
        make_record(sol=3, earth_date=date(2020, 2, 3)),
    ]
    await store(db_session, "curiosity", records)
    # Another rover's data must not leak in.
    await store(db_session, "perseverance", [make_record(sol=500, camera_code="SKYCAM")])

    await rovers_repo.refresh_rover_stats(db_session, "curiosity")

    rover = await rover_row(db_session, "curiosity")
    assert rover["max_sol"] == 12
    assert rover["max_date"] == date(2020, 2, 12)  # earth_date of the max-sol summary row
    assert rover["total_photos"] == 6
    assert rover["full_max_sol"] == 11
    assert rover["full_max_date"] == legacy_earth_date(date(2012, 8, 6), 11)
    assert rover["full_total_photos"] == 3
    assert rover["last_synced_at"] is None


async def test_refresh_rover_stats_without_summaries_resets_to_empty(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(
        text(
            "UPDATE rovers SET max_sol = 9, max_date = '2020-01-01', total_photos = 5, "
            "full_max_sol = 9, full_max_date = '2020-01-01', full_total_photos = 5 "
            "WHERE id = 'spirit'"
        )
    )
    await db_session.execute(text("DELETE FROM sol_summaries WHERE rover_id = 'spirit'"))

    await rovers_repo.refresh_rover_stats(db_session, "spirit")

    rover = await rover_row(db_session, "spirit")
    assert (rover["max_sol"], rover["max_date"], rover["total_photos"]) == (None, None, 0)
    assert (rover["full_max_sol"], rover["full_max_date"], rover["full_total_photos"]) == (
        None,
        None,
        0,
    )


async def test_refresh_rover_stats_without_full_photos(
    db_session: AsyncSession, make_record: MakeRecord
) -> None:
    await store(
        db_session,
        "curiosity",
        [make_record(sol=4, sample_type="downsampled", earth_date=date(2020, 3, 4))],
    )

    await rovers_repo.refresh_rover_stats(db_session, "curiosity")

    rover = await rover_row(db_session, "curiosity")
    assert (rover["max_sol"], rover["max_date"], rover["total_photos"]) == (
        4,
        date(2020, 3, 4),
        1,
    )
    assert (rover["full_max_sol"], rover["full_max_date"], rover["full_total_photos"]) == (
        None,
        None,
        0,
    )


async def test_touch_last_synced_sets_now_only_for_that_rover(db_session: AsyncSession) -> None:
    await db_session.execute(text("UPDATE rovers SET last_synced_at = NULL"))
    before = await db_session.scalar(text("SELECT now()"))

    await rovers_repo.touch_last_synced(db_session, "perseverance")

    rover = await rover_row(db_session, "perseverance")
    assert rover["last_synced_at"] is not None
    assert rover["last_synced_at"] >= before - timedelta(seconds=1)
    assert (await rover_row(db_session, "curiosity"))["last_synced_at"] is None


async def test_refresh_rover_stats_keeps_last_synced(db_session: AsyncSession) -> None:
    await rovers_repo.touch_last_synced(db_session, "curiosity")
    touched = (await rover_row(db_session, "curiosity"))["last_synced_at"]

    await rovers_repo.refresh_rover_stats(db_session, "curiosity")

    assert (await rover_row(db_session, "curiosity"))["last_synced_at"] == touched
