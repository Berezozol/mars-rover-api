"""Migrations 0008 (Ingenuity) and 0009 (site/drive index): downgrade and upgrade again.

Same safety rules as test_migrations_roundtrip.py (module marker, restore head in `finally`).
Downgrading 0008 removes the loaded Ingenuity data (docs/proposals/stage-12-ingenuity.md
section 5): that is what these tests pin, together with "other rovers are untouched".
"""

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch, _head

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

INDEX = "ix_photos_rover_id_site_drive"  # created by 0009, replaced by 0014
NEAR_INDEX = "ix_photos_near_cover"  # created by 0014
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
