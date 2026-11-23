"""Stage 21 review fixes: sol-level failure, crash reporting, repeated start times, import
validation, block limit, listing parsing, range runs (lead decisions of 2026-11-23).

md5 is NULL exactly when the status is `failed` or `md5_mismatch`; a repeated `START_OBS_UTC`
inside one file is counted in `dropped_invalid` (used + dropped = number of dose blocks).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import respx

from app import cli
from app.services.alerts import rad_sync_message
from app.services.rad_sync import combine_sol, merge_files_counted
from app.sources.rad import RadParser, parse_checksums
from tests._rad import (
    CHECKSUMS,
    F_4842,
    F_4843,
    Factory,
    RadHost,
    attr,
    daily_row,
    file_row,
    fixture_host,
    obs_block,
    rad_daily,
    rad_dataset,
    rad_file,
    rad_files,
    rel_path,
    run_rad,
    synthetic_host,
    synthetic_name,
    synthetic_path,
    twenty_sols,
)
from tests.integration.test_rad_transfer import read_export, snapshot, write_export

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("clean_db", "rad_env", "fake_sleep"),
]

P4843 = rel_path(F_4843)


@pytest.fixture
def host(upstream_router: respx.MockRouter, events: list[tuple[str, Any]]) -> RadHost:
    return fixture_host(upstream_router, events, F_4842, F_4843)


# --- 1. a sol that fails after its files were read -----------------------------------------------


async def test_sync_sol_without_a_readable_date_fails_its_files_and_keeps_the_row_then_retries(
    host: RadHost, clean_db: Factory
) -> None:
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    before = await daily_row(clean_db, 4843)
    undated = rad_file(
        4843, [obs_block(0, utc="garbage"), obs_block(2, utc="garbage2")], start_time="garbage"
    )
    host.set_file(P4843, undated)

    code = await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843")

    assert code == 1
    assert await daily_row(clean_db, 4843) == before  # untouched
    row = await file_row(clean_db, P4843)
    assert row["status"] == "failed" and row["md5"] is None and row["error"]
    host.requests.clear()
    from tests._rad import fixture

    host.set_file(P4843, fixture(F_4843))  # the source is fixed

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0

    assert [r.path for r in host.calls("files")] == [P4843]  # the sol is retried
    assert (await file_row(clean_db, P4843))["status"] == "loaded"
    assert await daily_row(clean_db, 4843) is not None


async def test_sync_failure_of_a_sol_does_not_stop_the_other_sols(
    host: RadHost, clean_db: Factory
) -> None:
    host.set_file(
        rel_path(F_4842),
        rad_file(4842, [obs_block(0, utc="garbage")], start_time="garbage"),
    )

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 1
    assert [r["sol"] for r in await rad_daily(clean_db)] == [4843]
    assert (await file_row(clean_db, rel_path(F_4842)))["status"] == "failed"


# --- 3. a crash of rad sync  ---------------------------------------------------------------------


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


async def test_sync_crash_records_last_error_sends_the_alert_and_exits_1(
    host: RadHost, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = FakeNotifier()
    monkeypatch.setattr(
        cli.TelegramNotifier, "from_settings", classmethod(lambda cls, settings: notifier)
    )

    async def boom(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli.RadLoader, "run", boom)

    code = await run_rad("sync")

    assert code == 1
    dataset = await rad_dataset(clean_db)
    assert dataset is not None and "kaboom" in dataset["last_error"]
    assert dataset["checked_at"] is not None
    assert len(notifier.sent) == 1
    assert notifier.sent[0].startswith(rad_sync_message(1, None))
    assert "kaboom" in notifier.sent[0]


async def test_sync_crash_without_telegram_still_records_the_error(
    host: RadHost, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli.TelegramNotifier, "from_settings", classmethod(lambda cls, settings: None)
    )

    async def boom(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli.RadLoader, "run", boom)

    assert await run_rad("sync") == 1
    dataset = await rad_dataset(clean_db)
    assert dataset is not None and "kaboom" in dataset["last_error"]


async def test_sync_succeeded_at_moves_only_on_exit_0(host: RadHost, clean_db: Factory) -> None:
    host.md5_override[P4843] = "0" * 32
    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 1
    dataset = await rad_dataset(clean_db)
    assert dataset is not None and dataset["succeeded_at"] is None
    host.md5_override.clear()
    host.bump(CHECKSUMS)

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 0
    ok = await rad_dataset(clean_db)
    assert ok is not None and ok["succeeded_at"] is not None
    host.md5_override[P4843] = "f" * 32
    host.bump(CHECKSUMS)

    assert await run_rad("sync", "--from-sol", "4843", "--to-sol", "4843") == 1

    failed = await rad_dataset(clean_db)
    assert failed is not None and failed["succeeded_at"] == ok["succeeded_at"]


# --- 4. a repeated START_OBS_UTC inside one file  ------------------------------------------------


def parsed(data: bytes) -> Any:
    parser = RadParser()
    parser.feed(data)
    return parser.result()


def test_repeated_start_time_in_one_file_is_counted_invalid() -> None:
    data = rad_file(
        4843,
        [
            obs_block(0, utc="2026-080 17:18:00"),
            obs_block(2, utc="2026-080 17:18:00"),
            obs_block(4, utc="2026-080 17:35:00"),
        ],
    )

    merged, repeated = merge_files_counted([("p", parsed(data))])
    summary = combine_sol(merged, 0.5, repeated=repeated)

    assert repeated == 1 and len(merged) == 2
    used = attr(summary, "n_used", "used")
    invalid = attr(summary, "n_dropped_invalid", "dropped_invalid")
    assert (used, invalid) == (2, 1)  # used + dropped = the 3 blocks


def test_same_start_time_in_two_files_is_a_merge_not_a_repeat() -> None:
    one = parsed(rad_file(4843, [obs_block(0, utc="2026-080 17:18:00")]))
    two = parsed(rad_file(4843, [obs_block(0, utc="2026-080 17:18:00")]))

    merged, repeated = merge_files_counted([("a", one), ("b", two)])

    assert repeated == 0 and len(merged) == 1


def test_combine_repeated_argument_adds_to_dropped_invalid() -> None:
    from tests.unit.test_rad_combine import mk

    summary = combine_sol([mk(0, 6.0, 900, 7200)], 0.5, repeated=3)

    assert attr(summary, "n_dropped_invalid", "dropped_invalid") == 3


async def test_sync_repeated_start_time_shows_in_the_stored_counters(
    upstream_router: respx.MockRouter, events: list[tuple[str, Any]], clean_db: Factory
) -> None:
    body = rad_file(
        700,
        [
            obs_block(0, utc="2026-080 17:18:00"),
            obs_block(2, utc="2026-080 17:18:00"),
            obs_block(4, utc="2026-080 17:35:00"),
        ],
    )
    synthetic_host(upstream_router, events, {synthetic_path(synthetic_name(700)): body})

    assert await run_rad("sync", "--from-sol", "700", "--to-sol", "700") == 0

    row = await daily_row(clean_db, 700)
    assert row is not None and (row["n_used"], row["n_dropped_invalid"]) == (2, 1)


# --- 5a. import validation -----------------------------------------------------------------------


def file_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [line for line in lines if line["t"] == "file"]


def daily_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [line for line in lines if line["t"] == "daily"]


def bend(how: str, lines: list[dict[str, Any]]) -> None:
    if how == "source-path-pattern":
        daily_lines(lines)[0]["source_path"] = "../../etc/passwd"
    elif how == "source-path-long":
        daily_lines(lines)[0]["source_path"] = "DATA/SOL_00001_00002/RAD_RDR_" + "A" * 200 + ".TXT"
    elif how == "duplicate-of-pattern":
        file_lines(lines)[0]["duplicate_of"] = "not a volume path"
    elif how == "duplicate-of-long":
        file_lines(lines)[0]["duplicate_of"] = "DATA/SOL_00001_00002/RAD_RDR_" + "A" * 200 + ".TXT"
    elif how == "source-path-not-imported":
        daily_lines(lines)[0]["source_path"] = (
            "DATA/SOL_09999_10000/RAD_RDR_2030_001_00_00_9999_V00.TXT"
        )
    elif how == "error-too-long":
        file_lines(lines)[0]["error"] = "x" * 501
    elif how == "failed-with-md5":
        file_lines(lines)[0]["status"] = "failed"
    elif how == "loaded-without-md5":
        file_lines(lines)[0]["md5"] = None
    else:  # pragma: no cover
        raise AssertionError(how)


@pytest.mark.parametrize(
    "how",
    [
        "source-path-pattern",
        "source-path-long",
        "duplicate-of-pattern",
        "duplicate-of-long",
        "source-path-not-imported",
        "error-too-long",
        "failed-with-md5",
        "loaded-without-md5",
    ],
)
async def test_import_rejects_unsafe_or_inconsistent_values_and_changes_nothing(
    host: RadHost, clean_db: Factory, tmp_path: Path, how: str
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0
    good = tmp_path / "good.gz"
    assert await run_rad("export", "--output", str(good)) == 0
    before = await snapshot(clean_db)
    lines = read_export(good)
    bend(how, lines)
    bad = write_export(tmp_path / "bad.gz", lines)

    code = await run_rad("import", "--input", str(bad))

    assert code == 2
    assert await snapshot(clean_db) == before


async def test_import_accepts_a_failed_file_row_without_md5(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    host.fail[P4843] = ["404"]
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 1
    out = tmp_path / "x.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    files = await rad_files(clean_db)

    assert await run_rad("import", "--input", str(out)) == 0

    assert await rad_files(clean_db) == files
    assert (await file_row(clean_db, P4843))["md5"] is None


# --- 5b. block limit  ----------------------------------------------------------------------------


async def test_sync_file_with_more_blocks_than_the_limit_fails_ac8(
    host: RadHost, clean_db: Factory, rad_env: Any
) -> None:
    rad_env(RAD_MAX_BLOCKS="3")  # the 4843 fixture has 7 observation blocks

    code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")

    assert code == 1
    assert (await file_row(clean_db, P4843))["status"] == "failed"
    assert await rad_daily(clean_db) == []


# --- 5c. listing parser  -------------------------------------------------------------------------

LINE = (
    "2dc001da9cf89ee807706eef84bde2e0  ./DATA/SOL_00000_00089/RAD_RDR_2012_228_20_41_0010_V00.TXT"
)
OTHER = (
    "fba74bc746dc8ccdcbe71e1f224513f7  ./DATA/SOL_01649_01772/RAD_RDR_2017_085_15_54_1649_V00.TXT"
)


def test_parse_checksums_long_trailing_whitespace_is_fast_and_correct() -> None:
    text = "\n".join([LINE + " " * 200_000, " " * 200_000, OTHER]) + "\n"

    started = time.perf_counter()
    entries = parse_checksums(text)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    paths = [attr(e, "path") for e in entries]
    assert paths[-1] == OTHER.split("  ./")[1]
    assert all(" " not in p for p in paths)  # padding never ends up in a path
    assert len(paths) in (1, 2)


def test_parse_checksums_many_long_junk_lines_stay_linear() -> None:
    text = "\n".join(["a" * 50_000 + " " * 50_000] * 50 + [LINE]) + "\n"

    started = time.perf_counter()
    entries = parse_checksums(text)

    assert time.perf_counter() - started < 2.0
    assert [attr(e, "sol") for e in entries] == [10]


# --- 5d. range runs never go for files that left the listing -----------------------------


@pytest.mark.parametrize(("lo", "hi"), [("100", "119"), ("110", "119"), ("105", "105")])
async def test_range_run_does_not_download_because_a_file_left_the_listing(
    upstream_router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    lo: str,
    hi: str,
) -> None:
    listing, paths = twenty_sols(upstream_router, events)
    assert await run_rad("sync") == 0
    listing.remove_file(paths[105])
    listing.requests.clear()

    code = await run_rad("sync", "--from-sol", lo, "--to-sol", hi)

    assert code == 0
    assert listing.calls("files") == []
    assert len(await rad_files(clean_db)) == 20  # nothing deleted by a range run
    assert len(await rad_daily(clean_db)) == 20
