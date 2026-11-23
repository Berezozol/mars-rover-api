"""`weather parse` end to end on synthetic raw trees (stage 17, AC-13 ... AC-21).

The CLI is run in-process (`tests/_weather.run_weather`) against the test database with
`WEATHER_RAW_DIR` in `tmp_path`, one worker process. Raw files get an old mtime (the parser
skips files younger than 60 s); `mtime` changes are made with `os.utime`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests._weather import (
    LOCK_KEY,
    OLD_MTIME_NS,
    Factory,
    ats_csv,
    build_meda_sol,
    count,
    daily_row,
    daily_rows,
    dataset_row,
    fetch,
    file_rows,
    full_meda_sol,
    hourly_air,
    hourly_rows,
    meda_filename,
    ps_csv,
    run_weather,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

PERSEVERANCE = ("--rover", "perseverance")
DAY_NS = 86_400 * 1_000_000_000


@pytest.fixture
def raw(weather_env: Callable[..., Path]) -> Path:
    return weather_env()


def air_of(sol: int) -> Callable[[int], float]:
    """Hour h of sol s: 200 + s + h/100 K, a different series for every sol."""
    return lambda h: 200.0 + sol + h / 100


def make_sols(raw: Path, sols: tuple[int, ...] = (1, 2, 3)) -> None:
    for sol in sols:
        full_meda_sol(raw, sol, air=air_of(sol))


async def parse(*argv: str) -> int:
    return await run_weather("parse", *PERSEVERANCE, *argv)


def touch(path: Path, delta_ns: int = DAY_NS) -> None:
    os.utime(path, ns=(OLD_MTIME_NS + delta_ns, OLD_MTIME_NS + delta_ns))


def sol_files(raw: Path, sol: int) -> list[Path]:
    return sorted(raw.glob(f"meda/*/*/sol_{sol:04d}/*.CSV"))


# --- AC-13 ---------------------------------------------------------------------------------------


async def test_parse_fills_the_tables_for_three_sols_ac13(raw: Path, clean_db: Factory) -> None:
    make_sols(raw)

    code = await parse()

    assert code == 0
    assert [r["sol"] for r in await daily_rows(clean_db)] == [1, 2, 3]
    assert await count(clean_db, "weather_hourly") == 72
    files = await file_rows(clean_db)
    assert len(files) == 9  # ATS, PS, RHS per sol
    for row in files:
        path = next(
            p for p in sol_files(raw, row["sol"]) if p.name == row["path"].rsplit("/", 1)[1]
        )
        assert row["status"] == "parsed"
        assert row["version"] == "P01"
        assert row["size_bytes"] == path.stat().st_size
        assert row["mtime_ns"] == path.stat().st_mtime_ns
        assert row["product"] in {"CAL_ATS", "DER_PS", "DER_RHS"}
        assert row["path"].startswith(("data_calibrated_env/", "data_derived_env/"))
        assert row["parsed_at"] is not None
    dataset = await dataset_row(clean_db)
    assert dataset is not None
    assert dataset["source_max_sol"] == 3
    assert dataset["updated_at"] is not None


async def test_parse_of_a_curiosity_tree_uses_the_rems_files_ac13(
    raw: Path, clean_db: Factory
) -> None:
    from tests._weather import full_rems_sol

    full_rems_sol(raw, 9, version="PA")
    full_rems_sol(raw, 30)

    code = await run_weather("parse", "--rover", "curiosity")

    assert code == 0
    rows = await daily_rows(clean_db, "curiosity")
    assert [r["sol"] for r in rows] == [9, 30]
    assert rows[0]["source_version"] == {"RMD": "PA"}
    assert rows[1]["source_version"] == {"RMD": "P1"}
    assert await count(clean_db, "weather_hourly") == 48
    assert await daily_rows(clean_db, "perseverance") == []
    assert {r["product"] for r in await file_rows(clean_db, "curiosity")} == {"RMD"}


async def test_parse_with_two_worker_processes_gives_the_same_rows_ac13(
    raw: Path, clean_db: Factory, weather_env: Callable[..., Path]
) -> None:
    make_sols(raw, (1, 2, 3, 4))
    assert await parse() == 0
    one = [{k: v for k, v in r.items() if k != "updated_at"} for r in await daily_rows(clean_db)]
    await clean_db_wipe(clean_db)

    weather_env(WEATHER_PARSE_WORKERS="2")
    assert await parse() == 0
    two = [{k: v for k, v in r.items() if k != "updated_at"} for r in await daily_rows(clean_db)]

    assert len(one) == 4
    assert two == one


async def clean_db_wipe(factory: Factory) -> None:
    from tests._weather import wipe_weather

    await wipe_weather(factory)


async def test_parse_stores_what_the_files_say_in_si_units_ac13(
    raw: Path, clean_db: Factory
) -> None:
    full_meda_sol(raw, 1, air=air_of(1), pressure=740.5, humidity=5.5)

    assert await parse() == 0

    row = await daily_row(clean_db, 1)
    assert row["air_min_k"] == pytest.approx(201.0)
    assert row["air_max_k"] == pytest.approx(201.23)
    assert row["air_mean_k"] == pytest.approx(201.115)
    assert row["pressure_min_pa"] == pytest.approx(740.5)
    assert row["pressure_mean_pa"] == pytest.approx(740.5)
    assert row["humidity_max_pct"] == pytest.approx(5.5)
    assert row["n_air"] == 24 * 60
    assert row["n_pressure"] == 24 * 60
    assert row["parser_revision"] >= 1
    assert row["flags"] == []
    assert row["source_version"] == {"CAL_ATS": "P01", "DER_PS": "P01", "DER_RHS": "P01"}
    assert row["earth_date"] is not None


async def test_parse_of_a_missing_raw_dir_is_an_input_error(
    weather_env: Callable[..., Path], clean_db: Factory, tmp_path: Path
) -> None:
    weather_env(WEATHER_RAW_DIR=str(tmp_path / "nope"))

    assert await parse() == 2
    assert await count(clean_db, "weather_daily") == 0


async def test_parse_of_an_empty_raw_dir_does_nothing_and_succeeds(
    raw: Path, clean_db: Factory
) -> None:
    assert await parse() == 0
    assert await count(clean_db, "weather_daily") == 0
    assert await count(clean_db, "weather_files") == 0


@pytest.mark.parametrize("rover", ["opportunity", "ingenuity", "pluto"])
async def test_parse_of_a_rover_without_weather_is_an_input_error(raw: Path, rover: str) -> None:
    assert await run_weather("parse", "--rover", rover) == 2


async def test_parse_skips_files_younger_than_a_minute_and_part_files(
    raw: Path, clean_db: Factory
) -> None:
    young = full_meda_sol(raw, 1, air=air_of(1))
    full_meda_sol(raw, 2, air=air_of(2))
    for path in young.values():
        os.utime(path)  # modified just now: the downloader may still be writing
    folder = sol_files(raw, 2)[0].parent
    (folder / (meda_filename(2, "CAL_WS", "P01") + ".part")).write_bytes(b"partial")

    assert await parse() == 0

    assert [r["sol"] for r in await daily_rows(clean_db)] == [2]
    assert all(".part" not in r["path"] for r in await file_rows(clean_db))


# --- AC-12 (versions, through the database) ------------------------------------------------------


async def test_parse_takes_the_newest_version_of_a_product_ac12(
    raw: Path, clean_db: Factory
) -> None:
    full_meda_sol(raw, 2, air=lambda h: 210.0)
    build_meda_sol(
        raw,
        2,
        {"CAL_ATS": ats_csv(2, hourly_air(2, range(24), value=lambda h: 230.0))},
        versions={"CAL_ATS": "P02"},
    )

    assert await parse() == 0

    row = await daily_row(clean_db, 2)
    assert row["source_version"]["CAL_ATS"] == "P02"
    assert row["air_mean_k"] == pytest.approx(230.0)
    files = {(r["product"], r["version"]): r for r in await file_rows(clean_db)}
    assert files[("CAL_ATS", "P02")]["status"] == "parsed"
    if ("CAL_ATS", "P01") in files:
        assert files[("CAL_ATS", "P01")]["status"] == "superseded"


async def test_parse_ignores_a_corrupt_older_version_when_a_newer_one_exists_ac12(
    raw: Path, clean_db: Factory
) -> None:
    full_meda_sol(raw, 2, air=lambda h: 230.0, versions={"CAL_ATS": "P02"})
    build_meda_sol(raw, 2, {"CAL_ATS": b"not a csv"}, versions={"CAL_ATS": "P01"})

    assert await parse() == 0

    assert (await daily_row(clean_db, 2))["source_version"]["CAL_ATS"] == "P02"


# --- AC-14 ---------------------------------------------------------------------------------------


async def test_second_parse_without_changes_touches_nothing_ac14(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)
    assert await parse() == 0
    daily = await daily_rows(clean_db)
    files = await file_rows(clean_db)
    dataset = await dataset_row(clean_db)

    assert await parse() == 0

    assert await daily_rows(clean_db) == daily  # including updated_at
    assert await file_rows(clean_db) == files  # including parsed_at: 0 files were parsed
    after = await dataset_row(clean_db)
    assert dataset is not None and after is not None
    # run bookkeeping (parsed_at, counters) may move; the fingerprint of the data must not
    assert after["updated_at"] == dataset["updated_at"]
    assert after["changed_at"] == dataset["changed_at"]
    assert after["source_max_sol"] == dataset["source_max_sol"]


# --- AC-15 ---------------------------------------------------------------------------------------


async def test_new_mtime_alone_recomputes_the_sol_but_not_updated_at_ac15(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)
    assert await parse() == 0
    before = {r["sol"]: r for r in await daily_rows(clean_db)}
    files_before = {r["path"]: r for r in await file_rows(clean_db)}
    target = sol_files(raw, 2)[0]
    touch(target)

    assert await parse() == 0

    after = {r["sol"]: r for r in await daily_rows(clean_db)}
    assert after == before  # same values: updated_at of sol 2 is unchanged as well
    files_after = {r["path"]: r for r in await file_rows(clean_db)}
    changed = {p for p in files_after if files_after[p] != files_before[p]}
    assert {files_after[p]["sol"] for p in changed} == {2}  # only sol 2 was parsed again
    path_key = next(p for p in files_after if p.endswith(target.name))
    assert files_after[path_key]["mtime_ns"] == target.stat().st_mtime_ns


async def test_changed_content_updates_the_row_and_its_updated_at_ac15(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)
    assert await parse() == 0
    before = {r["sol"]: r for r in await daily_rows(clean_db)}
    dataset_before = await dataset_row(clean_db)
    full_meda_sol(raw, 2, air=lambda h: 250.0)  # new content, same names
    for path in sol_files(raw, 2):
        touch(path)

    assert await parse() == 0

    after = {r["sol"]: r for r in await daily_rows(clean_db)}
    assert after[2]["air_mean_k"] == pytest.approx(250.0)
    assert after[2]["updated_at"] > before[2]["updated_at"]
    assert after[1] == before[1]
    assert after[3] == before[3]
    dataset_after = await dataset_row(clean_db)
    assert dataset_after is not None and dataset_before is not None
    assert dataset_after["updated_at"] > dataset_before["updated_at"]


# --- AC-16 ---------------------------------------------------------------------------------------


async def test_a_newer_version_file_recomputes_the_sol_ac16(raw: Path, clean_db: Factory) -> None:
    make_sols(raw)
    assert await parse() == 0
    before = {r["sol"]: r for r in await daily_rows(clean_db)}
    build_meda_sol(
        raw,
        2,
        {"CAL_ATS": ats_csv(2, hourly_air(2, range(24), value=lambda h: 240.0))},
        versions={"CAL_ATS": "P02"},
    )

    assert await parse() == 0

    after = {r["sol"]: r for r in await daily_rows(clean_db)}
    assert after[2]["source_version"]["CAL_ATS"] == "P02"
    assert after[2]["air_mean_k"] == pytest.approx(240.0)
    assert after[1] == before[1]
    assert after[3] == before[3]


async def test_changing_the_parser_revision_recomputes_every_sol_ac16(
    raw: Path, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import weather_sync

    make_sols(raw)
    assert await parse() == 0
    files_before = {r["path"]: r["parsed_at"] for r in await file_rows(clean_db)}
    revision = weather_sync.WEATHER_PARSER_REVISION
    monkeypatch.setattr(weather_sync, "WEATHER_PARSER_REVISION", revision + 1)

    assert await parse() == 0

    assert {r["parser_revision"] for r in await daily_rows(clean_db)} == {revision + 1}
    files_after = {r["path"]: r["parsed_at"] for r in await file_rows(clean_db)}
    assert all(files_after[p] > files_before[p] for p in files_before)


async def test_force_recomputes_every_sol(raw: Path, clean_db: Factory) -> None:
    make_sols(raw)
    assert await parse() == 0
    files_before = {r["path"]: r["parsed_at"] for r in await file_rows(clean_db)}

    assert await parse("--force") == 0

    files_after = {r["path"]: r["parsed_at"] for r in await file_rows(clean_db)}
    assert all(files_after[p] > files_before[p] for p in files_before)


# --- AC-17 ---------------------------------------------------------------------------------------


async def test_sol_range_limits_the_run_to_that_sol_ac17(raw: Path, clean_db: Factory) -> None:
    make_sols(raw)

    assert await parse("--from-sol", "2", "--to-sol", "2") == 0

    assert [r["sol"] for r in await daily_rows(clean_db)] == [2]
    assert {r["sol"] for r in await file_rows(clean_db)} == {2}


async def test_sol_range_leaves_the_other_changed_sols_alone_ac17(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)
    assert await parse() == 0
    before = {r["sol"]: r for r in await daily_rows(clean_db)}
    for sol in (1, 2):
        full_meda_sol(raw, sol, air=lambda h: 260.0)
        for path in sol_files(raw, sol):
            touch(path)

    assert await parse("--from-sol", "2", "--to-sol", "2") == 0

    after = {r["sol"]: r for r in await daily_rows(clean_db)}
    assert after[2]["air_mean_k"] == pytest.approx(260.0)
    assert after[1] == before[1]


async def test_reversed_sol_range_is_an_input_error_and_changes_nothing_ac17(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)

    assert await parse("--from-sol", "5", "--to-sol", "2") == 2

    assert await count(clean_db, "weather_daily") == 0
    assert await count(clean_db, "weather_files") == 0


# --- AC-18 ---------------------------------------------------------------------------------------


def break_file(path: Path) -> None:
    path.write_bytes(b"SCLK,LMST,LTST,SOMETHING\r\n1,00002M00:00:00.000,x,5\r\n")
    touch(path, 2 * DAY_NS)


async def test_broken_file_fails_its_sol_and_the_run_goes_on_ac18(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)
    break_file(sol_files(raw, 2)[1])  # DER_PS lacks the PRESSURE column

    code = await parse()

    assert code == 1
    assert [r["sol"] for r in await daily_rows(clean_db)] == [1, 3]
    failed = [r for r in await file_rows(clean_db) if r["status"] == "failed"]
    assert len(failed) >= 1 and {r["sol"] for r in failed} == {2}
    assert all(r["error"] for r in failed)


async def test_broken_file_keeps_the_old_rows_of_the_sol_and_a_fix_recovers_it_ac18(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)
    assert await parse() == 0
    before = {r["sol"]: r for r in await daily_rows(clean_db)}
    hours_before = await hourly_rows(clean_db, 2)
    broken = sol_files(raw, 2)[1]
    good_bytes = broken.read_bytes()
    break_file(broken)

    assert await parse() == 1

    after = {r["sol"]: r for r in await daily_rows(clean_db)}
    assert after == before  # sol 2 is as it was
    assert await hourly_rows(clean_db, 2) == hours_before
    failed = [r for r in await file_rows(clean_db) if r["status"] == "failed"]
    assert {r["sol"] for r in failed} == {2}
    assert all(r["error"] for r in failed)

    broken.write_bytes(good_bytes.replace(b"740.00", b"700.00"))
    touch(broken, 3 * DAY_NS)

    assert await parse() == 0

    fixed = await daily_row(clean_db, 2)
    assert fixed["pressure_mean_pa"] == pytest.approx(700.0)
    assert {r["status"] for r in await file_rows(clean_db) if r["sol"] == 2} == {"parsed"}
    assert all(not r["error"] for r in await file_rows(clean_db) if r["sol"] == 2)


async def test_zero_byte_file_does_not_stop_the_run(raw: Path, clean_db: Factory) -> None:
    make_sols(raw)
    sol_files(raw, 2)[0].write_bytes(b"")
    touch(sol_files(raw, 2)[0])

    code = await parse()

    assert code in (0, 1)
    assert 1 in [r["sol"] for r in await daily_rows(clean_db)]
    assert 3 in [r["sol"] for r in await daily_rows(clean_db)]


# --- AC-10 (through the database) ----------------------------------------------------------------


async def test_sol_with_only_gap_values_gets_no_row_ac10(raw: Path, clean_db: Factory) -> None:
    build_meda_sol(raw, 5, {"DER_PS": ps_csv(5, [(0.0, "999999999"), (1.0, None), (2.0, 50.0)])})
    full_meda_sol(raw, 6)

    assert await parse() == 0

    assert [r["sol"] for r in await daily_rows(clean_db)] == [6]
    assert await hourly_rows(clean_db, 5) == []


async def test_sol_with_only_pressure_still_gets_a_row_ac10(raw: Path, clean_db: Factory) -> None:
    build_meda_sol(raw, 5, {"DER_PS": ps_csv(5, [(0.0, 740.0), (3600.0, 742.0)])})

    assert await parse() == 0

    row = await daily_row(clean_db, 5)
    assert row["pressure_mean_pa"] == pytest.approx(741.0)
    assert row["air_min_k"] is None and row["air_max_k"] is None and row["air_mean_k"] is None
    assert row["hours_covered"] == 0
    assert row["coverage"] == 0


async def test_parameter_without_values_is_null_and_has_no_flag_ac10(
    raw: Path, clean_db: Factory
) -> None:
    """Pressure of sol 10 is missing (no `DER_PS` file): null, no flag for it."""
    build_meda_sol(raw, 10, {"CAL_ATS": ats_csv(10, hourly_air(10, range(24)))})

    assert await parse() == 0

    row = await daily_row(clean_db, 10)
    assert (row["pressure_min_pa"], row["pressure_max_pa"], row["pressure_mean_pa"]) == (
        None,
        None,
        None,
    )
    assert row["n_pressure"] == 0
    assert all(f["parameter"] != "pressure_pa" for f in row["flags"])
    assert row["flags"] == []


# --- AC-19 ---------------------------------------------------------------------------------------


def many_sols(raw: Path, n: int = 20) -> None:
    for sol in range(1, n + 1):
        build_meda_sol(raw, sol, {"CAL_ATS": ats_csv(sol, hourly_air(sol, range(24), per_hour=1))})


def remove_sol(raw: Path, sol: int) -> None:
    for folder in raw.glob(f"meda/*/*/sol_{sol:04d}"):
        shutil.rmtree(folder)


async def test_missing_files_keep_the_rows_and_status_reports_them_ac19(
    raw: Path, clean_db: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    many_sols(raw)
    assert await parse() == 0
    remove_sol(raw, 3)
    capsys.readouterr()

    assert await parse() == 0
    assert 3 in [r["sol"] for r in await daily_rows(clean_db)]
    assert await run_weather("status", "--rover", "perseverance") == 0

    out = capsys.readouterr()
    shown = (out.out + out.err).lower()
    assert "1" in shown
    assert any(word in shown for word in ("missing", "пропал", "gone", "absent", "vanish"))


async def test_prune_removes_the_sols_whose_files_are_gone_ac19(
    raw: Path, clean_db: Factory
) -> None:
    many_sols(raw)
    assert await parse() == 0
    remove_sol(raw, 3)

    assert await parse("--prune") == 0

    sols = [r["sol"] for r in await daily_rows(clean_db)]
    assert 3 not in sols and len(sols) == 19
    assert await hourly_rows(clean_db, 3) == []
    assert [r for r in await file_rows(clean_db) if r["sol"] == 3] == []
    assert await count(clean_db, "weather_hourly") == 19 * 24


async def test_prune_refuses_when_too_many_sols_are_gone_ac19(raw: Path, clean_db: Factory) -> None:
    many_sols(raw)
    assert await parse() == 0
    for sol in (3, 4, 5):  # 15 % of 20 sols; WEATHER_MIN_KEEP_RATIO is 0.9
        remove_sol(raw, sol)

    assert await parse("--prune") == 1

    assert len(await daily_rows(clean_db)) == 20


async def test_prune_with_nothing_missing_changes_nothing_ac19(
    raw: Path, clean_db: Factory
) -> None:
    many_sols(raw)
    assert await parse() == 0
    before = await daily_rows(clean_db)

    assert await parse("--prune") == 0

    assert await daily_rows(clean_db) == before


# --- AC-20 ---------------------------------------------------------------------------------------


async def hold_lock(url: str):
    engine = create_async_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    conn = await engine.connect()
    got = await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
    assert got is True
    return engine, conn


async def test_parse_exits_with_2_when_another_run_holds_the_lock_ac20(
    raw: Path, clean_db: Factory, settings
) -> None:
    make_sols(raw)
    engine, conn = await hold_lock(settings.database_url)
    try:
        code = await asyncio.wait_for(parse(), timeout=30)
    finally:
        await conn.scalar(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        await conn.close()
        await engine.dispose()

    assert code == 2
    assert await count(clean_db, "weather_daily") == 0

    assert await parse() == 0  # the lock is released: the next run works


async def test_the_lock_is_released_after_a_run_ac20(
    raw: Path, clean_db: Factory, settings
) -> None:
    make_sols(raw, (1,))
    assert await parse() == 0
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            got = await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
            await conn.scalar(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    finally:
        await engine.dispose()

    assert got is True


# --- AC-21 ---------------------------------------------------------------------------------------

TRIGGER_SQL = [
    "CREATE OR REPLACE FUNCTION weather_test_fail() RETURNS trigger AS $$ BEGIN "
    "IF NEW.sol = 2 THEN RAISE EXCEPTION 'simulated crash in the middle of sol 2'; END IF; "
    "RETURN NEW; END $$ LANGUAGE plpgsql",
    "CREATE TRIGGER weather_test_fail BEFORE INSERT ON weather_hourly "
    "FOR EACH ROW EXECUTE FUNCTION weather_test_fail()",
]


async def run_sql(factory: Factory, *statements: str) -> None:
    async with factory() as session:
        for statement in statements:
            await session.execute(text(statement))
        await session.commit()


async def test_a_failure_in_the_middle_of_a_sol_leaves_its_rows_as_they_were_ac21(
    raw: Path, clean_db: Factory
) -> None:
    """A trigger fails the insert of the hourly rows of sol 2 after its daily row was written."""
    make_sols(raw)
    assert await parse() == 0
    before = {r["sol"]: r for r in await daily_rows(clean_db)}
    hours_before = await hourly_rows(clean_db, 2)
    full_meda_sol(raw, 2, air=lambda h: 255.0)
    for path in sol_files(raw, 2):
        touch(path)
    await run_sql(clean_db, *TRIGGER_SQL)
    try:
        await run_weather("parse", *PERSEVERANCE, crash_ok=True)
        after = {r["sol"]: r for r in await daily_rows(clean_db)}
        assert after[2] == before[2]
        assert await hourly_rows(clean_db, 2) == hours_before
        assert all(r["air_k"] is not None for r in await hourly_rows(clean_db, 2))
    finally:
        await run_sql(
            clean_db,
            "DROP TRIGGER IF EXISTS weather_test_fail ON weather_hourly",
            "DROP FUNCTION IF EXISTS weather_test_fail()",
        )

    assert await parse() == 0  # the retry writes the sol

    assert (await daily_row(clean_db, 2))["air_mean_k"] == pytest.approx(255.0)
    assert len(await hourly_rows(clean_db, 2)) == 24


async def test_a_failure_on_a_new_sol_leaves_no_half_written_sol_ac21(
    raw: Path, clean_db: Factory
) -> None:
    make_sols(raw)
    await run_sql(clean_db, *TRIGGER_SQL)
    try:
        await run_weather("parse", *PERSEVERANCE, crash_ok=True)
        daily_sols = {r["sol"] for r in await daily_rows(clean_db)}
        hourly_sols = {
            r["sol"] for r in await fetch(clean_db, "SELECT DISTINCT sol FROM weather_hourly")
        }
        assert 2 not in daily_sols
        assert hourly_sols == daily_sols  # no daily row without hours and vice versa
    finally:
        await run_sql(
            clean_db,
            "DROP TRIGGER IF EXISTS weather_test_fail ON weather_hourly",
            "DROP FUNCTION IF EXISTS weather_test_fail()",
        )

    assert await parse() == 0
    assert [r["sol"] for r in await daily_rows(clean_db)] == [1, 2, 3]
