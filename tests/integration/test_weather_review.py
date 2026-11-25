"""Stage-17 review fixes, integration side (parser revision, bad Ls, paths, import checks).

Written from the expected behaviour: the hourly rows follow the files even when the daily
summary is unchanged; one unreadable label fails one sol only; stored error texts carry no
absolute paths; the import checks `schema_revision` and keeps its limits on large inputs.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests._weather import (
    Factory,
    count,
    daily_row,
    daily_rows,
    dataset_row,
    file_rows,
    full_meda_sol,
    hourly_rows,
    run_weather,
)
from tests.integration.test_weather_parse import break_file, sol_files
from tests.integration.test_weather_transfer import (
    exported,
    import_,
    read_export,
    snapshot,
    write_export,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

PERSEVERANCE = ("--rover", "perseverance")


@pytest.fixture
def raw(weather_env: Callable[..., Path]) -> Path:
    return weather_env()


def air_plain(h: int) -> float:
    return 200.0 + h


def air_swapped(h: int) -> float:
    """Hours 3 and 4 swapped: min, max, mean and coverage of the sol stay the same."""
    return air_plain({3: 4, 4: 3}.get(h, h))


async def parse(*argv: str) -> int:
    return await run_weather("parse", *PERSEVERANCE, *argv)


def air_by_hour(rows: list[dict]) -> list[float]:
    return [r["air_k"] for r in rows]


# --- MAJOR 2: hour-only changes reach weather_hourly ----------------------------------------------


@pytest.mark.parametrize("how", ["revision-bump", "force"], ids=["revision-bump", "force"])
async def test_hour_only_change_with_same_daily_summary_rewrites_the_hours_ac16(
    raw: Path, clean_db: Factory, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    from app.services import weather_sync

    full_meda_sol(raw, 5, air=air_plain)
    assert await parse() == 0
    daily_before = await daily_row(clean_db, 5)
    hours_before = await hourly_rows(clean_db, 5)
    assert air_by_hour(hours_before)[3] == pytest.approx(203.0, abs=0.05)
    dataset_before = await dataset_row(clean_db)

    full_meda_sol(raw, 5, air=air_swapped)  # same sizes, old mtimes: only a forced run sees them
    argv: tuple[str, ...] = ()
    if how == "revision-bump":
        monkeypatch.setattr(
            weather_sync, "WEATHER_PARSER_REVISION", weather_sync.WEATHER_PARSER_REVISION + 1
        )
    else:
        argv = ("--force",)
    assert await parse(*argv) == 0

    hours_after = await hourly_rows(clean_db, 5)
    assert len(hours_after) == 24
    assert air_by_hour(hours_after)[3] == pytest.approx(204.0, abs=0.05)
    assert air_by_hour(hours_after)[4] == pytest.approx(203.0, abs=0.05)
    daily_after = await daily_row(clean_db, 5)
    for column in ("air_min_k", "air_max_k", "air_mean_k", "coverage", "hours_covered"):
        assert daily_after[column] == pytest.approx(daily_before[column])
    dataset_after = await dataset_row(clean_db)
    assert dataset_after is not None and dataset_before is not None
    assert dataset_after["changed_at"] > dataset_before["changed_at"]


async def test_forced_rerun_without_any_change_leaves_hours_and_dataset_alone_ac14(
    raw: Path, clean_db: Factory
) -> None:
    full_meda_sol(raw, 5, air=air_plain)
    assert await parse() == 0
    hours = await hourly_rows(clean_db, 5)
    dataset = await dataset_row(clean_db)

    assert await parse("--force") == 0

    assert await hourly_rows(clean_db, 5) == hours
    after = await dataset_row(clean_db)
    assert after is not None and dataset is not None
    assert after["changed_at"] == dataset["changed_at"]


# --- MINOR 5: a malformed Ls fails one sol, not the run -------------------------------------------


BAD_LS = pytest.mark.parametrize(
    "bad_ls", ["1.2.3", "e", "--5", "1e"], ids=["dots", "e", "signs", "exp"]
)


@BAD_LS
async def test_bad_ls_in_a_label_does_not_stop_the_run_ac18(
    raw: Path, clean_db: Factory, bad_ls: str
) -> None:
    """The run survives a malformed Ls; the neighbours are stored; no garbage Ls is written."""
    full_meda_sol(raw, 1, air=air_plain)
    full_meda_sol(raw, 2, air=air_plain, ls=bad_ls)  # type: ignore[arg-type]
    full_meda_sol(raw, 3, air=air_plain)

    code = await parse()  # must not raise

    assert code in (0, 1)
    sol2_files = [r for r in await file_rows(clean_db) if r["sol"] == 2]
    assert sol2_files and all(r["label_ls"] is None for r in sol2_files)
    rows = {r["sol"]: r for r in await daily_rows(clean_db)}
    assert {1, 3} <= set(rows)
    if 2 in rows:  # tolerated: Ls falls back to the computed value, never to NaN or a bogus number
        assert rows[2]["ls"] is None or 0.0 <= rows[2]["ls"] < 360.0
    else:
        failed = [r for r in await file_rows(clean_db) if r["status"] == "failed"]
        assert {r["sol"] for r in failed} == {2} and all(r["error"] for r in failed)


# --- LOW: no absolute path in stored error texts --------------------------------------------------


def _roots(raw: Path, tmp_path: Path) -> list[str]:
    return sorted({str(tmp_path), tmp_path.as_posix(), str(raw), raw.as_posix()})


async def test_stored_errors_carry_no_absolute_path_ac18(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    for sol in (1, 2, 3):
        full_meda_sol(raw, sol, air=air_plain)
    break_file(sol_files(raw, 2)[1])

    assert await parse() == 1

    assert any(r["status"] == "failed" for r in await file_rows(clean_db))
    texts = [r["error"] or "" for r in await file_rows(clean_db)]
    dataset = await dataset_row(clean_db)
    if dataset is not None:
        texts.append(dataset.get("last_error") or "")
    for root in _roots(raw, tmp_path):
        assert not any(root in value for value in texts), (root, texts)


async def test_last_error_after_a_missing_raw_dir_carries_no_absolute_path(
    clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    missing = tmp_path / "does-not-exist-here"
    weather_env(WEATHER_RAW_DIR=str(missing))

    assert await parse() == 2

    dataset = await dataset_row(clean_db)
    if dataset is not None:
        error = dataset.get("last_error") or ""
        assert str(tmp_path) not in error and tmp_path.as_posix() not in error


async def test_export_carries_no_absolute_path_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    full_meda_sol(raw, 1, air=air_plain)
    assert await parse() == 0
    target = tmp_path / "w.jsonl.gz"
    assert await run_weather("export", "--output", str(target)) == 0

    with gzip.open(target, "rt", encoding="utf-8") as handle:
        body = handle.read()

    for root in _roots(raw, tmp_path):
        assert root not in body


# --- MINOR 8: import checks the schema revision; limits hold on large inputs ----------------------


@pytest.mark.parametrize(
    "revision",
    ["0017", "0019", "0021", "", None],
    ids=["older", "previous", "newer", "empty", "null"],
)
async def test_import_of_another_schema_revision_is_refused_and_changes_nothing_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path, revision: str | None
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    before = await snapshot(clean_db)
    lines[0]["schema_revision"] = revision
    bad = write_export(tmp_path / "bad-rev.jsonl.gz", lines)

    assert await import_(bad) == 2

    assert await snapshot(clean_db) == before
    assert await count(clean_db, "weather_daily") == 0


async def test_import_without_a_schema_revision_key_is_refused_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    del lines[0]["schema_revision"]
    bad = write_export(tmp_path / "no-rev.jsonl.gz", lines)

    assert await import_(bad) == 2
    assert await count(clean_db, "weather_daily") == 0


async def test_import_with_the_current_schema_revision_still_works_ac22(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    assert lines[0]["schema_revision"] == "0020"
    good = write_export(tmp_path / "good-rev.jsonl.gz", lines)

    assert await import_(good) == 0
    assert await count(clean_db, "weather_daily") == 2


async def test_many_small_lines_over_the_decompressed_limit_are_refused_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    padded = tmp_path / "padded.jsonl.gz"
    with gzip.open(padded, "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
        for _ in range(5000):  # thousands of short lines past the limit
            handle.write('{"t": "noise", "pad": "' + "x" * 200 + '"}\n')
    weather_env(WEATHER_IMPORT_MAX_BYTES=str(300_000))

    assert await import_(padded) == 2
    assert await count(clean_db, "weather_daily") == 0


async def test_a_single_endless_line_is_stopped_by_the_limit_ac23(
    clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    bomb = tmp_path / "endless.jsonl.gz"
    with gzip.open(bomb, "wb") as handle:
        handle.write(b'{"t": "meta", "pad": "')
        for _ in range(40):
            handle.write(b"a" * 1_000_000)  # 40 MB without a newline
    weather_env(WEATHER_IMPORT_MAX_BYTES="1000000")

    assert await import_(bomb) == 2


async def test_import_well_under_the_limit_is_accepted_ac23(
    raw: Path, clean_db: Factory, tmp_path: Path, weather_env: Callable[..., Path]
) -> None:
    lines = await exported(raw, clean_db, tmp_path)
    good = write_export(tmp_path / "ok.jsonl.gz", lines)
    size = sum(len(json.dumps(line)) + 1 for line in read_export(good))
    weather_env(WEATHER_IMPORT_MAX_BYTES=str(max(size * 2, 4096)))

    assert await import_(good) == 0
    assert await count(clean_db, "weather_daily") == 2
