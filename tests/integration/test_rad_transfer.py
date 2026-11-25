"""`rad export` / `rad import` and the run lock (stage 21, AC-24, AC-27, AC-28, AC-29).

The export is gzip JSON Lines: a `meta` line (`format` `mars-rad-export`, `version` 2,
`schema_revision`, `counts`, `dataset`), then `file` and `daily` lines with all columns.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest
import respx
from sqlalchemy import text

from tests._rad import (
    CHECKSUMS,
    F_4842,
    F_4843,
    LOCK_KEY,
    TIMING,
    Factory,
    RadHost,
    fixture_host,
    rad_daily,
    rad_dataset,
    rad_files,
    run_rad,
    twenty_sols,
    wipe_rad,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("clean_db", "rad_env", "fake_sleep"),
]


@pytest.fixture
def host(upstream_router: respx.MockRouter, events: list[tuple[str, Any]]) -> RadHost:
    return fixture_host(upstream_router, events, F_4842, F_4843)


async def load_two_sols() -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0


def read_export(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_export(path: Path, lines: list[dict[str, Any]], *, raw: bytes | None = None) -> Path:
    if raw is not None:
        path.write_bytes(raw)
        return path
    body = "\n".join(json.dumps(line) for line in lines) + "\n"
    path.write_bytes(gzip.compress(body.encode("utf-8")))
    return path


async def snapshot(factory: Factory) -> tuple[Any, Any, Any]:
    return await rad_files(factory), await rad_daily(factory), await rad_dataset(factory)


def comparable_dataset(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """`rad_dataset` without the columns that describe an import itself."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in ("imported_at", "import_meta")}


# --- AC-27: export  ------------------------------------------------------------------------------


async def test_export_writes_gzip_json_lines_with_meta_files_and_daily_rows_ac27(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    out = tmp_path / "rad.jsonl.gz"

    code = await run_rad("export", "--output", str(out))

    assert code == 0
    lines = read_export(out)
    meta = lines[0]
    assert meta["t"] == "meta"
    assert meta["format"] == "mars-rad-export"
    assert meta["version"] == 2  # stage 22: hourly lines
    assert meta["schema_revision"] == "0021"
    assert (meta["counts"]["file"], meta["counts"]["daily"]) == (2, 2)
    assert meta["exported_at"]
    assert isinstance(meta["dataset"], dict)
    assert meta["dataset"]["checksums_etag"] == '"cs-1"'
    kinds = [line["t"] for line in lines[1:]]
    assert sorted(k for k in kinds if k != "hourly") == ["daily", "daily", "file", "file"]
    daily = [line for line in lines if line["t"] == "daily"]
    assert sorted(line["sol"] for line in daily) == [4842, 4843]
    assert {line["path"] for line in lines if line["t"] == "file"} == {
        f["path"] for f in await rad_files(clean_db)
    }


async def test_export_file_is_a_real_gzip_stream_ac27(host: RadHost, tmp_path: Path) -> None:
    await load_two_sols()
    out = tmp_path / "rad.jsonl.gz"

    assert await run_rad("export", "--output", str(out)) == 0

    assert out.read_bytes()[:2] == b"\x1f\x8b"


async def test_export_of_an_empty_database_has_a_meta_line_only_ac27(tmp_path: Path) -> None:
    out = tmp_path / "empty.jsonl.gz"

    code = await run_rad("export", "--output", str(out))

    assert code == 0
    lines = read_export(out)
    assert [line["t"] for line in lines] == ["meta"]
    assert (lines[0]["counts"]["file"], lines[0]["counts"]["daily"]) == (0, 0)


async def test_export_does_not_change_the_database_ac27(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    before = await snapshot(clean_db)

    assert await run_rad("export", "--output", str(tmp_path / "x.gz")) == 0

    assert await snapshot(clean_db) == before


# --- AC-27: import  ------------------------------------------------------------------------------


async def test_import_restores_all_three_tables_column_for_column_ac27(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    files, daily, dataset = await snapshot(clean_db)
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    await wipe_rad(clean_db)

    code = await run_rad("import", "--input", str(out))

    assert code == 0
    assert await rad_files(clean_db) == files
    assert await rad_daily(clean_db) == daily  # `updated_at` included
    restored = await rad_dataset(clean_db)
    assert comparable_dataset(restored) == comparable_dataset(dataset)
    assert restored is not None and restored["imported_at"] is not None
    assert restored["import_meta"]


async def test_import_twice_changes_nothing_ac27(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    await wipe_rad(clean_db)
    assert await run_rad("import", "--input", str(out)) == 0
    files, daily, _ = await snapshot(clean_db)

    code = await run_rad("import", "--input", str(out))

    assert code == 0
    assert await rad_files(clean_db) == files
    assert await rad_daily(clean_db) == daily


async def test_import_over_a_non_empty_database_replaces_its_content_ac27(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    files, daily, _ = await snapshot(clean_db)
    async with clean_db() as session:
        await session.execute(text("UPDATE rad_daily SET dose_b_mean = 99 WHERE sol = 4843"))
        await session.execute(text("DELETE FROM rad_daily WHERE sol = 4842"))
        await session.commit()

    assert await run_rad("import", "--input", str(out)) == 0

    assert await rad_daily(clean_db) == daily
    assert await rad_files(clean_db) == files


async def test_import_missing_file_exits_2(tmp_path: Path) -> None:
    assert await run_rad("import", "--input", str(tmp_path / "nope.gz")) == 2


# --- AC-28: bad imports  -------------------------------------------------------------------------


def corrupt(lines: list[dict[str, Any]], how: str) -> list[dict[str, Any]]:
    out = json.loads(json.dumps(lines))
    meta = out[0]
    daily = [i for i, line in enumerate(out) if line["t"] == "daily"]
    if how == "format":
        meta["format"] = "something-else"
    elif how == "version":
        meta["version"] = 3
    elif how == "sol-zero":
        out[daily[0]]["sol"] = 0
    elif how == "sol-negative":
        out[daily[0]]["sol"] = -5
    elif how == "nan-dose":
        out[daily[0]]["dose_b_mean"] = float("nan")
    elif how == "negative-dose":
        out[daily[0]]["dose_e_min"] = -1.0
    elif how == "count-too-many":
        meta["counts"]["daily"] += 1
    elif how == "count-too-few":
        del out[daily[-1]]
    elif how == "unknown-kind":
        out.append({"t": "mystery", "x": 1})
    elif how == "no-meta":
        out = out[1:]
    elif how == "zero-live":
        out[daily[0]]["live_s"] = 0
    else:  # pragma: no cover
        raise AssertionError(how)
    return out


@pytest.mark.parametrize(
    "how",
    [
        "format",
        "version",
        "sol-zero",
        "sol-negative",
        "nan-dose",
        "negative-dose",
        "count-too-many",
        "count-too-few",
        "unknown-kind",
        "no-meta",
        "zero-live",
    ],
)
async def test_import_invalid_content_exits_2_and_changes_nothing_ac28(
    host: RadHost, clean_db: Factory, tmp_path: Path, how: str
) -> None:
    await load_two_sols()
    good = tmp_path / "good.gz"
    assert await run_rad("export", "--output", str(good)) == 0
    before = await snapshot(clean_db)
    bad = write_export(tmp_path / "bad.gz", corrupt(read_export(good), how))

    code = await run_rad("import", "--input", str(bad))

    assert code == 2
    assert await snapshot(clean_db) == before


@pytest.mark.parametrize(
    "raw",
    [b"this is not gzip at all", b"", b"\x1f\x8b\x08\x00garbage"],
    ids=["text", "empty", "broken-header"],
)
async def test_import_broken_gzip_exits_2_and_changes_nothing_ac28(
    host: RadHost, clean_db: Factory, tmp_path: Path, raw: bytes
) -> None:
    await load_two_sols()
    before = await snapshot(clean_db)
    bad = write_export(tmp_path / "bad.gz", [], raw=raw)

    code = await run_rad("import", "--input", str(bad))

    assert code == 2
    assert await snapshot(clean_db) == before


async def test_import_truncated_gzip_exits_2_and_changes_nothing_ac28(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    good = tmp_path / "good.gz"
    assert await run_rad("export", "--output", str(good)) == 0
    before = await snapshot(clean_db)
    cut = tmp_path / "cut.gz"
    cut.write_bytes(good.read_bytes()[:-20])

    code = await run_rad("import", "--input", str(cut))

    assert code == 2
    assert await snapshot(clean_db) == before


async def test_import_not_json_line_exits_2_and_changes_nothing_ac28(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    before = await snapshot(clean_db)
    bad = tmp_path / "bad.gz"
    bad.write_bytes(gzip.compress(b"{not json}\n"))

    assert await run_rad("import", "--input", str(bad)) == 2
    assert await snapshot(clean_db) == before


async def test_import_larger_than_the_unpacked_limit_exits_2_ac28(
    host: RadHost, clean_db: Factory, tmp_path: Path, rad_env: Any
) -> None:
    await load_two_sols()
    good = tmp_path / "good.gz"
    assert await run_rad("export", "--output", str(good)) == 0
    before = await snapshot(clean_db)
    rad_env(RAD_IMPORT_MAX_BYTES="1024")  # the export unpacks to more than 1 KiB

    assert await run_rad("import", "--input", str(good)) == 2
    assert await snapshot(clean_db) == before


async def test_import_with_much_fewer_daily_rows_is_refused_unless_forced_ac28(
    upstream_router: respx.MockRouter,
    events: list[tuple[str, Any]],
    clean_db: Factory,
    tmp_path: Path,
) -> None:
    twenty_sols(upstream_router, events)
    assert await run_rad("sync") == 0
    big = tmp_path / "big.gz"
    assert await run_rad("export", "--output", str(big)) == 0
    async with clean_db() as session:  # keep 5 sols
        await session.execute(text("DELETE FROM rad_daily WHERE sol >= 105"))
        await session.execute(text("DELETE FROM rad_files WHERE sol >= 105"))
        await session.commit()
    small = tmp_path / "small.gz"
    assert await run_rad("export", "--output", str(small)) == 0
    assert await run_rad("import", "--input", str(big)) == 0  # more rows over fewer: fine
    assert len(await rad_daily(clean_db)) == 20
    before = await snapshot(clean_db)

    code = await run_rad("import", "--input", str(small))  # 5 < 0.9 x 20

    assert code == 1
    assert await snapshot(clean_db) == before

    code = await run_rad("import", "--input", str(small), "--force")

    assert code == 0
    assert len(await rad_daily(clean_db)) == 5
    assert len(await rad_files(clean_db)) == 5


# --- AC-29: a sync after an import  --------------------------------------------------------------


async def test_sync_after_import_asks_with_the_validators_of_the_export_ac29(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    await wipe_rad(clean_db)
    assert await run_rad("import", "--input", str(out)) == 0
    host.requests.clear()

    code = await run_rad("sync")

    assert code == 0
    calls = host.calls()
    assert sorted(r.path for r in calls) == [CHECKSUMS, TIMING]
    assert {r.path: r.if_none_match for r in calls} == {CHECKSUMS: '"cs-1"', TIMING: '"tm-1"'}
    assert host.calls("files") == []  # the stored md5 of every file matches the list


async def test_sync_after_import_downloads_only_what_changed_since_the_export_ac29(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    from tests._rad import fixture, rel_path

    await load_two_sols()
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    await wipe_rad(clean_db)
    assert await run_rad("import", "--input", str(out)) == 0
    host.requests.clear()
    host.set_file(rel_path(F_4843), fixture(F_4843).replace(b"6.84128", b"6.94128", 1))

    assert await run_rad("sync") == 0

    assert [r.path for r in host.calls("files")] == [rel_path(F_4843)]


# --- AC-24: one run at a time  -------------------------------------------------------------------


async def hold_lock(factory: Factory) -> Any:
    session = factory()
    await session.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
    return session


async def release_lock(session: Any) -> None:
    await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    await session.rollback()
    await session.close()


async def test_sync_while_another_run_holds_the_lock_exits_2_without_requests_ac24(
    host: RadHost, clean_db: Factory
) -> None:
    session = await hold_lock(clean_db)
    try:
        code = await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843")
    finally:
        await release_lock(session)

    assert code == 2
    assert host.calls() == []
    assert await rad_daily(clean_db) == []


async def test_sync_runs_again_after_the_lock_is_released_ac24(
    host: RadHost, clean_db: Factory
) -> None:
    session = await hold_lock(clean_db)
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 2
    await release_lock(session)

    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    assert [r["sol"] for r in await rad_daily(clean_db)] == [4842, 4843]


async def test_import_while_another_run_holds_the_lock_exits_2_ac24(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load_two_sols()
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    await wipe_rad(clean_db)
    session = await hold_lock(clean_db)
    try:
        code = await run_rad("import", "--input", str(out))
    finally:
        await release_lock(session)

    assert code == 2
    assert await rad_daily(clean_db) == []


async def test_sync_holding_no_lock_after_the_run_ends_ac24(
    host: RadHost, clean_db: Factory
) -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0

    async with clean_db() as session:
        free = await session.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        assert free is True
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})


# --- status  -------------------------------------------------------------------------------------


async def test_status_with_loaded_data_exits_0(host: RadHost) -> None:
    await load_two_sols()

    assert await run_rad("status") == 0


async def test_status_on_an_empty_database_exits_0() -> None:
    assert await run_rad("status") == 0
