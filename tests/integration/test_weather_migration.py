"""Migration 0018 (weather tables): upgrade, CHECK constraints, downgrade (stage 17, AC-24).

Same safety rules as test_rad_migration.py: module marker `migrations_roundtrip` (runs last,
head is restored in `finally`). Downgrading drops only the four weather tables.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

WEATHER_TABLES = {"weather_files", "weather_daily", "weather_hourly", "weather_dataset"}

DAILY_COLUMNS = "rover_id, sol, earth_date, coverage, hours_covered, source_version, parser_revision, updated_at"  # noqa: E501


def daily_insert(sol: int = 1, coverage: str = "0.5", hours: int = 24, **extra: str) -> str:
    columns = DAILY_COLUMNS + "".join(f", {k}" for k in extra)
    values = (
        f"'perseverance', {sol}, DATE '2021-06-01', {coverage}, {hours}, '{{}}'::jsonb, 1, now()"
        + "".join(f", {v}" for v in extra.values())
    )
    return f"INSERT INTO weather_daily ({columns}) VALUES ({values})"


def hourly_insert(hour: int, sol: int = 1) -> str:
    return (
        "INSERT INTO weather_hourly (rover_id, sol, hour, coverage) "
        f"VALUES ('perseverance', {sol}, {hour}, 0)"
    )


FILE_ROW = (
    "INSERT INTO weather_files (rover_id, path, sol, product, version, size_bytes, mtime_ns, "
    "status, parsed_at) VALUES ('perseverance', 'data_derived_env/sol_0000_0089/sol_0001/x.CSV', "
    "1, 'DER_PS', 'P01', 100, 1, 'parsed', now())"
)
DATASET_ROW = (
    "INSERT INTO weather_dataset (rover_id, parsed_at, changed_at, updated_at, source_max_sol) "
    "VALUES ('perseverance', now(), now(), now(), 1)"
)


def test_migration_0018_follows_0017(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0018")

    assert revision is not None
    assert revision.down_revision == "0017"


def test_migration_0018_upgrade_creates_the_four_tables_ac24(migrated_db: str) -> None:
    assert asyncio.run(_db_state(migrated_db))["tables"] >= WEATHER_TABLES


def test_migration_0018_check_constraints_reject_bad_values_ac24(migrated_db: str) -> None:
    asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))
    asyncio.run(_execute(migrated_db, daily_insert(1)))
    try:
        # hour 0..23
        asyncio.run(_execute(migrated_db, hourly_insert(0)))
        asyncio.run(_execute(migrated_db, hourly_insert(23)))
        with pytest.raises(IntegrityError):
            asyncio.run(_execute(migrated_db, hourly_insert(24)))
        with pytest.raises(IntegrityError):
            asyncio.run(_execute(migrated_db, hourly_insert(-1)))
        # hours_covered 0..24
        asyncio.run(_execute(migrated_db, daily_insert(2, hours=0)))
        asyncio.run(_execute(migrated_db, daily_insert(3, hours=24)))
        with pytest.raises(IntegrityError):
            asyncio.run(_execute(migrated_db, daily_insert(4, hours=25)))
        with pytest.raises(IntegrityError):
            asyncio.run(_execute(migrated_db, daily_insert(4, hours=-1)))
        # coverage 0..1
        asyncio.run(_execute(migrated_db, daily_insert(5, coverage="0")))
        asyncio.run(_execute(migrated_db, daily_insert(6, coverage="1")))
        with pytest.raises(IntegrityError):
            asyncio.run(_execute(migrated_db, daily_insert(7, coverage="1.01")))
        with pytest.raises(IntegrityError):
            asyncio.run(_execute(migrated_db, daily_insert(7, coverage="-0.01")))
    finally:
        asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))


def test_migration_0018_hourly_rows_follow_their_daily_row_on_delete_ac24(
    migrated_db: str,
) -> None:
    asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))
    asyncio.run(_execute(migrated_db, daily_insert(1)))
    asyncio.run(_execute(migrated_db, hourly_insert(3)))
    try:
        # an hourly row needs its daily row
        with pytest.raises(IntegrityError):
            asyncio.run(_execute(migrated_db, hourly_insert(3, sol=2)))
        asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily WHERE sol = 1"))

        rows = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM weather_hourly"))
        assert rows[0]["n"] == 0
    finally:
        asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))


def test_migration_0018_rover_ids_are_foreign_keys_ac24(migrated_db: str) -> None:
    with pytest.raises(IntegrityError):
        asyncio.run(_execute(migrated_db, daily_insert(1).replace("'perseverance'", "'pluto'")))


def test_migration_0018_downgrade_drops_only_the_weather_tables_ac24(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0017")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0017"]
        assert state["tables"] & WEATHER_TABLES == set()
        assert state["tables"] >= {"photos", "rovers", "cameras", "rad_daily", "rad_files"}
        command.upgrade(alembic_cfg, "0018")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0018"]
        assert state["tables"] >= WEATHER_TABLES
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0018_roundtrip_on_filled_tables_loses_only_the_weather_data_ac24(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    for sql in (daily_insert(1), hourly_insert(0), FILE_ROW, DATASET_ROW):
        asyncio.run(_execute(migrated_db, sql))
    try:
        command.downgrade(alembic_cfg, "0017")
        command.upgrade(alembic_cfg, "0018")
        for table in WEATHER_TABLES:
            rows = asyncio.run(_fetch(migrated_db, f"SELECT count(*) AS n FROM {table}"))
            assert rows[0]["n"] == 0, table
        rovers = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM rovers"))
        assert rovers[0]["n"] == 5
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0018_double_roundtrip_is_clean_ac24(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        for _ in range(2):
            command.downgrade(alembic_cfg, "0017")
            command.upgrade(alembic_cfg, "0018")
        assert asyncio.run(_db_state(migrated_db))["tables"] >= WEATHER_TABLES
    finally:
        command.upgrade(alembic_cfg, "head")
