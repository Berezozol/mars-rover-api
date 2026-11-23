"""Migration 0017 (RAD tables): upgrade -> downgrade -> upgrade (stage 21, AC-37).

Same safety rules as test_migrations_roundtrip.py: module marker `migrations_roundtrip`
(runs last, head is restored in `finally`). Downgrading drops only `rad_files`, `rad_daily`
and `rad_dataset`.
"""

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

RAD_TABLES = {"rad_files", "rad_daily", "rad_dataset"}

FILE_ROW = (
    "INSERT INTO rad_files (path, sol, md5, status, role, size_bytes, processed_at) VALUES "
    "('DATA/SOL_04710_04843/RAD_RDR_2026_080_17_18_4843_V00.TXT', 4843, "
    "'53e137ba4ac493af2b26d585b5caf5d1', 'loaded', 'primary', 1000, now())"
)
DAILY_ROW = (
    "INSERT INTO rad_daily (sol, earth_date, dose_b_mean, dose_b_min, dose_b_max, dose_e_mean, "
    "dose_e_min, dose_e_max, bu_count, live_s, n_used, source_path, updated_at) VALUES "
    "(4843, '2026-03-21', 6.8, 6.7, 6.9, 6.5, 6.4, 6.6, 19450, 2790, 3, 'DATA/x.TXT', now())"
)
DATASET_ROW = "INSERT INTO rad_dataset (id, checksums_etag, files_listed) VALUES (1, 'e', 1)"


def test_migration_0017_follows_0016(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0017")

    assert revision is not None
    assert revision.down_revision == "0016"


def test_migration_0017_downgrade_drops_the_rad_tables_and_upgrade_recreates_them_ac37(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        assert asyncio.run(_db_state(migrated_db))["tables"] >= RAD_TABLES
        command.downgrade(alembic_cfg, "0016")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0016"]
        assert state["tables"] & RAD_TABLES == set()
        assert state["tables"] >= {"photos", "rovers", "cameras", "api_keys", "rover_samples"}
        command.upgrade(alembic_cfg, "0017")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0017"]
        assert state["tables"] >= RAD_TABLES
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0017_roundtrip_on_filled_tables_loses_only_the_rad_data_ac37(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    for sql in (FILE_ROW, DAILY_ROW, DATASET_ROW):
        asyncio.run(_execute(migrated_db, sql))
    try:
        command.downgrade(alembic_cfg, "0016")
        command.upgrade(alembic_cfg, "0017")
        for table in RAD_TABLES:
            rows = asyncio.run(_fetch(migrated_db, f"SELECT count(*) AS n FROM {table}"))
            assert rows[0]["n"] == 0, table
        rovers = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM rovers"))
        assert rovers[0]["n"] == 5
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0017_double_roundtrip_is_clean_ac37(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        for _ in range(2):
            command.downgrade(alembic_cfg, "0016")
            command.upgrade(alembic_cfg, "0017")
        assert asyncio.run(_db_state(migrated_db))["tables"] >= RAD_TABLES
    finally:
        command.upgrade(alembic_cfg, "head")
