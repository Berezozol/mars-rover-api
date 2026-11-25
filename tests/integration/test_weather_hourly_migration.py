"""Migration 0020 (hourly extremes of weather): upgrade, CHECK pairs, downgrade (stage 22 B)

Covers AC-23..

Same safety rules as test_weather_migration.py: module marker `migrations_roundtrip` (runs last,
head is restored in `finally`). Spec: docs/proposals/sol-conditions.md section 6 (eleven new
`double precision` null columns of `weather_hourly`, a CHECK on every min/max pair).
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch
from tests.integration.test_weather_migration import (
    DATASET_ROW,
    FILE_ROW,
    WEATHER_TABLES,
    daily_insert,
    hourly_insert,
)

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

PAIRS = [
    ("air_min_k", "air_max_k"),
    ("pressure_min_pa", "pressure_max_pa"),
    ("humidity_min_pct", "humidity_max_pct"),
    ("ground_min_k", "ground_max_k"),
    ("uv_min_wm2", "uv_max_wm2"),
]
NEW_COLUMNS = [name for pair in PAIRS for name in pair] + ["wind_gust_ms"]
OLD_COLUMNS = {
    "rover_id", "sol", "hour", "coverage", "air_k", "pressure_pa", "humidity_pct", "ground_k",
    "uv_wm2", "wind_ms", "wind_dir_deg",
}  # fmt: skip


async def hourly_columns(url: str) -> dict[str, dict[str, str]]:
    rows = await _fetch(
        url,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'weather_hourly'",
    )
    return {r["column_name"]: r for r in rows}


def columns_of(url: str) -> dict[str, dict[str, str]]:
    return asyncio.run(hourly_columns(url))


def seed_hour(url: str, **values: str) -> None:
    asyncio.run(_execute(url, "DELETE FROM weather_daily"))
    asyncio.run(_execute(url, daily_insert(1)))
    asyncio.run(_execute(url, hourly_insert(3)))
    if values:
        sets = ", ".join(f"{k} = {v}" for k, v in values.items())
        asyncio.run(_execute(url, f"UPDATE weather_hourly SET {sets} WHERE sol = 1 AND hour = 3"))


def test_migration_0020_follows_0019(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0020")

    assert revision is not None
    assert revision.down_revision == "0019"


def test_migration_0020_upgrade_adds_eleven_nullable_double_columns_ac23(
    migrated_db: str,
) -> None:
    columns = columns_of(migrated_db)

    assert len(NEW_COLUMNS) == 11
    for name in NEW_COLUMNS:
        assert columns[name]["data_type"] == "double precision", name
        assert columns[name]["is_nullable"] == "YES", name
    assert set(columns) >= OLD_COLUMNS


def test_migration_0020_check_requires_both_or_neither_of_a_pair_ac23(migrated_db: str) -> None:
    seed_hour(migrated_db)
    try:
        for low, high in PAIRS:
            with pytest.raises(IntegrityError):
                asyncio.run(
                    _execute(migrated_db, f"UPDATE weather_hourly SET {low} = 1 WHERE hour = 3")
                )
            with pytest.raises(IntegrityError):
                asyncio.run(
                    _execute(migrated_db, f"UPDATE weather_hourly SET {high} = 1 WHERE hour = 3")
                )
            asyncio.run(
                _execute(
                    migrated_db, f"UPDATE weather_hourly SET {low} = 1, {high} = 2 WHERE hour = 3"
                )
            )
            asyncio.run(
                _execute(
                    migrated_db,
                    f"UPDATE weather_hourly SET {low} = NULL, {high} = NULL WHERE hour = 3",
                )
            )
        # the gust has no pair
        asyncio.run(
            _execute(migrated_db, "UPDATE weather_hourly SET wind_gust_ms = 5 WHERE hour = 3")
        )
    finally:
        asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))


def test_migration_0020_existing_rows_get_null_in_the_new_columns_ac23(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0019")
        seed_hour(migrated_db, air_k="210.5", pressure_pa="740", wind_ms="3.5")
        command.upgrade(alembic_cfg, "0020")

        rows = asyncio.run(_fetch(migrated_db, "SELECT * FROM weather_hourly"))
        assert len(rows) == 1
        row = rows[0]
        assert (row["air_k"], row["pressure_pa"], row["wind_ms"]) == (210.5, 740.0, 3.5)
        assert {name: row[name] for name in NEW_COLUMNS} == dict.fromkeys(NEW_COLUMNS)
    finally:
        asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))
        command.upgrade(alembic_cfg, "head")


def test_migration_0020_downgrade_drops_only_the_new_columns_ac23(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    seed_hour(
        migrated_db, air_k="210.5", air_min_k="205", air_max_k="215", wind_gust_ms="6", wind_ms="3"
    )
    try:
        command.downgrade(alembic_cfg, "0019")

        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0019"]
        assert state["tables"] >= WEATHER_TABLES
        assert set(columns_of(migrated_db)) == OLD_COLUMNS
        rows = asyncio.run(_fetch(migrated_db, "SELECT * FROM weather_hourly"))
        assert len(rows) == 1
        assert (rows[0]["air_k"], rows[0]["wind_ms"], rows[0]["sol"]) == (210.5, 3.0, 1)
        daily = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM weather_daily"))
        assert daily[0]["n"] == 1
    finally:
        asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))
        command.upgrade(alembic_cfg, "head")


def test_migration_0020_upgrade_downgrade_upgrade_is_clean_ac23(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    for sql in (daily_insert(1), FILE_ROW, DATASET_ROW):
        asyncio.run(_execute(migrated_db, sql))
    try:
        for _ in range(2):
            command.downgrade(alembic_cfg, "0019")
            command.upgrade(alembic_cfg, "0020")

        assert set(columns_of(migrated_db)) == OLD_COLUMNS | set(NEW_COLUMNS)
        # the downgrade touches the hourly columns only: the other weather tables keep their rows
        for table in ("weather_daily", "weather_files", "weather_dataset"):
            rows = asyncio.run(_fetch(migrated_db, f"SELECT count(*) AS n FROM {table}"))
            assert rows[0]["n"] == 1, table
        # and the CHECK pairs are back after the second upgrade
        asyncio.run(_execute(migrated_db, hourly_insert(0)))
        with pytest.raises(IntegrityError):
            asyncio.run(
                _execute(migrated_db, "UPDATE weather_hourly SET uv_min_wm2 = 1 WHERE hour = 0")
            )
    finally:
        asyncio.run(_execute(migrated_db, "DELETE FROM weather_daily"))
        command.upgrade(alembic_cfg, "head")
