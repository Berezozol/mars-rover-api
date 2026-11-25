"""Re-reading with parser revision 5 and export/import format 2 (stage 22 B)

Covers AC-22 (re-reading with revision 5) and AC-24 (export version 2 and import)..

Spec: docs/proposals/sol-conditions.md section 2 (AC-22, AC-24), section 6 (flow, export) and
section 7 (`WEATHER_PARSER_REVISION = 5`, `EXPORT_VERSION = 2`, `SCHEMA_REVISION = "0020"`).
Data comes from `weather parse` on synthetic raw trees (and the real fixtures for the API check).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests._weather import (
    Factory,
    copy_fixture_sols,
    daily_rows,
    file_rows,
    hourly_rows,
    run_weather,
    wipe_weather,
)
from tests.api.v2.conftest import V2
from tests.integration.test_weather_transfer import (
    export,
    import_,
    read_export,
    seed_by_parse,
    snapshot,
    write_export,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

NEW_HOURLY = [
    "air_min_k", "air_max_k", "pressure_min_pa", "pressure_max_pa", "humidity_min_pct",
    "humidity_max_pct", "ground_min_k", "ground_max_k", "uv_min_wm2", "uv_max_wm2",
    "wind_gust_ms",
]  # fmt: skip


@pytest.fixture
def raw(weather_env: Callable[..., Path]) -> Path:
    return weather_env()


async def parse(*argv: str) -> int:
    return await run_weather("parse", "--rover", "perseverance", *argv)


def test_the_shared_names_of_stage_22_phase_b_ac22_ac24() -> None:
    from app.services import weather_sync, weather_transfer

    assert weather_sync.WEATHER_PARSER_REVISION == 5
    assert weather_transfer.EXPORT_VERSION == 2
    assert weather_transfer.SCHEMA_REVISION == "0020"
    for name in (
        "air_min", "air_max", "pressure_min", "pressure_max", "humidity_min", "humidity_max",
        "ground_min", "ground_max", "uv_min", "uv_max", "wind_gust",
    ):  # fmt: skip
        assert name in weather_sync.HourAggregate.__dataclass_fields__, name


# --- AC-22: a parser of revision 5 reads every sol again ----------------------------------------


async def erase_hourly_extremes(factory: Factory) -> None:
    """Make the hourly rows look as if revision 4 had written them (no extremes, no gust)."""
    from sqlalchemy import text

    async with factory() as session:
        await session.execute(
            text(f"UPDATE weather_hourly SET {', '.join(f'{c} = NULL' for c in NEW_HOURLY)}")
        )
        await session.commit()


SOLS = (10, 11, 12)  # sol 1 has no ground estimate (17G)
WIND_SOL = 11
DAILY_IGNORED = {"parser_revision", "updated_at"}


def stable(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in DAILY_IGNORED}


async def seed_revision_4(
    raw: Path, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse with the revision patched to 4, then erase the new hourly columns."""
    from app.services import weather_sync

    await seed_sols(raw)
    monkeypatch.setattr(weather_sync, "WEATHER_PARSER_REVISION", 4)
    assert await parse() == 0
    await erase_hourly_extremes(clean_db)
    monkeypatch.setattr(weather_sync, "WEATHER_PARSER_REVISION", 5)
    return await daily_rows(clean_db), await file_rows(clean_db)


async def seed_sols(raw: Path) -> None:
    from tests._weather import full_meda_sol

    for sol in SOLS:
        full_meda_sol(
            raw,
            sol,
            air=lambda h, s=sol: 200.0 + s + h / 100,
            with_ws=sol == WIND_SOL,
            ground=lambda h: 220.0,
        )


async def test_rows_of_revision_4_put_every_sol_in_the_plan_ac22(
    raw: Path, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily_before, files_before = await seed_revision_4(raw, clean_db, monkeypatch)
    assert {r["parser_revision"] for r in daily_before} == {4}

    assert await parse() == 0

    daily_after = await daily_rows(clean_db)
    assert [r["sol"] for r in daily_after] == list(SOLS)
    assert {r["parser_revision"] for r in daily_after} == {5}
    parsed_before = {r["path"]: r["parsed_at"] for r in files_before}
    assert all(r["parsed_at"] > parsed_before[r["path"]] for r in await file_rows(clean_db))


async def test_reparse_fills_the_hourly_extremes_and_keeps_the_means_ac22(
    raw: Path, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_revision_4(raw, clean_db, monkeypatch)
    old_means = {
        sol: [
            (h["air_k"], h["pressure_pa"], h["ground_k"]) for h in await hourly_rows(clean_db, sol)
        ]
        for sol in SOLS
    }
    assert all(h["air_min_k"] is None for h in await hourly_rows(clean_db, SOLS[0]))

    assert await parse() == 0

    for sol in SOLS:
        hours = await hourly_rows(clean_db, sol)
        assert [(h["air_k"], h["pressure_pa"], h["ground_k"]) for h in hours] == old_means[sol]
        for hour in hours:
            assert hour["air_min_k"] == pytest.approx(200.0 + sol + hour["hour"] / 100)
            assert hour["air_max_k"] == pytest.approx(200.0 + sol + hour["hour"] / 100)
            assert hour["pressure_min_pa"] == hour["pressure_max_pa"] == pytest.approx(740.0)
            assert hour["humidity_min_pct"] == hour["humidity_max_pct"] == pytest.approx(5.0)
            assert hour["ground_min_k"] == pytest.approx(220.0, abs=0.05)
    wind_hours = await hourly_rows(clean_db, WIND_SOL)
    assert all(3.0 <= h["wind_gust_ms"] <= 4.0 for h in wind_hours if h["wind_ms"])
    assert any(h["wind_gust_ms"] is not None for h in wind_hours)
    assert all(h["wind_gust_ms"] is None for h in await hourly_rows(clean_db, SOLS[0]))


async def test_reparse_does_not_change_the_daily_columns_ac22(
    raw: Path, clean_db: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    daily_before, _ = await seed_revision_4(raw, clean_db, monkeypatch)

    assert await parse() == 0

    daily_after = await daily_rows(clean_db)
    assert [stable(r) for r in daily_after] == [stable(r) for r in daily_before]


async def test_second_run_after_the_reparse_has_nothing_to_do_ac22(
    raw: Path,
    clean_db: Factory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await seed_revision_4(raw, clean_db, monkeypatch)
    capsys.readouterr()
    assert await parse() == 0
    assert "sols planned=3 " in capsys.readouterr().out  # all three sols, as revision 5 is new
    files = {r["path"]: r["parsed_at"] for r in await file_rows(clean_db)}
    state = await snapshot(clean_db)

    assert await parse() == 0

    assert "sols planned=0 " in capsys.readouterr().out
    assert {r["path"]: r["parsed_at"] for r in await file_rows(clean_db)} == files
    after = await snapshot(clean_db)
    for part in ("daily", "hourly"):
        assert after[part] == state[part]
    # the summaries did not change: neither the fingerprint of the answers nor the source max sol
    keep = ("rover_id", "changed_at", "updated_at", "source_max_sol")
    assert [{k: r[k] for k in keep} for r in after["dataset"]] == [
        {k: r[k] for k in keep} for r in state["dataset"]
    ]


# --- AC-24: the export of format 2 --------------------------------------------------------------


async def test_export_is_version_2_with_schema_0020_and_hourly_extremes_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw, (1, 2), (30,))
    target = tmp_path / "w.jsonl.gz"

    assert await export(target) == 0

    lines = read_export(target)
    assert lines[0]["version"] == 2
    assert lines[0]["schema_revision"] == "0020"
    assert lines[0]["counts"] == {
        "perseverance": {"daily": 2, "hourly": 48},
        "curiosity": {"daily": 1, "hourly": 24},
    }
    hourly = [line for line in lines if line["t"] == "hourly"]
    assert len(hourly) == 72
    for line in hourly:
        assert set(NEW_HOURLY) <= set(line), line["hour"]
    own = [h for h in hourly if h["rover_id"] == "perseverance" and h["sol"] == 1]
    assert all(
        h["air_min_k"] - 1e-9 <= h["air_k"] <= h["air_max_k"] + 1e-9 for h in own
    )  # one ulp of float noise
    rems = [h for h in hourly if h["rover_id"] == "curiosity"]
    assert all(h["uv_min_wm2"] is not None and h["uv_max_wm2"] is not None for h in rems)
    assert all(h["wind_gust_ms"] is None for h in rems)


async def test_import_into_an_empty_base_restores_the_hourly_extremes_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    await seed_by_parse(raw, (1, 2, 3), (9, 30))
    before = await snapshot(clean_db)
    assert any(h["air_min_k"] is not None for h in before["hourly"])
    target = tmp_path / "w.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)

    assert await import_(target) == 0

    after = await snapshot(clean_db)
    assert after["hourly"] == before["hourly"]
    assert after["daily"] == before["daily"]
    assert after["dataset"] == before["dataset"]


async def api_body(client: httpx.AsyncClient, rover: str, sol: int) -> dict[str, Any]:
    response = await client.get(f"{V2}/rovers/{rover}/weather/{sol}")
    assert response.status_code == 200
    body = response.json()
    body.pop("source")  # `weather_files` is not transferred: the file list of a sol differs
    return body


async def test_import_gives_the_same_api_answers_as_the_original_base_ac24(
    raw: Path,
    clean_db: Factory,
    tmp_path: Path,
    client: httpx.AsyncClient,
) -> None:
    copy_fixture_sols(raw, meda=[100, 400], rems=[30])
    assert await run_weather("parse", "--rover", "perseverance") == 0
    assert await run_weather("parse", "--rover", "curiosity") == 0
    wanted = [("perseverance", 100), ("perseverance", 400), ("curiosity", 30)]
    originals = {key: await api_body(client, *key) for key in wanted}
    lists = {
        rover: (await client.get(f"{V2}/rovers/{rover}/weather")).content
        for rover in ("perseverance", "curiosity")
    }
    target = tmp_path / "w.jsonl.gz"
    assert await export(target) == 0
    await wipe_weather(clean_db)

    assert await import_(target) == 0

    for key in wanted:
        assert await api_body(client, *key) == originals[key], key
    assert originals[("perseverance", 100)]["hours"][12]["air_temp_c_min"] is not None
    for rover in ("perseverance", "curiosity"):
        assert (await client.get(f"{V2}/rovers/{rover}/weather")).content == lists[rover]


# --- AC-24: older exports are refused -------------------------------------------------------


async def exported_lines(raw: Path, clean_db: Factory, tmp_path: Path) -> list[dict[str, Any]]:
    await seed_by_parse(raw, (1, 2), ())
    target = tmp_path / "good.jsonl.gz"
    assert await export(target) == 0
    return read_export(target)


@pytest.mark.parametrize("revision", ["0017", "0018", "0019"], ids=lambda r: f"schema-{r}")
async def test_import_of_an_older_schema_revision_is_refused_and_changes_nothing_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path, revision: str
) -> None:
    """Exit code 2, as for every other invalid file of stage 17."""
    lines = await exported_lines(raw, clean_db, tmp_path)
    before = await snapshot(clean_db)
    lines[0]["schema_revision"] = revision
    bad = write_export(tmp_path / "old.jsonl.gz", lines)

    assert await import_(bad) == 2

    assert await snapshot(clean_db) == before


async def test_import_of_a_version_1_export_is_refused_and_changes_nothing_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    """A real old export: version 1, revision 0018, hourly lines without the new columns."""
    lines = await exported_lines(raw, clean_db, tmp_path)
    before = await snapshot(clean_db)
    lines[0]["version"] = 1
    lines[0]["schema_revision"] = "0018"
    for line in lines:
        if line["t"] == "hourly":
            for column in NEW_HOURLY:
                del line[column]
    old = write_export(tmp_path / "v1.jsonl.gz", lines)

    assert await import_(old) == 2

    assert await snapshot(clean_db) == before


async def test_import_of_an_hourly_line_without_the_new_columns_is_refused_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path
) -> None:
    lines = await exported_lines(raw, clean_db, tmp_path)
    before = await snapshot(clean_db)
    hourly = next(line for line in lines if line["t"] == "hourly")
    del hourly["wind_gust_ms"]
    bad = write_export(tmp_path / "missing.jsonl.gz", lines)

    assert await import_(bad) == 2

    assert await snapshot(clean_db) == before


@pytest.mark.parametrize(
    ("low", "high"),
    [("air_min_k", "air_max_k"), ("uv_min_wm2", "uv_max_wm2")],
    ids=["air", "uv"],
)
async def test_import_of_a_min_without_its_max_is_refused_and_changes_nothing_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path, low: str, high: str
) -> None:
    lines = await exported_lines(raw, clean_db, tmp_path)
    before = await snapshot(clean_db)
    hourly = next(line for line in lines if line["t"] == "hourly")
    hourly[low], hourly[high] = 200.0, None
    bad = write_export(tmp_path / "pair.jsonl.gz", lines)

    assert await import_(bad) == 2

    assert await snapshot(clean_db) == before


TRIPLES = [
    ("air_k", "air_min_k", "air_max_k"),
    ("pressure_pa", "pressure_min_pa", "pressure_max_pa"),
    ("humidity_pct", "humidity_min_pct", "humidity_max_pct"),
]


@pytest.mark.parametrize(("mean", "low", "high"), TRIPLES, ids=[t[0] for t in TRIPLES])
async def test_import_of_an_hour_with_min_above_max_is_refused_and_changes_nothing_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path, mean: str, low: str, high: str
) -> None:
    lines = await exported_lines(raw, clean_db, tmp_path)
    before = await snapshot(clean_db)
    hourly = next(line for line in lines if line["t"] == "hourly")
    assert hourly[low] is not None
    hourly[low], hourly[high] = hourly[high] + 1.0, hourly[low]
    bad = write_export(tmp_path / "min-above-max.jsonl.gz", lines)

    assert await import_(bad) == 2

    assert await snapshot(clean_db) == before


@pytest.mark.parametrize("broken", ["mean-null", "extremes-null"])
@pytest.mark.parametrize(("mean", "low", "high"), TRIPLES, ids=[t[0] for t in TRIPLES])
async def test_import_of_an_hour_with_a_broken_null_triple_is_refused_ac24(
    raw: Path, clean_db: Factory, tmp_path: Path, mean: str, low: str, high: str, broken: str
) -> None:
    """The mean is null exactly when the min and the max are null."""
    lines = await exported_lines(raw, clean_db, tmp_path)
    before = await snapshot(clean_db)
    hourly = next(line for line in lines if line["t"] == "hourly")
    assert hourly[mean] is not None
    if broken == "mean-null":
        hourly[mean] = None
    else:
        hourly[low] = hourly[high] = None
    bad = write_export(tmp_path / "triple.jsonl.gz", lines)

    assert await import_(bad) == 2

    assert await snapshot(clean_db) == before
