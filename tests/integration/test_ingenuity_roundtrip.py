"""Migrations 0008 (Ingenuity) and 0009 (site/drive index): downgrade and upgrade again.

Same safety rules as test_migrations_roundtrip.py (module marker, restore head in `finally`).
Downgrading 0008 removes the loaded Ingenuity data (docs/proposals/stage-12-ingenuity.md
section 5): that is what these tests pin, together with "other rovers are untouched".
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch, _head

_MIGRATION_0015 = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "20261127_0015_photos_near_cover_include.py"
)
_spec = importlib.util.spec_from_file_location("migration_0015_under_test", _MIGRATION_0015)
assert _spec is not None and _spec.loader is not None
_migration_0015 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration_0015)

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

INDEX = "ix_photos_rover_id_site_drive"  # created by 0009, replaced by 0014
NEAR_INDEX = "ix_photos_near_cover"  # created by 0014, widened by 0015
NEAR_INDEX_TMP = _migration_0015.SWAP_INDEX  # 0015's temporary name: must never be left behind
assert NEAR_INDEX == _migration_0015.INDEX
OLD_INCLUDE = "(rover_id, site, drive) INCLUDE (sol, taken_at, id)"  # 0014
NEW_INCLUDE = "(rover_id, site, drive) INCLUDE (sol, taken_at, id, sample_type, camera_id)"  # 0015
SEED_CAMERAS_BEFORE = 17 + 7 + 6 + 6


def _index_names(url: str) -> list[str]:
    rows = asyncio.run(_fetch(url, "SELECT indexname FROM pg_indexes WHERE tablename = 'photos'"))
    return [r["indexname"] for r in rows]


async def _insert_ingenuity_data(url: str) -> None:
    """One photo and its derived rows for Ingenuity and for Curiosity."""
    for rover, code, sol in (("ingenuity", "HELI_NAV", 1069), ("curiosity", "FHAZ", 7777)):
        await _execute(
            url,
            "INSERT INTO photos (rover_id, camera_id, source_id, sol, earth_date, taken_at, "
            "sample_type, img_full) "
            f"SELECT '{rover}', c.id, 'rt-{rover}', {sol}, DATE '2024-02-22', "
            "TIMESTAMPTZ '2024-02-22 01:00:00+00', 'full', 'https://x.test/i' "
            f"FROM cameras c WHERE c.rover_id = '{rover}' AND c.code = '{code}'",
        )
        await _execute(
            url,
            "INSERT INTO sol_summaries (rover_id, sol, earth_date, total_photos, cameras, "
            f"full_photos, full_cameras) VALUES ('{rover}', {sol}, DATE '2024-02-22', 1, "
            f"ARRAY['{code}'], 1, ARRAY['{code}'])",
        )
        await _execute(
            url,
            "INSERT INTO camera_sol_counts (rover_id, sol, camera_id, total_photos, full_photos) "
            f"SELECT '{rover}', {sol}, c.id, 1, 1 FROM cameras c "
            f"WHERE c.rover_id = '{rover}' AND c.code = '{code}'",
        )
        await _execute(
            url,
            "INSERT INTO sol_source_counts (rover_id, sol, received, dropped, source_total, "
            f"fetched_at) VALUES ('{rover}', {sol}, 1, 0, 1, now())",
        )
        await _execute(
            url,
            "INSERT INTO sync_runs (rover_id, mode, status, error) "
            f"VALUES ('{rover}', 'backfill', 'success', 'rt-{rover}')",
        )


def _count(url: str, table: str, rover: str) -> int:
    column = "id" if table == "rovers" else "rover_id"
    rows = asyncio.run(_fetch(url, f"SELECT count(*) AS n FROM {table} WHERE {column} = '{rover}'"))
    return rows[0]["n"]


def test_migration_0009_downgrade_drops_the_index_and_upgrade_recreates_it(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    """At head the 0009 index is replaced by the covering one (0014); at 0009 it is back."""
    try:
        names = _index_names(migrated_db)
        assert NEAR_INDEX in names
        assert INDEX not in names
        command.downgrade(alembic_cfg, "0008")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0008"]
        names = _index_names(migrated_db)
        assert INDEX not in names
        assert NEAR_INDEX not in names
        # 0008 stays: the seed and the flight log survive.
        assert asyncio.run(_db_state(migrated_db))["counts"]["rovers"] == 5
        command.upgrade(alembic_cfg, "0009")
        assert INDEX in _index_names(migrated_db)
        assert NEAR_INDEX not in _index_names(migrated_db)
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0014_downgrade_restores_the_plain_index_and_upgrade_replaces_it(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0013")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0013"]
        names = _index_names(migrated_db)
        assert INDEX in names
        assert NEAR_INDEX not in names
        command.upgrade(alembic_cfg, "0014")
        names = _index_names(migrated_db)
        assert NEAR_INDEX in names
        assert INDEX not in names
    finally:
        command.upgrade(alembic_cfg, "head")


def _near_index_state(url: str) -> list[dict]:
    return asyncio.run(
        _fetch(
            url,
            "SELECT c.relname AS name, i.indisvalid AS valid, i.indisready AS ready, "
            "pg_get_indexdef(i.indexrelid) AS definition "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            f"WHERE c.relname IN ('{NEAR_INDEX}', '{NEAR_INDEX_TMP}')",
        )
    )


def _assert_near_index(url: str, include: str, other: str) -> None:
    [index] = _near_index_state(url)  # exactly one: the temporary name is gone
    assert index["name"] == NEAR_INDEX
    assert index["valid"] and index["ready"]
    definition = index["definition"]
    assert include in definition
    assert other not in definition
    assert "WHERE (site IS NOT NULL)" in definition
    assert "ON public.photos" in definition


def test_migration_0015_upgrade_downgrade_upgrade_widens_and_narrows_the_covering_index(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    """AC-15 (migration part): head -> 0014 -> 0015 swaps the INCLUDE list and back."""
    try:
        assert _head(alembic_cfg) == "0015"
        _assert_near_index(migrated_db, NEW_INCLUDE, OLD_INCLUDE)

        command.downgrade(alembic_cfg, "0014")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0014"]
        _assert_near_index(migrated_db, OLD_INCLUDE, "sample_type, camera_id")

        command.upgrade(alembic_cfg, "0015")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0015"]
        _assert_near_index(migrated_db, NEW_INCLUDE, OLD_INCLUDE)
    finally:
        command.upgrade(alembic_cfg, "head")


async def _autocommit(url: str, *statements: str) -> None:
    """Run statements outside a transaction (CREATE INDEX CONCURRENTLY needs that)."""
    engine = create_async_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


def _create_swap(url: str, include: str) -> list[str]:
    return [
        f"CREATE INDEX CONCURRENTLY {NEAR_INDEX_TMP} ON photos (rover_id, site, drive) "
        f"INCLUDE ({include}) WHERE site IS NOT NULL"
    ]


def _swap_oid(url: str) -> int | None:
    rows = asyncio.run(_fetch(url, f"SELECT to_regclass('{NEAR_INDEX_TMP}')::oid::int AS oid"))
    return rows[0]["oid"]


def _main_oid(url: str) -> int | None:
    rows = asyncio.run(_fetch(url, f"SELECT to_regclass('{NEAR_INDEX}')::oid::int AS oid"))
    return rows[0]["oid"]


def test_migration_0015_rerun_with_invalid_swap_index_and_old_main_rebuilds_the_swap(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    """AC-15: a crash during the build leaves an INVALID swap index; the rerun replaces it."""
    try:
        command.downgrade(alembic_cfg, "0014")
        asyncio.run(
            _autocommit(
                migrated_db,
                *_create_swap(migrated_db, "sol, taken_at, id, sample_type, camera_id"),
                f"UPDATE pg_index SET indisvalid = false "
                f"WHERE indexrelid = '{NEAR_INDEX_TMP}'::regclass",
            )
        )
        broken = _near_index_state(migrated_db)
        assert {i["name"]: i["valid"] for i in broken} == {NEAR_INDEX: True, NEAR_INDEX_TMP: False}

        command.upgrade(alembic_cfg, "0015")

        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0015"]
        _assert_near_index(migrated_db, NEW_INCLUDE, OLD_INCLUDE)
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0015_rerun_with_main_missing_and_valid_swap_only_renames_it(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    """AC-15: a crash after the drop, before the rename: the valid swap index is kept."""
    try:
        command.downgrade(alembic_cfg, "0014")
        asyncio.run(
            _autocommit(
                migrated_db,
                *_create_swap(migrated_db, "sol, taken_at, id, sample_type, camera_id"),
                f"DROP INDEX CONCURRENTLY {NEAR_INDEX}",
            )
        )
        swap_oid = _swap_oid(migrated_db)
        assert swap_oid is not None and _main_oid(migrated_db) is None

        command.upgrade(alembic_cfg, "0015")

        _assert_near_index(migrated_db, NEW_INCLUDE, OLD_INCLUDE)
        assert _main_oid(migrated_db) == swap_oid  # renamed, not rebuilt
        assert _swap_oid(migrated_db) is None
    finally:
        command.upgrade(alembic_cfg, "head")


@pytest.mark.parametrize(
    ("direction", "start", "wrong_include", "target", "wanted", "other"),
    [
        pytest.param(
            "upgrade",
            "0014",
            "sol, taken_at, id",
            "0015",
            NEW_INCLUDE,
            OLD_INCLUDE,
            id="upgrade-with-old-swap",
        ),
        pytest.param(
            "downgrade",
            "0015",
            "sol, taken_at, id, sample_type, camera_id",
            "0014",
            OLD_INCLUDE,
            "sample_type, camera_id",
            id="downgrade-with-new-swap",
        ),
    ],
)
def test_migration_0015_rerun_with_valid_swap_of_the_wrong_include_rebuilds_it(
    migrated_db: str,
    alembic_cfg: AlembicConfig,
    direction: str,
    start: str,
    wrong_include: str,
    target: str,
    wanted: str,
    other: str,
) -> None:
    """AC-15: a VALID swap index left by the opposite direction must not be promoted as is."""
    try:
        if start == "0014":
            command.downgrade(alembic_cfg, "0014")
        asyncio.run(_autocommit(migrated_db, *_create_swap(migrated_db, wrong_include)))
        assert [i["valid"] for i in _near_index_state(migrated_db)] == [True, True]

        if direction == "upgrade":
            command.upgrade(alembic_cfg, target)
        else:
            command.downgrade(alembic_cfg, target)

        _assert_near_index(migrated_db, wanted, other)
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0015_keeps_photos_and_leaves_no_other_near_index(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    """AC-15: only the index is replaced; rows survive both directions; no stray index."""
    asyncio.run(
        _execute(
            migrated_db,
            "INSERT INTO photos (rover_id, camera_id, source_id, sol, earth_date, taken_at, "
            "sample_type, img_full, site, drive) "
            "SELECT 'curiosity', c.id, 'rt-0015', 7, DATE '2012-08-13', "
            "TIMESTAMPTZ '2012-08-13 01:00:00+00', 'full', 'https://x.test/i', 5, 10 "
            "FROM cameras c WHERE c.rover_id = 'curiosity' AND c.code = 'FHAZ'",
        )
    )
    try:
        command.downgrade(alembic_cfg, "0014")
        command.upgrade(alembic_cfg, "0015")
        rows = asyncio.run(
            _fetch(migrated_db, "SELECT site, drive FROM photos WHERE source_id = 'rt-0015'")
        )
        assert rows == [{"site": 5, "drive": 10}]
        names = _index_names(migrated_db)
        assert names.count(NEAR_INDEX) == 1
        assert NEAR_INDEX_TMP not in names
        assert INDEX not in names
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(_execute(migrated_db, "DELETE FROM photos WHERE source_id = 'rt-0015'"))


def test_migration_0008_downgrade_removes_ingenuity_everywhere_and_keeps_other_rovers(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    asyncio.run(_insert_ingenuity_data(migrated_db))
    try:
        command.downgrade(alembic_cfg, "0007")

        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0007"]
        assert "ingenuity_flights" not in state["tables"]
        assert state["counts"] == {"rovers": 4, "cameras": SEED_CAMERAS_BEFORE}
        for table in ("photos", "sol_summaries", "camera_sol_counts", "sol_source_counts",
                      "sync_runs", "cameras", "rovers"):  # fmt: skip
            assert _count(migrated_db, table, "ingenuity") == 0, table
        # Other rovers keep their data.
        for table in ("photos", "sol_summaries", "camera_sol_counts", "sol_source_counts",
                      "sync_runs"):  # fmt: skip
            assert _count(migrated_db, table, "curiosity") == 1, table
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(_execute(migrated_db, "DELETE FROM photos WHERE source_id LIKE 'rt-%'"))
        for table in ("camera_sol_counts", "sol_summaries", "sol_source_counts"):
            asyncio.run(_execute(migrated_db, f"DELETE FROM {table} WHERE sol IN (7777, 1069)"))
        asyncio.run(_execute(migrated_db, "DELETE FROM sync_runs WHERE error LIKE 'rt-%'"))

    # Upgrading again re-seeds the rover, the cameras and the flight log; no photos come back.
    state = asyncio.run(_db_state(migrated_db))
    assert state["versions"] == [_head(alembic_cfg)]
    assert state["counts"] == {"rovers": 5, "cameras": SEED_CAMERAS_BEFORE + 2}
    flights = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM ingenuity_flights"))
    assert flights == [{"n": 72}]
    assert _count(migrated_db, "photos", "ingenuity") == 0


def test_migration_0008_upgrade_from_0007_seeds_rover_cameras_and_72_flights(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0007")
        command.upgrade(alembic_cfg, "0008")

        rover = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT legacy_id, name, mission, launch_date::text AS launch, "
                "landing_date::text AS landing, end_date::text AS ended, status "
                "FROM rovers WHERE id = 'ingenuity'",
            )
        )
        assert rover == [
            {
                "legacy_id": 9,
                "name": "Ingenuity",
                "mission": "mars2020",
                "launch": "2020-07-30",
                "landing": "2021-02-18",
                "ended": "2024-01-25",
                "status": "complete",
            }
        ]
        flights = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT min(flight) AS lo, max(flight) AS hi, count(*) AS n FROM ingenuity_flights",
            )
        )
        assert flights == [{"lo": 1, "hi": 72, "n": 72}]
        assert INDEX not in _index_names(migrated_db)  # 0009 is not applied yet
    finally:
        command.upgrade(alembic_cfg, "head")
