"""Migration 0010 (samples): downgrade to 0009 and upgrade again.

Same safety rules as test_migrations_roundtrip.py (module marker, restore head in `finally`).
Downgrading drops both tables with their data (the data is restored by one `samples` run);
nothing else is touched.
"""

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

SAMPLE_TABLES = {"rover_samples", "sample_datasets"}
SEED_CAMERAS = 17 + 7 + 6 + 6 + 2


def test_migration_0010_downgrade_drops_the_sample_tables_and_upgrade_recreates_them(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        assert asyncio.run(_db_state(migrated_db))["tables"] >= SAMPLE_TABLES
        command.downgrade(alembic_cfg, "0009")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0009"]
        assert state["tables"] & SAMPLE_TABLES == set()
        # Nothing else is touched: the seed, photos and the location tables stay.
        assert state["tables"] >= {
            "photos",
            "rovers",
            "cameras",
            "rover_waypoints",
            "location_datasets",
            "ingenuity_flights",
        }
        assert state["counts"] == {"rovers": 5, "cameras": SEED_CAMERAS}
        command.upgrade(alembic_cfg, "0010")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0010"]
        assert state["tables"] >= SAMPLE_TABLES
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0010_downgrade_removes_stored_samples_and_keeps_other_data(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    asyncio.run(
        _execute(
            migrated_db,
            "INSERT INTO rover_samples (rover_id, number, name) VALUES ('perseverance', 1, 'X')",
        )
    )
    asyncio.run(
        _execute(migrated_db, "INSERT INTO sample_datasets (rover_id) VALUES ('perseverance')")
    )
    try:
        command.downgrade(alembic_cfg, "0009")
        command.upgrade(alembic_cfg, "0010")
        for table in ("rover_samples", "sample_datasets"):
            rows = asyncio.run(_fetch(migrated_db, f"SELECT count(*) AS n FROM {table}"))
            assert rows[0]["n"] == 0, table
        rovers = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM rovers"))
        assert rovers[0]["n"] == 5
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0010_is_the_head_revision_after_0009(alembic_cfg: AlembicConfig) -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    revision = script.get_revision("0010")

    assert revision is not None
    assert revision.down_revision == "0009"
