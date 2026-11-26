"""`rad export` / `rad import` in format version 2 (stage 22, AC-46)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text

from tests._rad import (
    F_4842,
    F_4843,
    REV,
    Factory,
    RadHost,
    fixture_host,
    rad_daily,
    rad_dataset,
    rad_files,
    rad_hourly,
    run_rad,
    wipe_rad,
)
from tests.integration.test_rad_transfer import read_export, write_export

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("clean_db", "rad_env", "fake_sleep"),
]

V2 = "/api/v2"


@pytest.fixture
def host(upstream_router: respx.MockRouter, events: list[tuple[str, Any]]) -> RadHost:
    return fixture_host(upstream_router, events, F_4842, F_4843)


async def load() -> None:
    assert await run_rad("sync", "--from-sol", "4842", "--to-sol", "4843") == 0


async def snapshot4(factory: Factory) -> tuple[Any, ...]:
    return (
        await rad_files(factory),
        await rad_daily(factory),
        await rad_hourly(factory),
        await rad_dataset(factory),
    )


async def test_export_has_version_2_hourly_lines_and_the_new_daily_keys_ac46(
    host: RadHost, tmp_path: Path
) -> None:
    """AC-46: version 2, schema 0021, `{"t": "hourly"}` lines, new keys in the daily lines."""
    await load()
    out = tmp_path / "rad.jsonl.gz"

    assert await run_rad("export", "--output", str(out)) == 0

    lines = read_export(out)
    meta = lines[0]
    assert (meta["version"], meta["schema_revision"]) == (2, "0021")
    hourly = [line for line in lines if line["t"] == "hourly"]
    assert [(h["sol"], h["hour"], h["n_obs"]) for h in hourly] == [(4843, 0, 2)]
    assert {"live_s", "dose_b_mean", "dose_e_mean", "bu_count"} <= set(hourly[0])
    dailies = {d["sol"]: d for d in lines if d["t"] == "daily"}
    for daily in dailies.values():
        assert {"hourly_revision", "hourly_reject", "hourly_dropped"} <= set(daily)
    assert dailies[4843]["hourly_revision"] == REV and dailies[4843]["hourly_reject"] is None
    assert dailies[4842]["hourly_reject"] == "off_sol"


async def test_export_meta_counts_include_the_hours_ac46(host: RadHost, tmp_path: Path) -> None:
    await load()
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0

    assert read_export(out)[0]["counts"] == {"file": 2, "daily": 2, "hourly": 1}


async def test_import_into_an_empty_database_restores_hours_and_the_api_answer_ac46(
    host: RadHost, clean_db: Factory, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    """AC-46: after the import `/radiation/{sol}` is the same as before the export."""
    await load()
    before = await snapshot4(clean_db)
    answers = {
        sol: (await client.get(f"{V2}/rovers/curiosity/radiation/{sol}")).json()
        for sol in (4842, 4843)
    }
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    await wipe_rad(clean_db)

    assert await run_rad("import", "--input", str(out)) == 0

    assert (await snapshot4(clean_db))[:3] == before[:3]
    for sol, answer in answers.items():
        again = (await client.get(f"{V2}/rovers/curiosity/radiation/{sol}")).json()
        assert again == answer
    assert answers[4843]["hours"][0]["observations"] == 2
    assert answers[4842]["hours"] is None


async def test_import_replaces_the_hours_of_a_non_empty_database_ac46(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    await load()
    out = tmp_path / "rad.jsonl.gz"
    assert await run_rad("export", "--output", str(out)) == 0
    hours = await rad_hourly(clean_db)
    async with clean_db() as session:
        await session.execute(text("UPDATE rad_hourly SET n_obs = 9, hour = 7"))
        await session.commit()

    assert await run_rad("import", "--input", str(out)) == 0

    assert await rad_hourly(clean_db) == hours


@pytest.mark.parametrize(
    ("version", "revision"),
    [(1, "0017"), (2, "0017"), (1, "0021"), (2, "0020")],
    ids=["v1-0017", "v2-0017", "v1-0021", "v2-0020"],
)
async def test_import_of_an_old_format_exits_2_and_changes_nothing_ac46(
    host: RadHost, clean_db: Factory, tmp_path: Path, version: int, revision: str
) -> None:
    """AC-46 (amended): a file of version 1 / schema 0017 -> exit 2, database untouched."""
    await load()
    good = tmp_path / "good.gz"
    assert await run_rad("export", "--output", str(good)) == 0
    before = await snapshot4(clean_db)
    lines = [line for line in read_export(good) if line["t"] != "hourly"]
    lines[0]["version"], lines[0]["schema_revision"] = version, revision
    lines[0]["counts"].pop("hourly", None)
    for line in lines:
        if line["t"] == "daily":
            for key in ("hourly_revision", "hourly_reject", "hourly_dropped"):
                line.pop(key, None)
    bad = write_export(tmp_path / "old.gz", lines)

    assert await run_rad("import", "--input", str(bad)) == 2
    assert await snapshot4(clean_db) == before


@pytest.mark.parametrize("how", ["orphan-hour", "dup-hour", "hour-24", "hours-for-rejected"])
async def test_import_inconsistent_hours_exit_2_and_change_nothing_ac46(
    host: RadHost, clean_db: Factory, tmp_path: Path, how: str
) -> None:
    await load()
    good = tmp_path / "good.gz"
    assert await run_rad("export", "--output", str(good)) == 0
    before = await snapshot4(clean_db)
    lines = read_export(good)
    hour = next(line for line in lines if line["t"] == "hourly")
    if how == "orphan-hour":
        lines.append({**hour, "sol": 99999})
    elif how == "dup-hour":
        lines.append(dict(hour))
    elif how == "hour-24":
        hour["hour"] = 24
    else:
        hour["sol"] = 4842  # the rejected sol has no hours
    if how in ("orphan-hour", "dup-hour"):
        lines[0]["counts"]["hourly"] += 1
    bad = write_export(tmp_path / "bad.gz", lines)

    assert await run_rad("import", "--input", str(bad)) == 2
    assert await snapshot4(clean_db) == before


async def export_lines(tmp_path: Path) -> tuple[list[dict[str, Any]], Any]:
    await load()
    good = tmp_path / "good.gz"
    assert await run_rad("export", "--output", str(good)) == 0
    return read_export(good), good


def by_kind(lines: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [line for line in lines if line["t"] == kind]


@pytest.mark.parametrize(
    "how",
    [
        "live-tiny",
        "live-1e-300",
        "bu-over-int64",
        "sum-mismatch-accepted",
        "dropped-mismatch-rejected",
    ],
)
async def test_import_review_bounds_exit_2_and_change_nothing_ac46(
    host: RadHost, clean_db: Factory, tmp_path: Path, how: str
) -> None:
    lines, _ = await export_lines(tmp_path)
    before = await snapshot4(clean_db)
    hour = by_kind(lines, "hourly")[0]
    dailies = {d["sol"]: d for d in by_kind(lines, "daily")}
    if how == "live-tiny":
        hour["live_s"] = 0.5
    elif how == "live-1e-300":
        hour["live_s"] = 1e-300
    elif how == "bu-over-int64":
        hour["bu_count"] = 2**63
    elif how == "sum-mismatch-accepted":
        dailies[4843]["hourly_dropped"] += 1
    else:
        dailies[4842]["hourly_dropped"] = 1
    bad = write_export(tmp_path / "bad.gz", lines)

    assert await run_rad("import", "--input", str(bad)) == 2
    assert await snapshot4(clean_db) == before


async def test_import_full_v2_file_with_only_the_schema_revision_changed_exits_2_ac46(
    host: RadHost, clean_db: Factory, tmp_path: Path
) -> None:
    lines, _ = await export_lines(tmp_path)
    before = await snapshot4(clean_db)
    lines[0]["schema_revision"] = "0020"
    bad = write_export(tmp_path / "bad.gz", lines)

    assert await run_rad("import", "--input", str(bad)) == 2
    assert await snapshot4(clean_db) == before


async def test_export_and_import_print_the_number_of_hours_ac46(
    host: RadHost, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await load()
    out = tmp_path / "rad.jsonl.gz"
    capsys.readouterr()

    assert await run_rad("export", "--output", str(out)) == 0
    exported = capsys.readouterr().out
    assert await run_rad("import", "--input", str(out)) == 0
    imported = capsys.readouterr().out

    assert "1 hours" in exported
    assert "1 hours" in imported
