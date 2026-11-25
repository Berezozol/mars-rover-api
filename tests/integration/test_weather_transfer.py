"""`weather export` / `weather import` (stage 17, AC-22, AC-23, import side of AC-20).

The export is gzip JSON Lines: a `meta` line (`format` `mars-weather-export`, `version` 2,
`schema_revision` `0020`, per-rover counts), then `dataset`, `daily`, `hourly` lines. Data
for the tests is produced by `weather parse` on small synthetic raw trees. The invalid files
are made from a real export by editing its lines.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests._weather import (
    LOCK_KEY,
    Factory,
    count,
    daily_rows,
    fetch,
    full_meda_sol,
    full_rems_sol,
    insert_daily,
    insert_dataset,
    run_weather,
    wipe_weather,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

DATASET_IGNORED = {"imported_at", "import_meta"}


@pytest.fixture
def raw(weather_env: Callable[..., Path]) -> Path:
    return weather_env()


async def seed_by_parse(raw: Path, p_sols: tuple[int, ...] = (1, 2, 3), c_sols=(9, 30)) -> None:
    for sol in p_sols:
        full_meda_sol(raw, sol, air=lambda h, s=sol: 200.0 + s + h / 100, with_ws=sol == 2)
    for sol in c_sols:
        full_rems_sol(raw, sol)
    if p_sols:
        assert await run_weather("parse", "--rover", "perseverance") == 0
    if c_sols:
        assert await run_weather("parse", "--rover", "curiosity") == 0


async def snapshot(factory: Factory) -> dict[str, list[dict[str, Any]]]:
    daily = await fetch(factory, "SELECT * FROM weather_daily ORDER BY rover_id, sol")
    hourly = await fetch(factory, "SELECT * FROM weather_hourly ORDER BY rover_id, sol, hour")
    dataset = await fetch(factory, "SELECT * FROM weather_dataset ORDER BY rover_id")
    return {
        "daily": daily,
        "hourly": hourly,
        "dataset": [{k: v for k, v in r.items() if k not in DATASET_IGNORED} for r in dataset],
    }


def read_export(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_export(path: Path, lines: list[dict[str, Any]], *, raw_json: bool = False) -> Path:
    body = "\n".join(json.dumps(line, allow_nan=True) for line in lines) + "\n"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(body)
    return path


async def export(path: Path, *extra: str) -> int:
    return await run_weather("export", "--output", str(path), *extra)


async def import_(path: Path, *extra: str) -> int:
    return await run_weather("import", "--input", str(path), *extra)


# --- AC-22 ---------------------------------------------------------------------------------------


async def test_export_writes_meta_dataset_daily_and_hourly_lines_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw)
    target = tmp_path / "weather.jsonl.gz"

    assert await export(target) == 0

    lines = read_export(target)
    meta = lines[0]
    assert meta["t"] == "meta"
    assert meta["format"] == "mars-weather-export"
    assert meta["version"] == 2  # stage 22: hourly min/max and gust
    assert meta["schema_revision"] == "0020"
    assert meta["counts"] == {
        "perseverance": {"daily": 3, "hourly": 72},
        "curiosity": {"daily": 2, "hourly": 48},
    }
    assert datetime.fromisoformat(meta["exported_at"]).utcoffset().total_seconds() == 0
    kinds = [line["t"] for line in lines]
    assert kinds.count("meta") == 1
    assert kinds.count("dataset") == 2
    assert kinds.count("daily") == 5
    assert kinds.count("hourly") == 120
    assert kinds[0] == "meta"
    for rover in ("perseverance", "curiosity"):
        own = [line["t"] for line in lines[1:] if line.get("rover_id") == rover]
        assert own == sorted(own, key=["dataset", "daily", "hourly"].index), rover


async def test_export_rows_carry_all_columns_with_iso_utc_times_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw, (1,), ())
    target = tmp_path / "weather.jsonl.gz"
    assert await export(target) == 0

    daily = next(line for line in read_export(target) if line["t"] == "daily")
    hourly = next(line for line in read_export(target) if line["t"] == "hourly")

    stored = (await fetch(clean_db, "SELECT * FROM weather_daily"))[0]
    for column in stored:
        assert column in daily, column
    assert daily["rover_id"] == "perseverance" and daily["sol"] == 1
    assert daily["air_mean_k"] == pytest.approx(stored["air_mean_k"])
    assert datetime.fromisoformat(daily["updated_at"]).utcoffset().total_seconds() == 0
    assert daily["flags"] == [] and daily["source_version"]["CAL_ATS"] == "P01"
    assert {
        "rover_id",
        "sol",
        "hour",
        "coverage",
        "air_k",
        "air_min_k",
        "air_max_k",
    } <= hourly.keys()


async def test_export_of_one_rover_has_only_its_rows_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw)
    target = tmp_path / "weather.jsonl.gz"

    assert await export(target, "--rover", "curiosity") == 0

    lines = read_export(target)
    assert lines[0]["counts"] == {"curiosity": {"daily": 2, "hourly": 48}}
    assert {line.get("rover_id") for line in lines[1:]} == {"curiosity"}


async def test_import_into_an_empty_database_restores_the_three_tables_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw)
    before = await snapshot(clean_db)
    target = tmp_path / "weather.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)

    assert await import_(target) == 0

    after = await snapshot(clean_db)
    assert after["daily"] == before["daily"]
    assert after["hourly"] == before["hourly"]
    assert after["dataset"] == before["dataset"]
    assert await count(clean_db, "weather_files") == 0  # raw-file state is not transferred
    imported = await fetch(clean_db, "SELECT imported_at FROM weather_dataset")
    assert all(r["imported_at"] is not None for r in imported)


async def test_second_import_changes_nothing_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw)
    target = tmp_path / "weather.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)
    assert await import_(target) == 0
    first = await snapshot(clean_db)

    assert await import_(target) == 0

    assert await snapshot(clean_db) == first


async def test_import_replaces_the_data_of_the_rovers_in_the_file_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    """Import over a non-empty base: the rover's rows become exactly those of the file."""
    await seed_by_parse(raw, tuple(range(1, 11)), ())  # 10 sols: adding one keeps 10/11 >= 0.9
    target = tmp_path / "weather.jsonl.gz"
    assert await export(target) == 0
    await insert_daily(clean_db, 99)  # a sol that is not in the file
    await run_sql(clean_db, "UPDATE weather_daily SET air_mean_k = 111 WHERE sol = 3")

    assert await import_(target) == 0

    rows = await daily_rows(clean_db)
    assert [r["sol"] for r in rows] == list(range(1, 11))
    assert rows[2]["air_mean_k"] == pytest.approx(203.115)
    assert await count(clean_db, "weather_hourly") == 10 * 24


async def test_import_of_one_rover_leaves_the_other_untouched_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw, tuple(range(1, 11)))
    target = tmp_path / "weather.jsonl.gz"
    assert await export(target, "--rover", "perseverance") == 0
    await insert_daily(clean_db, 99, "perseverance")
    await run_sql(
        clean_db, "UPDATE weather_daily SET air_mean_k = 111 WHERE rover_id = 'curiosity'"
    )
    curiosity_before = await daily_rows(clean_db, "curiosity")

    assert await import_(target) == 0

    assert [r["sol"] for r in await daily_rows(clean_db, "perseverance")] == list(range(1, 11))
    assert await daily_rows(clean_db, "curiosity") == curiosity_before


async def run_sql(factory: Factory, statement: str) -> None:
    async with factory() as session:
        await session.execute(text(statement))
        await session.commit()


# --- AC-23 ---------------------------------------------------------------------------------------


def good_lines(path: Path) -> list[dict[str, Any]]:
    return read_export(path)


async def exported(raw: Path, clean_db: Factory, tmp_path: Path) -> list[dict[str, Any]]:
    await seed_by_parse(raw, (1, 2), ())
    target = tmp_path / "good.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)
    return read_export(target)


def mutate(lines: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    out = json.loads(json.dumps(lines))
    daily = next(line for line in out if line["t"] == "daily")
    hourly = next(line for line in out if line["t"] == "hourly")
    if how == "format":
        out[0]["format"] = "something-else"
    elif how == "version":
        out[0]["version"] = 1  # stage 22: only version 2 is accepted
    elif how == "negative-sol":
        daily["sol"] = -1
    elif how == "hour-24":
        hourly["hour"] = 24
    elif how == "nan":
        daily["air_mean_k"] = float("nan")
    elif how == "counts":
        out[0]["counts"]["perseverance"]["daily"] += 1
    elif how == "hourly-counts":
        out[0]["counts"]["perseverance"]["hourly"] -= 1
    elif how == "coverage-above-1":
        daily["coverage"] = 1.5
    elif how == "hours-covered-25":
        daily["hours_covered"] = 25
    elif how == "unknown-rover":
        daily["rover_id"] = "pluto"
    elif how == "missing-column":
        del daily["flags"]
    else:
        raise AssertionError(how)
    return out


INVALID = [
    "format",
    "version",
    "negative-sol",
    "hour-24",
    "nan",
    "counts",
    "hourly-counts",
    "coverage-above-1",
    "hours-covered-25",
    "unknown-rover",
    "missing-column",
]


@pytest.mark.parametrize("how", INVALID)
async def test_invalid_import_file_is_refused_with_code_2_and_the_base_is_unchanged_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path, how: str
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    await insert_daily(clean_db, 50)
    before = await snapshot(clean_db)
    bad = write_export(tmp_path / "bad.jsonl.gz", mutate(lines, how))

    assert await import_(bad) == 2

    assert await snapshot(clean_db) == before


async def test_broken_gzip_is_refused_with_code_2_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    good = write_export(tmp_path / "good.jsonl.gz", lines)
    data = good.read_bytes()
    await insert_daily(clean_db, 50)
    before = await snapshot(clean_db)
    cut = tmp_path / "cut.jsonl.gz"
    cut.write_bytes(data[: len(data) // 2])
    junk = tmp_path / "junk.jsonl.gz"
    junk.write_bytes(b"this is not gzip at all")

    assert await import_(cut) == 2
    assert await import_(junk) == 2

    assert await snapshot(clean_db) == before


async def test_a_line_that_is_not_json_is_refused_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    bad = tmp_path / "bad.jsonl.gz"
    with gzip.open(bad, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(lines[0]) + "\n{not json}\n")

    assert await import_(bad) == 2
    assert await count(clean_db, "weather_daily") == 0


async def test_missing_input_file_is_an_input_error_ac23(
    clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    assert await import_(tmp_path / "nope.jsonl.gz") == 2


async def test_import_over_the_size_limit_is_refused_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    big = write_export(tmp_path / "big.jsonl.gz", lines)
    weather_env(WEATHER_IMPORT_MAX_BYTES="2000")  # the decompressed lines are much larger

    assert await import_(big) == 2

    assert await count(clean_db, "weather_daily") == 0


async def test_gzip_bomb_is_stopped_by_the_decompressed_limit_ac23(
    clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    bomb = tmp_path / "bomb.jsonl.gz"
    with gzip.open(bomb, "wb") as handle:
        handle.write(b'{"t": "meta", "pad": "' + b"a" * 5_000_000 + b'"}\n')
    weather_env(WEATHER_IMPORT_MAX_BYTES="100000")

    assert await import_(bomb) == 2


async def test_import_with_far_fewer_sols_than_in_the_base_needs_force_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw, (1, 2, 3, 4, 5), ())
    small = tmp_path / "small.jsonl.gz"
    assert await export(small) == 0
    for sol in range(6, 21):
        await insert_daily(clean_db, sol, hours=False)  # the base now has 20 sols
    before = await snapshot(clean_db)

    assert await import_(small) == 1  # 5 of 20 is below WEATHER_MIN_KEEP_RATIO (0.9)
    assert await snapshot(clean_db) == before

    assert await import_(small, "--force") == 0
    assert [r["sol"] for r in await daily_rows(clean_db)] == [1, 2, 3, 4, 5]


async def test_import_just_above_the_keep_ratio_needs_no_force_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw, tuple(range(1, 10)), ())  # 9 sols in the file
    nine = tmp_path / "nine.jsonl.gz"
    assert await export(nine) == 0
    await insert_daily(clean_db, 10, hours=False)  # the base has 10: 9 >= 0.9 * 10

    assert await import_(nine) == 0

    assert len(await daily_rows(clean_db)) == 9


async def test_import_into_an_empty_base_never_needs_force_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw, (1,), ())
    target = tmp_path / "one.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)

    assert await import_(target) == 0


# --- AC-20: the import takes the same lock -------------------------------------------------------


async def test_import_exits_with_2_when_the_weather_lock_is_held_ac20(
    raw: Path, clean_db: Factory, tmp_path: Path, settings: Any
) -> None:
    await seed_by_parse(raw, (1,), ())
    target = tmp_path / "one.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)
    engine = create_async_engine(
        settings.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    conn = await engine.connect()
    try:
        assert await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}) is True
        code = await import_(target)
    finally:
        await conn.scalar(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        await conn.close()
        await engine.dispose()

    assert code == 2
    assert await count(clean_db, "weather_daily") == 0
    assert await import_(target) == 0


async def test_dataset_row_survives_the_roundtrip_with_its_source_max_sol_ac22(
    clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    await insert_daily(clean_db, 1859, "perseverance")
    await insert_dataset(clean_db, "perseverance", source_max_sol=1859)
    target = tmp_path / "d.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)

    assert await import_(target) == 0

    row = (await fetch(clean_db, "SELECT * FROM weather_dataset"))[0]
    assert row["source_max_sol"] == 1859
    assert row["updated_at"].isoformat().startswith("2026-11-02T03:04:05")
