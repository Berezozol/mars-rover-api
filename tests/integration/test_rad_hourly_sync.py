"""Hourly RAD radiation in the loader: `rad sync` and `rad sync --reprocess` (stage 22, phase C).

Spec: docs/proposals/sol-conditions.md, AC-41 ... AC-44, AC-52 (offline part), AC-58 (loader
part). The PDS host is the respx-based `tests/_rad.RadHost`; asyncio sleeps are faked.
"Before stage 22" databases are made by `reset_hourly`: the sols are loaded normally, then
`hourly_revision` goes back to 0 and the hours are deleted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text

from app.repositories import rad as rad_repo
from tests._rad import (
    CHECKSUMS,
    F_1649_A,
    F_1649_B,
    F_4842,
    F_4843,
    F_SOL10,
    TIMING,
    Factory,
    RadHost,
    daily_row,
    fixture_host,
    insert_daily,
    obs_block,
    rad_daily,
    rad_file,
    rad_files,
    rad_hourly,
    rel_path,
    reset_hourly,
    run_rad,
    synthetic_host,
    synthetic_name,
    synthetic_path,
    utc_text,
    without_hourly,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("clean_db", "rad_env", "fake_sleep"),
]

P10 = rel_path(F_SOL10)
P4842 = rel_path(F_4842)
P4843 = rel_path(F_4843)
P1649_A = rel_path(F_1649_A)
P1649_B = rel_path(F_1649_B)
V2 = "/api/v2"


@pytest.fixture
def make_host(upstream_router: respx.MockRouter, events: list[tuple[str, Any]]) -> Any:
    def make(*names: str) -> RadHost:
        return fixture_host(upstream_router, events, *names)

    return make


@pytest.fixture
def host_4842_4843(make_host: Any) -> RadHost:
    return make_host(F_4842, F_4843)


def approx(value: float) -> Any:
    return pytest.approx(value, rel=1e-9)


async def sync_two(*extra: str) -> int:
    return await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843", *extra)


async def load_and_reset_two(factory: Factory, host: RadHost) -> tuple[Any, Any]:
    """Load 4842 and 4843, go back to the pre-stage-22 state; returns the two daily rows."""
    assert await sync_two() == 0
    await reset_hourly(factory)
    host.requests.clear()
    return await daily_row(factory, 4842), await daily_row(factory, 4843)


def paths_of(host: RadHost) -> list[str]:
    return [r.path for r in host.calls()]


# --- AC-44: a normal sync computes the hours -------------------------------------------------


async def test_sync_new_sol_stores_daily_row_hours_and_revision_together_ac44(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """AC-44: sol 4843 -> one hour row (hour 0, observations 0 and 2), `hourly_revision` 1."""
    assert await sync_two() == 0

    row = await daily_row(clean_db, 4843)
    assert row is not None
    assert (row["hourly_revision"], row["hourly_reject"], row["hourly_dropped"]) == (1, None, 1)
    (hour,) = await rad_hourly(clean_db, 4843)
    assert hour["hour"] == 0
    assert hour["n_obs"] == 2
    assert hour["live_s"] == approx(1860.0)
    assert hour["dose_b_mean"] == approx((6.84128 + 6.78671) / 2)
    assert hour["dose_e_mean"] == approx((6.58689 + 6.53178) / 2)
    assert hour["bu_count"] == 6454 + 6487


async def test_sync_sol_with_the_time_of_another_sol_is_rejected_off_sol_ac44(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """The 4842 fixture has the observation times of sol 4843: no hours, `off_sol`, all dropped."""
    assert await sync_two() == 0

    row = await daily_row(clean_db, 4842)
    assert row is not None
    assert (row["hourly_revision"], row["hourly_reject"]) == (1, "off_sol")
    assert row["hourly_dropped"] == row["n_used"] == 3
    assert await rad_hourly(clean_db, 4842) == []


async def test_sync_fixture_sol_10_has_the_hours_0_and_23_and_one_dropped_ac44(
    make_host: Any, clean_db: Factory
) -> None:
    """Sol 10 (no OBS_DURATION): obs 0 -> hour 0, obs 25 -> hour 23, obs 12 (garbage UTC) dropped,
    obs 13 is not used at all (low rate)."""
    make_host(F_SOL10)

    assert await run_rad("sync", "--from-sol", "10", "--to-sol", "10") == 0

    row = await daily_row(clean_db, 10)
    assert row is not None
    assert (row["n_used"], row["n_dropped_low_rate"]) == (3, 1)
    assert (row["hourly_revision"], row["hourly_reject"], row["hourly_dropped"]) == (1, None, 1)
    hours = await rad_hourly(clean_db, 10)
    assert [(h["hour"], h["n_obs"], h["bu_count"]) for h in hours] == [(0, 1, 7817), (23, 1, 7707)]
    assert [h["live_s"] for h in hours] == [960.0, 960.0]
    assert hours[0]["dose_b_mean"] == approx(8.40398)
    assert hours[1]["dose_e_mean"] == approx(8.26651)


async def test_sync_fixture_sol_1649_is_rejected_off_sol_and_keeps_its_day_ac44(
    make_host: Any, clean_db: Factory
) -> None:
    """AC-57 end to end: used 1, 3, 17 and two of them are in sol 1648 by UTC."""
    make_host(F_1649_A, F_1649_B)

    assert await run_rad("sync", "--from-sol", "1649", "--to-sol", "1649") == 0

    row = await daily_row(clean_db, 1649)
    assert row is not None
    assert (row["n_used"], row["n_dropped_qc"]) == (3, 1)
    assert (row["hourly_revision"], row["hourly_reject"], row["hourly_dropped"]) == (
        1,
        "off_sol",
        3,
    )
    assert await rad_hourly(clean_db, 1649) == []


async def test_sync_deleting_a_sol_from_rad_daily_deletes_its_hours_ac44(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """AC-44: `ON DELETE CASCADE` from `rad_daily` to `rad_hourly`."""
    assert await sync_two() == 0
    assert len(await rad_hourly(clean_db)) == 1

    async with clean_db() as session:
        await session.execute(text("DELETE FROM rad_daily WHERE sol = 4843"))
        await session.commit()

    assert await rad_hourly(clean_db) == []


async def test_sync_hours_and_daily_row_are_one_transaction_ac44(
    host_4842_4843: RadHost, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-44: when writing the hours fails, the sol's daily row is not committed either."""

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the hours cannot be written")

    monkeypatch.setattr(rad_repo, "replace_hourly", broken)

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843", crash_ok=True)

    assert code != 0
    assert await rad_daily(clean_db) == []
    assert await rad_hourly(clean_db) == []
    assert [f for f in await rad_files(clean_db) if f["status"] == "loaded"] == []


# --- AC-44, AC-58: a synthetic sol with every kind of observation ----------------------------

BASE = datetime(2026, 3, 21, 17, 18, 0, tzinfo=UTC)  # LMST 00:06 of sol 4843


def at(seconds: float) -> str:
    return utc_text(BASE + timedelta(seconds=seconds))


def mixed_file() -> bytes:
    """Sol 4843: 4 plain observations (2 in hour 0, 2 in hour 1), one QC failure, one low rate,
    one implausible dose, one garbage UTC and one that is already in sol 4844."""
    blocks = [
        obs_block(0, dose_b=6.0, dose_e=5.0, utc=at(0)),
        obs_block(2, dose_b=6.4, dose_e=5.4, utc=at(1008)),
        obs_block(4, qc="00000001", utc=at(2016)),
        obs_block(6, dose_b=0.5, dose_e=5.0, utc=at(3024)),
        obs_block(8, count=100, utc=at(4032)),
        obs_block(10, dose_b=7.0, dose_e=6.0, utc=at(5040)),
        obs_block(12, dose_b=7.4, dose_e=6.4, utc=at(6048)),
        obs_block(14, utc="garbage"),
        obs_block(16, utc=at(88_800)),
    ]
    return rad_file(4843, blocks, start_time=at(0))


@pytest.fixture
def mixed(upstream_router: respx.MockRouter, events: list[tuple[str, Any]]) -> RadHost:
    path = synthetic_path(synthetic_name(4843))
    return synthetic_host(upstream_router, events, {path: mixed_file()})


async def test_sync_mixed_sol_hours_hold_only_what_the_day_counted_ac44(
    mixed: RadHost, clean_db: Factory
) -> None:
    """AC-37 / AC-44 / AC-58 through the loader: QC, low rate and implausible dose are out of
    the day and of the hours; the garbage UTC and the foreign observation are in the day only."""
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    row = await daily_row(clean_db, 4843)
    assert row is not None
    assert (
        row["n_used"],
        row["n_dropped_qc"],
        row["n_dropped_low_rate"],
        row["n_dropped_invalid"],
    ) == (6, 1, 1, 1)
    assert row["hourly_dropped"] == 2
    assert (row["hourly_revision"], row["hourly_reject"]) == (1, None)
    hours = await rad_hourly(clean_db, 4843)
    assert [(h["hour"], h["n_obs"]) for h in hours] == [(0, 2), (1, 2)]
    # The identities of AC-37, in SQL terms.
    assert sum(h["n_obs"] for h in hours) + row["hourly_dropped"] == row["n_used"]
    assert sum(h["live_s"] for h in hours) <= row["live_s"]
    assert hours[0]["dose_b_mean"] == approx(6.2)
    assert hours[0]["dose_e_mean"] == approx(5.2)
    assert hours[1]["dose_b_mean"] == approx(7.2)
    assert hours[0]["live_s"] == hours[1]["live_s"] == 1800.0
    assert hours[0]["bu_count"] == hours[1]["bu_count"] == 14400


async def test_sync_implausible_dose_is_out_of_the_day_summary_ac58(
    mixed: RadHost, clean_db: Factory
) -> None:
    """AC-58: the 0.5 uGy/h observation changes neither min nor mean of the day."""
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    row = await daily_row(clean_db, 4843)
    assert row is not None
    assert row["dose_b_min"] == 6.0
    assert row["n_dropped_invalid"] == 1


# --- AC-41: the reprocess --------------------------------------------------------------------


async def test_reprocess_two_sols_asks_two_lists_and_two_files_without_probes_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """AC-41: 2 lists + 2 files, no `Range` probes; the hours and revisions appear."""
    await load_and_reset_two(clean_db, host_4842_4843)

    code = await sync_two("--reprocess")

    assert code == 0
    paths = paths_of(host_4842_4843)
    assert len(paths) == 4
    assert sorted(paths[:2]) == [CHECKSUMS, TIMING]
    assert paths[2:] == [P4842, P4843]  # ascending sol order
    assert host_4842_4843.calls("probes") == []
    assert all(r.range is None for r in host_4842_4843.calls())
    assert host_4842_4843.max_inflight == 1


async def test_reprocess_writes_the_hours_and_the_revision_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """AC-41: 4843 gets hour 0 and `hourly_dropped` 1; 4842 gets `off_sol`; both revision 1."""
    await load_and_reset_two(clean_db, host_4842_4843)

    assert await sync_two("--reprocess") == 0

    row_4843 = await daily_row(clean_db, 4843)
    row_4842 = await daily_row(clean_db, 4842)
    assert row_4843 is not None and row_4842 is not None
    assert (row_4843["hourly_revision"], row_4843["hourly_reject"], row_4843["hourly_dropped"]) == (
        1,
        None,
        1,
    )
    assert (row_4842["hourly_revision"], row_4842["hourly_reject"]) == (1, "off_sol")
    hours = await rad_hourly(clean_db)
    assert [(h["sol"], h["hour"], h["n_obs"]) for h in hours] == [(4843, 0, 2)]


async def test_reprocess_leaves_the_daily_columns_and_updated_at_alone_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """AC-41: the daily values did not change, so the row (with `updated_at`) is the same."""
    before_4842, before_4843 = await load_and_reset_two(clean_db, host_4842_4843)

    assert await sync_two("--reprocess") == 0

    assert without_hourly(await daily_row(clean_db, 4842)) == without_hourly(before_4842)
    assert without_hourly(await daily_row(clean_db, 4843)) == without_hourly(before_4843)


async def test_reprocess_again_asks_only_the_two_lists_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """AC-41: a repeat has nothing to do: two conditional list requests, no file."""
    await load_and_reset_two(clean_db, host_4842_4843)
    assert await sync_two("--reprocess") == 0
    hours = await rad_hourly(clean_db)
    host_4842_4843.requests.clear()

    code = await sync_two("--reprocess")

    assert code == 0
    assert sorted(paths_of(host_4842_4843)) == [CHECKSUMS, TIMING]
    assert host_4842_4843.calls("files") == []
    assert await rad_hourly(clean_db) == hours


async def test_reprocess_with_a_duplicate_group_downloads_the_primary_file_only_ac41(
    make_host: Any, clean_db: Factory
) -> None:
    """AC-41: files with status `duplicate` are not touched, not even by a probe."""
    host = make_host(F_1649_A, F_1649_B)
    assert await run_rad("sync", "--from-sol", "1649", "--to-sol", "1649") == 0
    statuses = {f["path"]: (f["status"], f["role"]) for f in await rad_files(clean_db)}
    assert statuses[P1649_A] == ("duplicate", "extra")
    await reset_hourly(clean_db)
    host.requests.clear()

    code = await run_rad("sync", "--reprocess", "--from-sol", "1649", "--to-sol", "1649")

    assert code == 0
    assert [r.path for r in host.calls("files")] == [P1649_B]
    assert host.calls("probes") == []
    assert host.file_calls(P1649_A) == []
    row = await daily_row(clean_db, 1649)
    assert row is not None
    assert (row["hourly_revision"], row["hourly_reject"]) == (1, "off_sol")
    after = {f["path"]: (f["status"], f["role"]) for f in await rad_files(clean_db)}
    assert after == statuses


async def test_reprocess_early_format_sol_gets_its_hours_ac41(
    make_host: Any, clean_db: Factory
) -> None:
    host = make_host(F_SOL10)
    assert await run_rad("sync", "--from-sol", "10", "--to-sol", "10") == 0
    hours = await rad_hourly(clean_db, 10)
    await reset_hourly(clean_db)
    assert await rad_hourly(clean_db) == []
    host.requests.clear()

    assert await run_rad("sync", "--reprocess", "--from-sol", "10", "--to-sol", "10") == 0

    assert await rad_hourly(clean_db, 10) == hours
    row = await daily_row(clean_db, 10)
    assert row is not None and row["hourly_revision"] == 1


async def test_reprocess_range_without_sols_in_the_plan_downloads_nothing_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """Edge: `--from-sol` / `--to-sol` that select no sol of the plan."""
    await load_and_reset_two(clean_db, host_4842_4843)

    code = await run_rad("sync", "--reprocess", "--from-sol", "100", "--to-sol", "200")

    assert code == 0
    assert host_4842_4843.calls("files") == []
    assert (await daily_row(clean_db, 4843))["hourly_revision"] == 0  # type: ignore[index]
    assert await rad_hourly(clean_db) == []


async def test_reprocess_touches_only_the_sols_in_the_range_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    await load_and_reset_two(clean_db, host_4842_4843)

    code = await run_rad("sync", "--reprocess", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 0
    assert [r.path for r in host_4842_4843.calls("files")] == [P4843]
    assert (await daily_row(clean_db, 4843))["hourly_revision"] == 1  # type: ignore[index]
    assert (await daily_row(clean_db, 4842))["hourly_revision"] == 0  # type: ignore[index]


async def test_reprocess_on_an_empty_database_has_nothing_to_do_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    code = await run_rad("sync", "--reprocess")

    assert code == 0
    assert host_4842_4843.calls("files") == []
    assert await rad_daily(clean_db) == []


async def test_reprocess_with_an_inverted_range_exits_2_without_requests_ac41(
    host_4842_4843: RadHost,
) -> None:
    assert await run_rad("sync", "--reprocess", "--from-sol", "4843", "--to-sol", "4842") == 2
    assert host_4842_4843.calls() == []


async def test_reprocess_applies_the_dose_filter_to_the_day_and_updates_it_ac58(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    """AC-58 + AC-41: a sol stored before the filter changes on reprocess (values, `updated_at`)."""
    path = synthetic_path(synthetic_name(4843))
    host = synthetic_host(upstream_router, events, {path: mixed_file()})
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    async with clean_db() as session:  # the state of stage 21: the garbage dose was used
        await session.execute(
            text(
                "UPDATE rad_daily SET dose_b_min = 0.5, n_used = 7, n_dropped_invalid = 0, "
                "hourly_revision = 0, hourly_reject = NULL, hourly_dropped = 0, "
                "updated_at = '2026-11-01T00:00:00+00:00'"
            )
        )
        await session.execute(text("DELETE FROM rad_hourly"))
        await session.commit()
    host.requests.clear()

    assert await run_rad("sync", "--reprocess", "--from-sol", "4843", "--to-sol", "4843") == 0

    row = await daily_row(clean_db, 4843)
    assert row is not None
    assert (row["dose_b_min"], row["n_used"], row["n_dropped_invalid"]) == (6.0, 6, 1)
    assert row["updated_at"] > datetime(2026, 11, 1, tzinfo=UTC)
    assert row["hourly_revision"] == 1


# --- AC-42: the monthly timer does not re-download the history -------------------------------


@pytest.mark.parametrize("new_list", [False, True], ids=["list-unchanged", "list-reread"])
@pytest.mark.parametrize("ranged", [False, True], ids=["whole", "range"])
async def test_sync_without_reprocess_downloads_nothing_for_old_sols_ac42(
    host_4842_4843: RadHost, clean_db: Factory, new_list: bool, ranged: bool
) -> None:
    """AC-42: md5 are the same, `hourly_revision` is 0 -> no file is requested."""
    await load_and_reset_two(clean_db, host_4842_4843)
    if new_list:
        host_4842_4843.bump(CHECKSUMS)  # the list is read again; every md5 is still the same
    args = ("--from-sol", "4842", "--to-sol", "4843") if ranged else ()

    code = await run_rad("sync", *args)

    assert code == 0
    assert host_4842_4843.calls("files") == []
    assert host_4842_4843.calls("probes") == []
    assert (await daily_row(clean_db, 4843))["hourly_revision"] == 0  # type: ignore[index]
    assert await rad_hourly(clean_db) == []


async def test_sync_without_reprocess_still_handles_new_and_changed_sols_fully_ac42(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """The timer processes a changed sol with its hours and revision; old sols stay at 0."""
    await load_and_reset_two(clean_db, host_4842_4843)
    host_4842_4843.set_file(P4843, host_4842_4843.files[P4843].replace(b"6.84128", b"7.84128", 1))

    assert await sync_two() == 0

    assert [r.path for r in host_4842_4843.calls("files")] == [P4843]
    assert (await daily_row(clean_db, 4843))["hourly_revision"] == 1  # type: ignore[index]
    assert (await daily_row(clean_db, 4842))["hourly_revision"] == 0  # type: ignore[index]
    assert [(h["sol"], h["hour"]) for h in await rad_hourly(clean_db)] == [(4843, 0)]


# --- AC-43: interruption ---------------------------------------------------------------------


async def interrupted(*argv: str) -> int | None:
    """Run the CLI; Ctrl+C may end it by an exception or by an exit code (returned)."""
    try:
        return await run_rad(*argv, crash_ok=True)
    except KeyboardInterrupt:
        return None


async def test_reprocess_ctrl_c_keeps_the_done_sols_and_the_next_run_goes_on_ac43(
    make_host: Any, clean_db: Factory
) -> None:
    """AC-43: sols 10 and 4842 are done (revision 1), 4843 was in work; the repeat reads it."""
    host = make_host(F_SOL10, F_4842, F_4843)
    assert await run_rad("sync") == 0
    await reset_hourly(clean_db)
    host.requests.clear()
    host.fail[P4843] = ["interrupt"]

    code = await interrupted("sync", "--reprocess")

    assert code != 0
    revisions = {r["sol"]: r["hourly_revision"] for r in await rad_daily(clean_db)}
    assert revisions == {10: 1, 4842: 1, 4843: 0}
    assert sorted({h["sol"] for h in await rad_hourly(clean_db)}) == [10]
    host.fail.clear()
    host.requests.clear()

    assert await run_rad("sync", "--reprocess") == 0

    assert [r.path for r in host.calls("files")] == [P4843]  # 10 and 4842 are not read again
    revisions = {r["sol"]: r["hourly_revision"] for r in await rad_daily(clean_db)}
    assert revisions == {10: 1, 4842: 1, 4843: 1}
    assert sorted({h["sol"] for h in await rad_hourly(clean_db)}) == [10, 4843]


async def test_reprocess_ctrl_c_inside_the_sol_transaction_rolls_the_sol_back_ac43(
    host_4842_4843: RadHost, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-43: Ctrl+C while the hours of 4843 are written -> nothing of 4843 is committed:
    not the hours, not the revision, not the daily value changed in the same transaction."""
    await load_and_reset_two(clean_db, host_4842_4843)
    async with clean_db() as session:  # makes the day of 4843 change in the reprocess
        await session.execute(text("UPDATE rad_daily SET dose_b_mean = 99 WHERE sol = 4843"))
        await session.commit()
    real = rad_repo.replace_hourly

    async def interrupt_on_4843(
        session: Any, sol: int, rows: Any, *args: Any, **kwargs: Any
    ) -> Any:
        if sol == 4843:
            raise KeyboardInterrupt
        return await real(session, sol, rows, *args, **kwargs)

    monkeypatch.setattr(rad_repo, "replace_hourly", interrupt_on_4843)

    code = await interrupted("sync", "--reprocess", "--from-sol", "4842", "--to-sol", "4843")

    assert code != 0
    row = await daily_row(clean_db, 4843)
    assert row is not None
    assert (row["hourly_revision"], row["dose_b_mean"]) == (0, 99)
    assert await rad_hourly(clean_db, 4843) == []
    done = await daily_row(clean_db, 4842)
    assert done is not None and done["hourly_revision"] == 1
    monkeypatch.setattr(rad_repo, "replace_hourly", real)
    host_4842_4843.requests.clear()

    assert await sync_two("--reprocess") == 0

    assert [r.path for r in host_4842_4843.calls("files")] == [P4843]
    row = await daily_row(clean_db, 4843)
    assert row is not None and row["hourly_revision"] == 1
    assert row["dose_b_mean"] != 99


async def test_reprocess_a_failing_file_does_not_stop_the_other_sols_and_a_sync_retries_it_ac43(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    """A 404 on one file fails that sol only (exit 1); the plain `rad sync` retries the failed
    file (stage 21 rule) and then the sol gets its hours."""
    await load_and_reset_two(clean_db, host_4842_4843)
    host_4842_4843.fail[P4842] = ["404"]

    code = await sync_two("--reprocess")

    assert code == 1
    assert (await daily_row(clean_db, 4843))["hourly_revision"] == 1  # type: ignore[index]
    assert (await daily_row(clean_db, 4842))["hourly_revision"] == 0  # type: ignore[index]
    host_4842_4843.fail.clear()
    host_4842_4843.requests.clear()

    assert await sync_two() == 0

    assert [r.path for r in host_4842_4843.calls("files")] == [P4842]
    assert (await daily_row(clean_db, 4842))["hourly_revision"] == 1  # type: ignore[index]


# --- the repository --------------------------------------------------------------------------


async def test_replace_hourly_replaces_the_rows_of_one_sol_only_ac44(clean_db: Factory) -> None:
    """`rad_repo.replace_hourly(session, sol, rows)`: delete + insert of one sol, others stay."""
    await insert_daily(clean_db, 4842)
    await insert_daily(clean_db, 4843)

    def hour(sol: int, h: int, n: int = 2) -> dict[str, Any]:
        # Row mappings with the columns of `rad_hourly` (the contract fixes only the call shape).
        return {
            "sol": sol,
            "hour": h,
            "n_obs": n,
            "live_s": 900.0 * n,
            "dose_b_mean": 6.5,
            "dose_e_mean": 6.0,
            "bu_count": 7000 * n,
        }

    async with clean_db() as session:
        await rad_repo.replace_hourly(session, 4842, [hour(4842, 3, 1)])
        await rad_repo.replace_hourly(session, 4843, [hour(4843, 0), hour(4843, 1), hour(4843, 23)])
        await session.commit()
    assert [(h["sol"], h["hour"]) for h in await rad_hourly(clean_db)] == [
        (4842, 3),
        (4843, 0),
        (4843, 1),
        (4843, 23),
    ]

    async with clean_db() as session:
        await rad_repo.replace_hourly(session, 4843, [hour(4843, 5, 3)])
        await session.commit()

    rows = await rad_hourly(clean_db)
    assert [(h["sol"], h["hour"]) for h in rows] == [(4842, 3), (4843, 5)]
    assert rows[1]["n_obs"] == 3 and rows[1]["live_s"] == 2700.0 and rows[1]["bu_count"] == 21000

    async with clean_db() as session:
        await rad_repo.replace_hourly(session, 4843, [])
        await session.commit()

    assert [(h["sol"], h["hour"]) for h in await rad_hourly(clean_db)] == [(4842, 3)]


# --- the whole way: loader -> database -> API ------------------------------------------------


async def test_api_after_a_sync_shows_the_hours_of_the_fixtures_ac38(
    host_4842_4843: RadHost, client: httpx.AsyncClient
) -> None:
    """AC-38 (end to end): 4843 has 24 hours, hour 0 with two observations; 4842 has none."""
    assert await sync_two() == 0

    good = (await client.get(f"{V2}/rovers/curiosity/radiation/4843")).json()
    rejected = (await client.get(f"{V2}/rovers/curiosity/radiation/4842")).json()

    assert len(good["hours"]) == 24
    assert good["hours"][0]["observations"] == 2
    assert good["hours"][0]["dose_b_ugy_h"] == 6.81
    assert good["hours"][0]["dose_e_ugy_h"] == 6.56
    assert good["hours"][0]["count_rate_b_per_s"] == 6.958
    assert good["hours"][0]["coverage"] == 0.5
    assert all(h["observations"] == 0 for h in good["hours"][1:])
    assert rejected["hours"] is None
    assert rejected["sol"] == 4842


async def test_reprocess_success_does_not_move_succeeded_at_ac41(
    host_4842_4843: RadHost, clean_db: Factory
) -> None:
    from tests._rad import rad_dataset

    await load_and_reset_two(clean_db, host_4842_4843)
    before = (await rad_dataset(clean_db))["succeeded_at"]  # type: ignore[index]

    assert await sync_two("--reprocess") == 0

    assert (await rad_dataset(clean_db))["succeeded_at"] == before  # type: ignore[index]


async def test_failure_in_the_hours_of_one_sol_fails_that_sol_only_ac44(
    host_4842_4843: RadHost, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception while combining one sol: that sol is `failed`, the other one is loaded."""
    from app.services import rad_sync

    real = rad_sync.combine_hours

    def broken(used: Any, file_sol: int, *args: Any, **kwargs: Any) -> Any:
        if file_sol == 4842:
            raise OverflowError("date value out of range")
        return real(used, file_sol, *args, **kwargs)

    monkeypatch.setattr(rad_sync, "combine_hours", broken)

    assert await sync_two() == 1

    assert [r["sol"] for r in await rad_daily(clean_db)] == [4843]
    files = {f["path"]: f["status"] for f in await rad_files(clean_db)}
    assert files[P4842] == "failed" and files[P4843] == "loaded"
