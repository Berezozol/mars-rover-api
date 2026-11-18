"""CLI ``python -m app.cli import-mer`` (stage 8) on the test database, offline.

Contract (docs/proposals/stage-8-mer-import.md, sections 2 and 4):
``import-mer --rover spirit|opportunity [--from-sol] [--to-sol] [--batch-sols] [--no-resume]``
= ``BackfillService.run(MerAdapter)``: same batches, resume, sync_runs (mode ``backfill``),
exit codes as ``backfill``, plus 2 with a "run mer-download" hint when the cache is missing.
Fills photos, sol_summaries, camera_sol_counts, sol_source_counts and the rover counters;
a second import changes nothing; ``reconcile --offline`` is clean afterwards; MI is created
with the catalog name and sort_order 7. The cache is tests/fixtures/upstream/mer (real
index rows); no request may leave the process (respx guard in conftest).
"""

import asyncio
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import Settings, get_settings
from tests.integration._fakes import fetch_all
from tests.unit._mer import (
    FRAMES_BY_CAMERA,
    FRAMES_TOTAL,
    FULL_FRAMES,
    MAX_FULL_SOL,
    MAX_PHOTO_SOL,
    MER_FIXTURES,
    ROWS_TOTAL,
    copy_cache,
    received_by_sol,
    reference_frames,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]
ROVERS = ["spirit", "opportunity"]
BIG_BATCH = "100000"


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    for key, value in {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "MER_CACHE_DIR": str(MER_FIXTURES),
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
        "BACKFILL_LOCK_RETRY_SECONDS": "0.05",
        "BACKFILL_LOCK_MAX_WAIT_SECONDS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BACKFILL_BATCH_SOLS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def run_cli(argv: list[str]) -> int:
    try:
        return await asyncio.to_thread(cli.main, argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


async def import_mer(rover: str, *extra: str) -> int:
    return await run_cli(["import-mer", "--rover", rover, *extra])


async def photos(factory: SessionFactory, rover: str) -> list[dict[str, Any]]:
    return await fetch_all(
        factory,
        "SELECT p.id, p.source_id, p.sol, c.code AS camera, p.sample_type, p.earth_date, "
        "p.taken_at, p.received_at, p.img_full, p.img_small, p.img_medium, p.img_large, "
        "p.title, p.caption, p.credit, p.source_link, p.site, p.drive, p.extra, p.updated_at "
        "FROM photos p JOIN cameras c ON c.id = p.camera_id "
        f"WHERE p.rover_id = '{rover}' ORDER BY p.id",
    )


async def runs(factory: SessionFactory, rover: str) -> list[dict[str, Any]]:
    return await fetch_all(
        factory,
        "SELECT mode, from_sol, to_sol, status, photos_upserted, failed_sols FROM sync_runs "
        f"WHERE rover_id = '{rover}' ORDER BY id",
    )


@pytest.mark.parametrize("rover", ROVERS)
async def test_import_mer_loads_every_frame(clean_db: SessionFactory, rover: str) -> None:
    assert await import_mer(rover, "--batch-sols", BIG_BATCH) == 0

    rows = await photos(clean_db, rover)
    expected = {frame.source_id: frame for frame in reference_frames(rover)}
    assert len(rows) == FRAMES_TOTAL[rover]
    assert {row["source_id"] for row in rows} == set(expected)
    assert Counter(row["camera"] for row in rows) == FRAMES_BY_CAMERA[rover]
    for row in rows:
        frame = expected[row["source_id"]]
        assert row["sol"] == frame.sol
        assert row["camera"] == frame.camera_code
        assert row["sample_type"] == frame.sample_type
        assert row["taken_at"] == frame.taken_at
        assert row["earth_date"] == frame.taken_at.date()
        assert row["img_full"] == frame.img_full
        assert row["source_link"] == frame.source_link
        assert row["credit"] == frame.credit
        assert (row["img_small"], row["img_medium"], row["img_large"]) == (None, None, None)
        assert (row["title"], row["caption"]) == (None, None)
        assert (row["site"], row["drive"]) == (frame.site, frame.drive)
        assert set(row["extra"]["products"]) == frame.products


@pytest.mark.parametrize("rover", ROVERS)
async def test_import_mer_updates_rover_counters(clean_db: SessionFactory, rover: str) -> None:
    assert await import_mer(rover, "--batch-sols", BIG_BATCH) == 0

    [stats] = await fetch_all(
        clean_db,
        "SELECT status, max_sol, total_photos, full_max_sol, full_total_photos, last_synced_at "
        f"FROM rovers WHERE id = '{rover}'",
    )
    assert stats["status"] == "complete"
    assert stats["max_sol"] == MAX_PHOTO_SOL[rover]
    assert stats["total_photos"] == FRAMES_TOTAL[rover]
    assert stats["full_max_sol"] == MAX_FULL_SOL[rover]
    assert stats["full_total_photos"] == FULL_FRAMES[rover]
    assert stats["last_synced_at"] is not None


@pytest.mark.parametrize("rover", ROVERS)
async def test_import_mer_fills_sol_summaries_and_camera_counts(
    clean_db: SessionFactory, rover: str
) -> None:
    assert await import_mer(rover, "--batch-sols", BIG_BATCH) == 0

    frames = reference_frames(rover)
    total: Counter[int] = Counter(f.sol for f in frames)
    full: Counter[int] = Counter(f.sol for f in frames if f.sample_type == "full")
    cameras: dict[int, set[str]] = defaultdict(set)
    per_camera: Counter[tuple[int, str]] = Counter((f.sol, f.camera_code) for f in frames)
    for f in frames:
        cameras[f.sol].add(f.camera_code)

    summaries = await fetch_all(
        clean_db,
        "SELECT sol, earth_date, total_photos, full_photos, cameras FROM sol_summaries "
        f"WHERE rover_id = '{rover}' ORDER BY sol",
    )
    assert [s["sol"] for s in summaries] == sorted(total)
    for summary in summaries:
        assert summary["total_photos"] == total[summary["sol"]]
        assert summary["full_photos"] == full[summary["sol"]]
        assert set(summary["cameras"]) == cameras[summary["sol"]]

    counts = await fetch_all(
        clean_db,
        "SELECT s.sol, c.code, s.total_photos FROM camera_sol_counts s "
        f"JOIN cameras c ON c.id = s.camera_id WHERE s.rover_id = '{rover}'",
    )
    assert {(r["sol"], r["code"]): r["total_photos"] for r in counts} == dict(per_camera)


@pytest.mark.parametrize("rover", ROVERS)
async def test_import_mer_records_source_counts_per_sol(
    clean_db: SessionFactory, rover: str
) -> None:
    assert await import_mer(rover, "--batch-sols", BIG_BATCH) == 0

    rows = await fetch_all(
        clean_db,
        f"SELECT sol, received, dropped FROM sol_source_counts WHERE rover_id = '{rover}'",
    )
    stored: Counter[int] = Counter(f.sol for f in reference_frames(rover))
    received = received_by_sol(rover)
    by_sol = {r["sol"]: (r["received"], r["dropped"]) for r in rows}
    for sol, count in received.items():
        assert by_sol[sol] == (count, count - stored[sol]), sol
    assert sum(r["received"] for r in rows) == ROWS_TOTAL[rover]


async def test_import_mer_writes_backfill_sync_runs(clean_db: SessionFactory) -> None:
    # Spirit: index sols 1..2116 -> batches [2000,2116], [1000,1999], [0,999].
    assert await import_mer("spirit", "--batch-sols", "1000") == 0

    assert await runs(clean_db, "spirit") == [
        {"mode": "backfill", "from_sol": 2000, "to_sol": 2116, "status": "success",
         "photos_upserted": 0, "failed_sols": []},
        {"mode": "backfill", "from_sol": 1000, "to_sol": 1999, "status": "success",
         "photos_upserted": 3, "failed_sols": []},
        {"mode": "backfill", "from_sol": 0, "to_sol": 999, "status": "success",
         "photos_upserted": FRAMES_TOTAL["spirit"] - 3, "failed_sols": []},
    ]  # fmt: skip


async def test_import_mer_creates_mi_camera_with_catalog_name(clean_db: SessionFactory) -> None:
    assert await import_mer("opportunity", "--batch-sols", BIG_BATCH) == 0

    cameras = await fetch_all(
        clean_db,
        "SELECT code, name, sort_order FROM cameras WHERE rover_id = 'opportunity' "
        "ORDER BY sort_order",
    )
    assert [c["code"] for c in cameras] == [
        "FHAZ",
        "RHAZ",
        "NAVCAM",
        "PANCAM",
        "MINITES",
        "ENTRY",
        "MI",
    ]
    assert cameras[-1] == {"code": "MI", "name": "Microscopic Imager", "sort_order": 7}


@pytest.mark.parametrize("resume", [[], ["--no-resume"]], ids=["resume", "no-resume"])
async def test_import_mer_second_run_changes_nothing(
    clean_db: SessionFactory, resume: list[str]
) -> None:
    assert await import_mer("opportunity", "--batch-sols", BIG_BATCH) == 0
    before = await photos(clean_db, "opportunity")
    summaries = await fetch_all(clean_db, "SELECT * FROM sol_summaries ORDER BY rover_id, sol")

    assert await import_mer("opportunity", "--batch-sols", BIG_BATCH, *resume) == 0

    assert await photos(clean_db, "opportunity") == before
    assert await fetch_all(clean_db, "SELECT * FROM sol_summaries ORDER BY rover_id, sol") == (
        summaries
    )


async def test_import_mer_interrupted_import_resumes(clean_db: SessionFactory) -> None:
    """A first run that stopped after the low batches; the full run skips them."""
    assert await import_mer("spirit", "--batch-sols", "500", "--to-sol", "999") == 0
    first = len(await photos(clean_db, "spirit"))
    assert 0 < first < FRAMES_TOTAL["spirit"]

    assert await import_mer("spirit", "--batch-sols", "500") == 0

    assert len(await photos(clean_db, "spirit")) == FRAMES_TOTAL["spirit"]
    ranges = [(r["from_sol"], r["to_sol"]) for r in await runs(clean_db, "spirit")]
    assert ranges == [(500, 999), (0, 499), (2000, 2116), (1500, 1999), (1000, 1499)]


async def test_import_mer_sol_range_imports_only_that_range(clean_db: SessionFactory) -> None:
    assert await import_mer("spirit", "--from-sol", "400", "--to-sol", "500") == 0

    sols = {row["sol"] for row in await photos(clean_db, "spirit")}
    assert sols == {432, 434, 444, 483, 493}


async def test_import_mer_missing_cache_exits_2_with_hint(
    clean_db: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MER_CACHE_DIR", str(tmp_path / "nothing-here"))
    get_settings.cache_clear()

    assert await import_mer("spirit") == 2

    assert "mer-download" in capsys.readouterr().err
    assert await runs(clean_db, "spirit") == []
    assert await photos(clean_db, "spirit") == []


async def test_import_mer_one_volume_missing_exits_2(
    clean_db: SessionFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = copy_cache(tmp_path, ("spirit",))
    (cache / "mer2do_0xxx" / "index" / "edrindex.lbl").unlink()
    monkeypatch.setenv("MER_CACHE_DIR", str(cache))
    get_settings.cache_clear()

    assert await import_mer("spirit") == 2
    assert await photos(clean_db, "spirit") == []


@pytest.mark.parametrize("rover", ["curiosity", "perseverance", "pluto"])
async def test_import_mer_non_mer_rover_exits_2(clean_db: SessionFactory, rover: str) -> None:
    assert await import_mer(rover) == 2
    assert await fetch_all(clean_db, "SELECT id FROM sync_runs") == []


@pytest.mark.parametrize("rover", ROVERS)
async def test_reconcile_offline_after_import_is_clean(
    clean_db: SessionFactory, rover: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await import_mer(rover, "--batch-sols", BIG_BATCH) == 0
    capsys.readouterr()

    assert await run_cli(["reconcile", "--rover", rover, "--offline"]) == 0


async def test_import_spirit_does_not_touch_opportunity(clean_db: SessionFactory) -> None:
    assert await import_mer("spirit", "--batch-sols", BIG_BATCH) == 0

    assert await photos(clean_db, "opportunity") == []
    [opp] = await fetch_all(clean_db, "SELECT total_photos FROM rovers WHERE id = 'opportunity'")
    assert opp["total_photos"] == 0
