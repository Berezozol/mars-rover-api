"""CLI ``python -m app.cli backfill-meta`` (stage 18B-2, AC-15).

Contract (docs/proposals/stage-18b-filters.md section 6):
``backfill-meta [--rover R] [--from-sol N] [--batch-sols 50]`` fills `local_time_s`, `width`,
`height` of stored photos from `extra` (the same rules as the write path), by rover and batches of
sols, one commit per batch; it is idempotent (`... IS DISTINCT FROM ...`: a second run writes
nothing) and never touches the network.

Assumption about `--from-sol` (the document does not give the direction): sols below N are left
alone, sols from N up are processed, so a run restarted with `--from-sol N` after an interruption
completes the same set of rows. See the report.

Photos are seeded through the write path (tests/api/v2/_meta.py dataset) and then emptied with SQL
to look like rows stored before migration 0013. ``cli.main`` runs its own event loop, so it is
called through ``asyncio.to_thread``.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import Settings, get_settings
from tests.api.v2._meta import ALL_ROVERS, MRow
from tests.integration._fakes import fetch_all
from tests.integration._meta_seed import (
    expected_meta,
    null_meta_columns,
    seed_meta_rows,
    stored_meta,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]
EMPTY = (None, None, None)
OTHER_COLUMNS_SQL = (
    "SELECT id, to_jsonb(p) - 'local_time_s' - 'width' - 'height' - 'updated_at' AS rest "
    "FROM photos p ORDER BY id"
)


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    """The CLI reads Settings from the environment: test DB, sync off, no pauses."""
    for key, value in {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def run_cli(*argv: str) -> int:
    try:
        return await asyncio.to_thread(cli.main, ["backfill-meta", *argv])
    except SystemExit as exc:  # argparse usage errors
        return exc.code if isinstance(exc.code, int) else 1


@pytest.fixture
async def world(clean_db: SessionFactory) -> list[MRow]:
    """Every rover's photos stored, then the three columns emptied (the pre-0013 state)."""
    rows = await seed_meta_rows(clean_db)
    await null_meta_columns(clean_db)
    assert set((await stored_meta(clean_db)).values()) == {EMPTY}
    return rows


def assert_filled(stored: dict[str, tuple], rows: list[MRow]) -> None:
    for row in rows:
        assert stored[row.source_id] == expected_meta(row), row.source_id


# --- AC-15: fills ------------------------------------------------------------------------


async def test_ac15_backfill_meta_fills_empty_columns_of_every_rover(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    code = await run_cli()

    assert code == 0
    stored = await stored_meta(clean_db)
    assert len(stored) == len(world)
    assert_filled(stored, world)
    # Sanity of the reference: both kinds of rows exist in the dataset.
    assert any(expected_meta(r) == EMPTY for r in world)
    assert any(expected_meta(r)[0] is not None for r in world)
    assert {r.rover for r in world if expected_meta(r)[1] is not None} == set(ALL_ROVERS) - {
        "curiosity"
    }


async def test_ac15_rows_without_data_stay_null(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    await run_cli()

    stored = await stored_meta(clean_db)
    nothing = [r for r in world if r.local_time is None and r.size is None]
    assert nothing
    assert all(stored[r.source_id] == EMPTY for r in nothing)


async def test_ac15_size_over_32767_stays_null_while_time_is_filled(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    await run_cli()

    stored = await stored_meta(clean_db)
    traps = [r for r in world if r.sol == 102]
    assert len(traps) == 2
    assert all(stored[r.source_id] == (12 * 3600, None, None) for r in traps)


async def test_ac15_curiosity_gets_time_but_never_a_size(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    await run_cli()

    stored = await stored_meta(clean_db)
    curiosity = [r for r in world if r.rover == "curiosity"]
    assert all(stored[r.source_id][1:] == (None, None) for r in curiosity)
    assert any(stored[r.source_id][0] is not None for r in curiosity)


async def test_ac15_backfill_meta_does_not_change_other_columns(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    before = await fetch_all(clean_db, OTHER_COLUMNS_SQL)

    await run_cli()

    assert await fetch_all(clean_db, OTHER_COLUMNS_SQL) == before


async def test_ac15_wrong_stored_values_are_corrected(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    await run_cli()
    target = next(r for r in world if r.rover == "perseverance" and r.size and r.local_time)
    async with clean_db() as session:
        await session.execute(
            text(
                "UPDATE photos SET local_time_s = 1, width = 7, height = 7 WHERE source_id = :sid"
            ),
            {"sid": target.source_id},
        )
        await session.commit()

    await run_cli()

    assert (await stored_meta(clean_db))[target.source_id] == expected_meta(target)


async def test_ac15_empty_database_is_a_clean_no_op(clean_db: SessionFactory) -> None:
    assert await run_cli() == 0


# --- AC-15: idempotence ------------------------------------------------------------------


async def test_ac15_second_run_changes_zero_rows(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    assert await run_cli() == 0
    versions = "SELECT id, xmin::text AS xmin, updated_at FROM photos ORDER BY id"
    before = await fetch_all(clean_db, versions)
    stored_before = await stored_meta(clean_db)

    assert await run_cli() == 0

    # No UPDATE happened: every tuple keeps its transaction id.
    assert await fetch_all(clean_db, versions) == before
    assert await stored_meta(clean_db) == stored_before
    assert_filled(stored_before, world)


# --- AC-15: --rover ----------------------------------------------------------------------


async def test_ac15_rover_option_limits_the_work_to_one_rover(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    assert await run_cli("--rover", "perseverance") == 0

    stored = await stored_meta(clean_db)
    for row in world:
        want = expected_meta(row) if row.rover == "perseverance" else EMPTY
        assert stored[row.source_id] == want, row.source_id


async def test_ac15_rover_runs_one_after_another_add_up_to_a_full_run(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    for rover in ALL_ROVERS:
        assert await run_cli("--rover", rover) == 0

    assert_filled(await stored_meta(clean_db), world)


async def test_ac15_unknown_rover_is_rejected_and_nothing_is_written(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    code = await run_cli("--rover", "nope")

    assert code != 0
    assert set((await stored_meta(clean_db)).values()) == {EMPTY}


# --- AC-15: batches and --from-sol -------------------------------------------------------


@pytest.mark.parametrize("batch", ["1", "2", "50", "1000"])
async def test_ac15_any_batch_size_gives_the_same_result(
    clean_db: SessionFactory, world: list[MRow], batch: str
) -> None:
    assert await run_cli("--batch-sols", batch) == 0

    assert_filled(await stored_meta(clean_db), world)


async def test_ac15_sparse_sols_are_all_reached_with_small_batches(
    clean_db: SessionFactory,
) -> None:
    rows = await seed_meta_rows(clean_db, ["perseverance"])
    far = rows[0]
    async with clean_db() as session:
        await session.execute(
            text("UPDATE photos SET sol = 90000 WHERE source_id = :sid"), {"sid": far.source_id}
        )
        await session.commit()
    await null_meta_columns(clean_db)

    assert await run_cli("--rover", "perseverance", "--batch-sols", "3") == 0

    assert_filled(await stored_meta(clean_db), rows)


async def test_ac15_from_sol_leaves_lower_sols_alone(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    assert await run_cli("--from-sol", "101") == 0

    stored = await stored_meta(clean_db)
    for row in world:
        want = expected_meta(row) if row.sol >= 101 else EMPTY
        assert stored[row.source_id] == want, row.source_id


async def test_ac15_restart_with_from_sol_after_partial_run_gives_the_full_result(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    """An interrupted run (simulated by a partial one) is completed by a later run."""
    assert await run_cli("--from-sol", "102") == 0
    assert await run_cli("--from-sol", "100", "--batch-sols", "1") == 0

    assert_filled(await stored_meta(clean_db), world)


async def test_ac15_from_sol_with_rover_combines(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    assert await run_cli("--rover", "spirit", "--from-sol", "101") == 0

    stored = await stored_meta(clean_db)
    for row in world:
        done = row.rover == "spirit" and row.sol >= 101
        assert stored[row.source_id] == (expected_meta(row) if done else EMPTY), row.source_id


# --- no network --------------------------------------------------------------------------


async def test_ac15_backfill_meta_makes_no_http_request(
    clean_db: SessionFactory, world: list[MRow], upstream_router: respx.MockRouter
) -> None:
    assert await run_cli() == 0

    assert upstream_router.calls.call_count == 0


# --- reviewer findings: usage error and the race with a concurrent write ---------------------


async def test_ac15_to_sol_below_from_sol_is_a_usage_error_and_writes_nothing(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    code = await run_cli("--from-sol", "105", "--to-sol", "100")

    assert code == 2
    assert set((await stored_meta(clean_db)).values()) == {EMPTY}


async def test_ac15_to_sol_limits_the_range_from_above(
    clean_db: SessionFactory, world: list[MRow]
) -> None:
    assert await run_cli("--to-sol", "100") == 0

    stored = await stored_meta(clean_db)
    for row in world:
        want = expected_meta(row) if row.sol <= 100 else EMPTY
        assert stored[row.source_id] == want, row.source_id


async def test_ac15_concurrent_upsert_between_read_and_write_is_not_overwritten(
    clean_db: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A photo rewritten by a sync after the batch read keeps its newer values.

    The batch reads photo A with the old `extra` (columns empty); before its UPDATE a sync
    upserts A with a new `extra` (and the matching columns, newer `updated_at`). Writing the
    stale snapshot would put the old values back; the final state must be the new ones.
    """
    from app.repositories import photos as photos_repo
    from app.repositories.photos import upsert_photos
    from app.services.meta_backfill import MetaBackfillService
    from tests.conftest import build_record

    def record(source_id: str, clock: str, size: str) -> Any:
        return build_record(
            source_id=source_id,
            sol=5,
            camera_code="NAVCAM_LEFT",
            extra={"date_taken_mars": f"Sol-00005M{clock}.000", "extended": {"dimension": size}},
        )

    async with clean_db() as session:
        await upsert_photos(
            session,
            "perseverance",
            [record("race-a", "01:00:00", "(100,100)"), record("race-b", "03:00:00", "(300,300)")],
        )
        await session.commit()
    await null_meta_columns(clean_db)

    real = photos_repo.meta_backfill_batch
    raced: list[bool] = []

    async def racing_batch(session: AsyncSession, rover_id: str, lo: int, hi: int) -> Any:
        original = session.execute

        async def execute(*args: Any, **kwargs: Any) -> Any:
            result = await original(*args, **kwargs)
            if not raced:  # right after the batch's read, before its write
                raced.append(True)
                async with clean_db() as other:
                    await upsert_photos(
                        other, "perseverance", [record("race-a", "02:00:00", "(200,200)")]
                    )
                    await other.commit()
            return result

        session.execute = execute  # type: ignore[method-assign]
        return await real(session, rover_id, lo, hi)

    monkeypatch.setattr(photos_repo, "meta_backfill_batch", racing_batch)

    await MetaBackfillService(clean_db).run(["perseverance"])

    assert raced
    stored = await stored_meta(clean_db)
    assert stored["race-a"] == (2 * 3600, 200, 200), "stale snapshot overwrote a newer write"
    assert stored["race-b"] == (3 * 3600, 300, 300)  # the untouched photo of the batch is filled
    monkeypatch.setattr(photos_repo, "meta_backfill_batch", real)
    await MetaBackfillService(clean_db).run(["perseverance"])
    assert (await stored_meta(clean_db))["race-a"] == (2 * 3600, 200, 200)
