"""CLI ``python -m app.cli reconcile`` (stage 7, service app/services/reconcile.py).

Contract (stage-7 brief):
``reconcile --rover X [--offline] [--legacy-manifest PATH] [--output PATH]``
The report compares our photo total, sum(sol_summaries) and the source total (online: at
most 2 requests; offline: none) and lists
- sols where received - dropped != stored photos;
- sols where the source declared a per-sol total and it differs from received;
- sols in [0, max_sol] without data, split into "failed in sync_runs" and "source has
  nothing";
- dropped totals;
- legacy manifest: sol_summaries.full_photos vs the old ``total_photos`` - ours >= old is
  expected, ours < old is unexplained.
JSON goes to --output. Exit 0 if there are no unexplained differences, otherwise 1.

The JSON key names are not part of the brief, so the shape checks are structural: a JSON
object, and each sol of a discrepancy is found under a key path naming its kind.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.core.config import Settings, get_settings
from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.integration._fakes import sol_records
from tests.unit._upstream import M2020_URL, MSL_URL, load_fixture

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]
ROVER = "curiosity"
MSL_SOURCE_TOTAL = 1519997  # `total` of tests/fixtures/upstream/msl/latest.json

# Clean dataset: sol -> (full photos, subframes, received, dropped, source_total).
CLEAN = {
    0: (2, 0, 2, 0, 2),
    1: (3, 1, 5, 1, 5),  # 4 stored = 5 received - 1 dropped
    2: (1, 0, 1, 0, None),  # the source declared no per-sol total
}
OUR_TOTAL = 2 + 4 + 1
DROPPED_TOTAL = 1


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[None]:
    for key, value in {
        "DATABASE_URL": settings.database_url,
        "SYNC_ENABLED": "false",
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
        "M2020_FEED_URL": M2020_URL,
        "MSL_FEED_URL": MSL_URL,
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- data --------------------------------------------------------------------------------


async def store_sol(
    factory: SessionFactory, sol: int, full: int, subframes: int, *, rover: str = ROVER
) -> None:
    camera = "FHAZ" if rover == ROVER else "NAVCAM_LEFT"
    records = sol_records(rover, sol, full, camera_code=camera)
    if subframes:
        records += sol_records(rover, sol, subframes, camera_code=camera, sample_type="subframe")
    async with factory() as session:
        await upsert_photos(session, rover, records)
        await recompute_sol_summaries(session, rover, [sol])
        await refresh_rover_stats(session, rover)
        await session.commit()


async def set_source_counts(
    factory: SessionFactory,
    sol: int,
    received: int,
    dropped: int,
    source_total: int | None,
    *,
    rover: str = ROVER,
) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO sol_source_counts "
                "(rover_id, sol, received, dropped, source_total, fetched_at) "
                "VALUES (:rover, :sol, :received, :dropped, :source_total, now()) "
                "ON CONFLICT (rover_id, sol) DO UPDATE SET received = EXCLUDED.received, "
                "dropped = EXCLUDED.dropped, source_total = EXCLUDED.source_total"
            ),
            {
                "rover": rover,
                "sol": sol,
                "received": received,
                "dropped": dropped,
                "source_total": source_total,
            },
        )
        await session.commit()


async def add_run(
    factory: SessionFactory,
    from_sol: int,
    to_sol: int,
    status: str,
    failed: list[int],
    *,
    rover: str = ROVER,
) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO sync_runs "
                "(rover_id, mode, from_sol, to_sol, status, finished_at, failed_sols) "
                "VALUES (:rover, 'backfill', :from_sol, :to_sol, :status, now(), :failed)"
            ),
            {
                "rover": rover,
                "from_sol": from_sol,
                "to_sol": to_sol,
                "status": status,
                "failed": failed,
            },
        )
        await session.commit()


async def execute(factory: SessionFactory, sql: str) -> None:
    async with factory() as session:
        await session.execute(text(sql))
        await session.commit()


@pytest.fixture
async def clean_data(session_factory: SessionFactory) -> SessionFactory:
    for sol, (full, subframes, received, dropped, source_total) in CLEAN.items():
        await store_sol(session_factory, sol, full, subframes)
        await set_source_counts(session_factory, sol, received, dropped, source_total)
    await add_run(session_factory, 0, 2, "success", [])
    return session_factory


# --- running the CLI and reading the report ---------------------------------------------


async def reconcile(
    tmp_path: Path, *extra: str, offline: bool = True, rover: str = ROVER
) -> tuple[int, Any]:
    output = tmp_path / "report.json"
    argv = ["reconcile", "--rover", rover, "--output", str(output), *extra]
    if offline:
        argv.append("--offline")
    try:
        code = await asyncio.to_thread(cli.main, argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    assert output.exists(), "reconcile must write the JSON report to --output"
    return code, json.loads(output.read_text(encoding="utf-8"))


def _walk(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """(key path without list indices, node) for every node of the report."""
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, (*path, str(key)))
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, path)


def sol_paths(report: Any, sol: int) -> set[str]:
    """Key paths (lower case, '/'-joined) under which ``sol`` is listed.

    A sol is listed as an int item of a list, as an object with ``"sol": sol``, or as an
    object key ``"<sol>"``.
    """
    found = set()
    for path, node in _walk(report):
        joined = "/".join(path).lower()
        if isinstance(node, list) and any(type(item) is int and item == sol for item in node):
            found.add(joined)
        elif isinstance(node, dict):
            if node.get("sol") == sol:
                found.add(joined)
            if str(sol) in node:
                found.add(joined)
    return found


def numbers_under(report: Any, word: str) -> set[int]:
    """Integer leaves whose key path mentions ``word``."""
    return {
        node for path, node in _walk(report) if type(node) is int and word in "/".join(path).lower()
    }


def all_numbers(report: Any) -> set[int]:
    return {node for _, node in _walk(report) if type(node) is int}


def mentions(paths: set[str], *words: str) -> bool:
    return any(word in path for path in paths for word in words)


# --- clean data ---------------------------------------------------------------------------


async def test_reconcile_clean_data_exits_0_and_writes_json_object(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    code, report = await reconcile(tmp_path)

    assert code == 0
    assert isinstance(report, dict) and report
    assert ROVER in json.dumps(report)
    assert OUR_TOTAL in all_numbers(report)  # our photos total == sum(sol_summaries)
    assert DROPPED_TOTAL in numbers_under(report, "dropped")


async def test_reconcile_offline_makes_no_requests(
    clean_data: SessionFactory, tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    msl = upstream_router.get(MSL_URL).mock(return_value=httpx.Response(500))
    m2020 = upstream_router.get(M2020_URL).mock(return_value=httpx.Response(500))

    code, report = await reconcile(tmp_path)

    assert code == 0
    assert (msl.call_count, m2020.call_count) == (0, 0)
    assert MSL_SOURCE_TOTAL not in all_numbers(report)


async def test_reconcile_online_makes_at_most_two_requests_and_reports_source_total(
    clean_data: SessionFactory, tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    msl = upstream_router.get(MSL_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("msl/latest.json"))
    )
    m2020 = upstream_router.get(M2020_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("m2020/latest.json"))
    )

    _, report = await reconcile(tmp_path, offline=False)

    assert 1 <= msl.call_count + m2020.call_count <= 2
    assert m2020.call_count == 0  # Curiosity only asks its own feed
    assert MSL_SOURCE_TOTAL in numbers_under(report, "source")


# --- each discrepancy kind ----------------------------------------------------------------


async def test_reconcile_stored_differs_from_received_minus_dropped_exits_1(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await set_source_counts(clean_data, 1, 7, 1, 7)  # 7 - 1 = 6, but 4 photos stored

    code, report = await reconcile(tmp_path)

    assert code == 1
    paths = sol_paths(report, 1)
    assert mentions(paths, "stored", "mismatch", "received"), paths
    assert not mentions(sol_paths(report, 0), "stored", "mismatch")


async def test_reconcile_source_total_differs_from_received_exits_1(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await set_source_counts(clean_data, 0, 2, 0, 9)  # the source declared 9, we received 2

    code, report = await reconcile(tmp_path)

    assert code == 1
    paths = sol_paths(report, 0)
    assert mentions(paths, "source"), paths


async def test_reconcile_photos_total_differs_from_sol_summaries_exits_1(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await execute(
        clean_data,
        f"UPDATE sol_summaries SET total_photos = total_photos + 5 "
        f"WHERE rover_id = '{ROVER}' AND sol = 2",
    )

    code, report = await reconcile(tmp_path)

    assert code == 1
    numbers = all_numbers(report)
    assert OUR_TOTAL in numbers
    assert OUR_TOTAL + 5 in numbers


async def test_reconcile_no_data_sols_split_into_failed_and_source_has_nothing(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    # Sols 3 and 4 have no photos; 5 does. Sol 3 failed in a sync run, sol 4 did not.
    await store_sol(clean_data, 5, 1, 0)
    await set_source_counts(clean_data, 5, 1, 0, 1)
    await set_source_counts(clean_data, 4, 0, 0, None)  # fetched, the source had nothing
    await add_run(clean_data, 3, 5, "partial", [3])

    _, report = await reconcile(tmp_path)

    failed = sol_paths(report, 3)
    nothing = sol_paths(report, 4)
    assert mentions(failed, "fail"), failed
    assert nothing, "a no-data sol inside [0, max_sol] must be listed"
    assert not mentions(nothing, "fail"), nothing
    assert failed != nothing


async def test_reconcile_no_data_sols_fetched_empty_are_explained_exit_0(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await store_sol(clean_data, 5, 1, 0)
    await set_source_counts(clean_data, 5, 1, 0, 1)
    for sol in (3, 4):  # fetched by the sync, nothing received
        await set_source_counts(clean_data, sol, 0, 0, None)
    await add_run(clean_data, 3, 5, "success", [])

    code, report = await reconcile(tmp_path)

    assert code == 0
    assert sol_paths(report, 3) and sol_paths(report, 4)
    assert not mentions(sol_paths(report, 3) | sol_paths(report, 4), "fail")


async def test_reconcile_no_data_sols_covered_by_successful_run_are_explained_exit_0(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await store_sol(clean_data, 5, 1, 0)
    await set_source_counts(clean_data, 5, 1, 0, 1)
    await add_run(clean_data, 3, 5, "success", [])

    code, report = await reconcile(tmp_path)

    assert code == 0
    assert sol_paths(report, 3) and sol_paths(report, 4)
    assert not mentions(sol_paths(report, 3) | sol_paths(report, 4), "fail")


async def test_reconcile_no_data_sols_never_fetched_are_unexplained_exit_1(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    # Sols 3 and 4: no photos, no sol_source_counts row, no run covering them.
    await store_sol(clean_data, 5, 1, 0)
    await set_source_counts(clean_data, 5, 1, 0, 1)
    await add_run(clean_data, 5, 5, "success", [])

    code, report = await reconcile(tmp_path)

    assert code == 1
    paths = sol_paths(report, 3)
    assert mentions(paths, "never", "unfetched", "not_fetched", "unknown"), paths
    assert not mentions(paths, "fail")


async def test_reconcile_failed_sol_later_loaded_is_not_reported_as_failed(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await add_run(clean_data, 0, 2, "partial", [1])  # sol 1 failed once, it has photos now

    code, report = await reconcile(tmp_path)

    assert code == 0
    assert not mentions(sol_paths(report, 1), "fail")


async def test_reconcile_reports_dropped_total(clean_data: SessionFactory, tmp_path: Path) -> None:
    await store_sol(clean_data, 3, 2, 0)
    await set_source_counts(clean_data, 3, 5, 3, 5)

    code, report = await reconcile(tmp_path)

    assert code == 0
    assert DROPPED_TOTAL + 3 in numbers_under(report, "dropped")


# --- legacy manifest ----------------------------------------------------------------------


def write_manifest(tmp_path: Path, totals: dict[int, int], name: str = "Curiosity") -> Path:
    """A v1 photo_manifest (docs/LEGACY-API.md) with the given sol -> total_photos."""
    manifest = {
        "photo_manifest": {
            "name": name,
            "landing_date": "2012-08-06",
            "launch_date": "2011-11-26",
            "status": "active",
            "max_sol": max(totals),
            "max_date": "2012-08-06",
            "total_photos": sum(totals.values()),
            "photos": [
                {
                    "sol": sol,
                    "earth_date": "2012-08-06",
                    "total_photos": total,
                    "cameras": ["FHAZ"],
                }
                for sol, total in sorted(totals.items())
            ],
        }
    }
    path = tmp_path / "legacy_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


async def test_reconcile_legacy_ours_greater_or_equal_is_expected_exit_0(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    # Our full_photos: sol 0 -> 2, sol 1 -> 3 (the subframe does not count), sol 2 -> 1.
    manifest = write_manifest(tmp_path, {0: 2, 1: 3, 2: 0})

    code, report = await reconcile(tmp_path, "--legacy-manifest", str(manifest))

    assert code == 0
    assert not mentions(sol_paths(report, 1), "unexplained")


# Curiosity before sol 4103: the old API stored all frame types (docs/LEGACY-API.md, last row
# of the deviations table), so the comparison with the old manifest starts at sol 4103.
LEGACY_FIRST_SOL = 4103


@pytest.mark.parametrize(
    ("totals", "sol"),
    [
        pytest.param({0: 2, 1: 4, 2: 1}, 1, id="full-photos-below-old-total"),
        pytest.param({0: 2, 1: 3, 2: 1, 9: 4}, 9, id="sol-missing-here"),
    ],
)
async def test_reconcile_legacy_curiosity_early_sol_below_old_is_ignored_exit_0(
    clean_data: SessionFactory, tmp_path: Path, totals: dict[int, int], sol: int
) -> None:
    manifest = write_manifest(tmp_path, totals)

    code, report = await reconcile(tmp_path, "--legacy-manifest", str(manifest))

    assert code == 0
    assert not mentions(sol_paths(report, sol), "unexplained")


@pytest.fixture
async def curiosity_late(session_factory: SessionFactory) -> SessionFactory:
    """Curiosity: sol 20 (6 full + 518 subframes, as in stage 7) and sol 4200 (2 full)."""
    for sol, full, subframes in ((20, 6, 518), (4200, 2, 0)):
        await store_sol(session_factory, sol, full, subframes)
        total = full + subframes
        await set_source_counts(session_factory, sol, total, 0, total)
    await add_run(session_factory, 0, 4200, "success", [])
    return session_factory


async def test_reconcile_legacy_curiosity_sol_20_mismatch_ignored_sol_4200_unexplained(
    curiosity_late: SessionFactory, tmp_path: Path
) -> None:
    manifest = write_manifest(tmp_path, {20: 1046, 4200: 5})

    code, report = await reconcile(tmp_path, "--legacy-manifest", str(manifest))

    assert code == 1
    assert not mentions(sol_paths(report, 20), "unexplained"), sol_paths(report, 20)
    paths = sol_paths(report, 4200)
    assert mentions(paths, "legacy", "manifest", "unexplained"), paths


async def test_reconcile_legacy_curiosity_only_early_mismatch_exits_0(
    curiosity_late: SessionFactory, tmp_path: Path
) -> None:
    manifest = write_manifest(tmp_path, {20: 1046, 4102: 7, 4200: 2})

    code, report = await reconcile(tmp_path, "--legacy-manifest", str(manifest))

    assert code == 0
    for sol in (20, 4102):
        assert not mentions(sol_paths(report, sol), "unexplained"), sol


async def test_reconcile_legacy_curiosity_boundary_sol_4103_is_compared_exit_1(
    curiosity_late: SessionFactory, tmp_path: Path
) -> None:
    # Sol 4103 has no photos here but 3 in the old manifest.
    manifest = write_manifest(tmp_path, {20: 6, LEGACY_FIRST_SOL: 3, 4200: 2})

    code, report = await reconcile(tmp_path, "--legacy-manifest", str(manifest))

    assert code == 1
    assert mentions(sol_paths(report, LEGACY_FIRST_SOL), "unexplained")


@pytest.mark.parametrize(
    ("totals", "sol"),
    [
        pytest.param({0: 2, 1: 4}, 1, id="full-photos-below-old-total"),
        pytest.param({0: 2, 1: 3, 9: 4}, 9, id="sol-missing-here"),
    ],
)
async def test_reconcile_legacy_other_rover_early_sol_below_old_is_unexplained_exit_1(
    session_factory: SessionFactory, tmp_path: Path, totals: dict[int, int], sol: int
) -> None:
    rover = "perseverance"
    for s, full in ((0, 2), (1, 3)):
        await store_sol(session_factory, s, full, 0, rover=rover)
        await set_source_counts(session_factory, s, full, 0, full, rover=rover)
    await add_run(session_factory, 0, 9, "success", [], rover=rover)
    manifest = write_manifest(tmp_path, totals, name="Perseverance")

    code, report = await reconcile(tmp_path, "--legacy-manifest", str(manifest), rover=rover)

    assert code == 1
    paths = sol_paths(report, sol)
    assert mentions(paths, "legacy", "manifest", "unexplained"), paths
    assert not mentions(sol_paths(report, 0), "unexplained")


# --- review round: old failures, per-run source totals, camera counters --------------------


async def add_run_with_total(
    factory: SessionFactory, mode: str, from_sol: int, to_sol: int, source_total: int
) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO sync_runs (rover_id, mode, from_sol, to_sol, status, "
                "finished_at, failed_sols, source_total) VALUES (:rover, :mode, :from_sol, "
                ":to_sol, 'success', now(), '{}', :source_total)"
            ),
            {
                "rover": ROVER,
                "mode": mode,
                "from_sol": from_sol,
                "to_sol": to_sol,
                "source_total": source_total,
            },
        )
        await session.commit()


async def test_reconcile_old_failure_fixed_by_later_successful_run_is_explained_exit_0(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    # Sol 3 failed once, then a successful run covered it and found nothing; no count row
    # (e.g. synced before stage 7).
    await store_sol(clean_data, 5, 1, 0)
    await set_source_counts(clean_data, 5, 1, 0, 1)
    await add_run(clean_data, 3, 5, "partial", [3])
    await add_run(clean_data, 3, 5, "success", [])

    code, report = await reconcile(tmp_path)

    assert code == 0
    paths = sol_paths(report, 3)
    assert not mentions(paths, "fail", "unknown", "never", "unexplained"), paths


async def test_reconcile_finished_run_source_total_matching_received_exits_0(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    # Sols 0..1 received 2 + 5 = 7; sol 2 stays the live edge.
    await add_run_with_total(clean_data, "backfill", 0, 1, 7)

    code, _ = await reconcile(tmp_path)

    assert code == 0


async def test_reconcile_finished_run_source_total_differs_from_received_exits_1(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await add_run_with_total(clean_data, "backfill", 0, 1, 99)  # received 7

    code, report = await reconcile(tmp_path)

    assert code == 1
    assert 99 in all_numbers(report)


async def test_reconcile_live_edge_run_source_total_is_informational_exit_0(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    # An incremental run ending at max_sol: photos still arrive there, so no verdict.
    await add_run_with_total(clean_data, "incremental", 1, 2, 99)  # received 6

    code, report = await reconcile(tmp_path)

    assert code == 0
    assert 99 in all_numbers(report)


async def test_reconcile_camera_counts_differ_from_photos_total_exits_1(
    clean_data: SessionFactory, tmp_path: Path
) -> None:
    await execute(
        clean_data,
        f"UPDATE camera_sol_counts SET total_photos = total_photos + 3 "
        f"WHERE rover_id = '{ROVER}' AND sol = 0",
    )

    code, report = await reconcile(tmp_path)

    assert code == 1
    numbers = all_numbers(report)
    assert OUR_TOTAL in numbers
    assert OUR_TOTAL + 3 in numbers


# --- usage errors --------------------------------------------------------------------------


@pytest.mark.parametrize("offline", [False, True], ids=["online", "offline"])
@pytest.mark.parametrize("rover", ["curiousity", "Perseverence", "mars"])
async def test_reconcile_mistyped_rover_exits_2_without_offline_hint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    upstream_router: respx.MockRouter,
    rover: str,
    offline: bool,
) -> None:
    output = tmp_path / "report.json"
    argv = ["reconcile", "--rover", rover, "--output", str(output)]
    if offline:
        argv.append("--offline")
    try:
        code = await asyncio.to_thread(cli.main, argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1

    assert code == 2
    err = capsys.readouterr().err
    assert err.strip()
    assert "--offline" not in err
    assert not upstream_router.calls
    assert not output.exists()
