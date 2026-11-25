"""Migration 0021 (hourly RAD): upgrade -> downgrade -> upgrade (stage 22, AC-45) and its schema.

Same safety rules as test_rad_migration.py: module marker `migrations_roundtrip` (runs last, head
is restored in `finally`).
"""

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch
from tests.integration.test_rad_migration import DAILY_ROW, FILE_ROW

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

NEW_DAILY_COLUMNS = {"hourly_revision", "hourly_reject", "hourly_dropped"}
HOURLY_ROW = (
    "INSERT INTO rad_hourly (sol, hour, n_obs, live_s, dose_b_mean, dose_e_mean, bu_count) "
    "VALUES (4843, 0, 2, 1860, 6.8, 6.5, 12941)"
)


async def daily_columns(url: str) -> set[str]:
    rows = await _fetch(
        url,
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'rad_daily'",
    )
    return {r["column_name"] for r in rows}


def test_migration_0021_follows_0020(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0021")

    assert revision is not None
    assert revision.down_revision == "0020"


def test_migration_0021_creates_the_hourly_table_and_columns_ac45(migrated_db: str) -> None:
    assert "rad_hourly" in asyncio.run(_db_state(migrated_db))["tables"]
    assert asyncio.run(daily_columns(migrated_db)) >= NEW_DAILY_COLUMNS


def test_migration_0021_roundtrip_drops_only_the_new_objects_ac45(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    """AC-45: downgrade removes `rad_hourly` and the three columns; the daily data survives."""
    asyncio.run(_execute(migrated_db, FILE_ROW))
    asyncio.run(_execute(migrated_db, DAILY_ROW))
    asyncio.run(_execute(migrated_db, HOURLY_ROW))
    try:
        command.downgrade(alembic_cfg, "0020")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0020"]
        assert "rad_hourly" not in state["tables"]
        assert {"rad_daily", "rad_files", "rad_dataset"} <= state["tables"]
        assert asyncio.run(daily_columns(migrated_db)) & NEW_DAILY_COLUMNS == set()
        rows = asyncio.run(_fetch(migrated_db, "SELECT sol, dose_b_mean, n_used FROM rad_daily"))
        assert [(r["sol"], r["dose_b_mean"], r["n_used"]) for r in rows] == [(4843, 6.8, 3)]

        command.upgrade(alembic_cfg, "0021")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0021"]
        assert "rad_hourly" in state["tables"]
        assert asyncio.run(daily_columns(migrated_db)) >= NEW_DAILY_COLUMNS
        rows = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT hourly_revision, hourly_reject, hourly_dropped FROM rad_daily",
            )
        )
        assert [dict(r) for r in rows] == [
            {"hourly_revision": 0, "hourly_reject": None, "hourly_dropped": 0}
        ]
        count = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM rad_hourly"))
        assert count[0]["n"] == 0
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0021_double_roundtrip_is_clean_ac45(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        for _ in range(2):
            command.downgrade(alembic_cfg, "0020")
            command.upgrade(alembic_cfg, "0021")
        assert "rad_hourly" in asyncio.run(_db_state(migrated_db))["tables"]
    finally:
        command.upgrade(alembic_cfg, "head")
