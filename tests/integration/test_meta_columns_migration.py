"""Migration 0013 (`photos.local_time_s`, `width`, `height` + partial indexes; stage 18B-2).

Spec: docs/proposals/stage-18b-filters.md section 6 and AC-12 / AC-14 (schema part): three
nullable columns (integer, smallint, smallint), partial indexes
`ix_photos_rover_id_local_time (rover_id, local_time_s) WHERE local_time_s IS NOT NULL` and
`ix_photos_rover_id_width (rover_id, width) WHERE width IS NOT NULL`, created and dropped
CONCURRENTLY; `downgrade` drops indexes and columns and keeps the photos.

Same safety rules as test_taken_at_index_migration.py: the module is marked
`migrations_roundtrip` (runs last), the head is restored in `finally`.
"""

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_migrations_roundtrip import _db_state, _fetch

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

COLUMNS = {"local_time_s": "integer", "width": "smallint", "height": "smallint"}
LOCAL_TIME_INDEX = "ix_photos_rover_id_local_time"
WIDTH_INDEX = "ix_photos_rover_id_width"
HEIGHT_INDEX = "ix_photos_rover_id_height"

_COLUMNS_SQL = (
    "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
    "WHERE table_name = 'photos' AND column_name IN ('local_time_s', 'width', 'height')"
)


def _index_sql(name: str) -> str:
    return f"SELECT indexdef FROM pg_indexes WHERE tablename = 'photos' AND indexname = '{name}'"


def _normalized(definition: str) -> str:
    return " ".join(definition.split())


def test_ac12_migration_0013_follows_0012(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0013")

    assert revision is not None
    assert revision.down_revision == "0012"


def test_ac12_up_adds_three_nullable_columns_without_default(migrated_db: str) -> None:
    rows = asyncio.run(_fetch(migrated_db, _COLUMNS_SQL))

    assert {r["column_name"]: r["data_type"] for r in rows} == COLUMNS
    assert {r["is_nullable"] for r in rows} == {"YES"}
    assert {r["column_default"] for r in rows} == {None}  # catalog-only ADD COLUMN


def test_ac12_up_creates_the_partial_index_on_local_time(migrated_db: str) -> None:
    [row] = asyncio.run(_fetch(migrated_db, _index_sql(LOCAL_TIME_INDEX)))

    definition = _normalized(row["indexdef"])
    assert "(rover_id, local_time_s)" in definition
    assert "WHERE (local_time_s IS NOT NULL)" in definition


def test_ac12_up_creates_the_partial_index_on_width(migrated_db: str) -> None:
    [row] = asyncio.run(_fetch(migrated_db, _index_sql(WIDTH_INDEX)))

    definition = _normalized(row["indexdef"])
    assert "(rover_id, width)" in definition
    assert "WHERE (width IS NOT NULL)" in definition


def test_ac12_up_creates_the_partial_index_on_height(migrated_db: str) -> None:
    [row] = asyncio.run(_fetch(migrated_db, _index_sql(HEIGHT_INDEX)))

    definition = _normalized(row["indexdef"])
    assert "(rover_id, height)" in definition
    assert "WHERE (height IS NOT NULL)" in definition


def test_ac12_the_new_indexes_are_valid(migrated_db: str) -> None:
    """A failed CREATE INDEX CONCURRENTLY leaves an invalid index behind."""
    rows = asyncio.run(
        _fetch(
            migrated_db,
            "SELECT c.relname, i.indisvalid, i.indisready FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            f"WHERE c.relname IN ('{LOCAL_TIME_INDEX}', '{WIDTH_INDEX}', '{HEIGHT_INDEX}')",
        )
    )

    assert {r["relname"] for r in rows} == {LOCAL_TIME_INDEX, WIDTH_INDEX, HEIGHT_INDEX}
    assert all(r["indisvalid"] and r["indisready"] for r in rows)


def test_ac12_down_drops_columns_and_indexes_keeps_photos_and_up_restores(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    insert = (
        "INSERT INTO photos (rover_id, camera_id, source_id, sol, earth_date, taken_at, "
        "sample_type, img_full, local_time_s, width, height) "
        "SELECT 'perseverance', c.id, 'mig-0013-keep', 7, DATE '2021-03-01', "
        "TIMESTAMPTZ '2021-03-01 12:00:00+00', 'full', 'https://x.test/i', 61200, 1920, 1200 "
        "FROM cameras c WHERE c.rover_id = 'perseverance' LIMIT 1"
    )
    try:
        asyncio.run(_fetch_write(migrated_db, insert))
        command.downgrade(alembic_cfg, "0012")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0012"]
        assert not asyncio.run(_fetch(migrated_db, _COLUMNS_SQL))
        assert not asyncio.run(_fetch(migrated_db, _index_sql(LOCAL_TIME_INDEX)))
        assert not asyncio.run(_fetch(migrated_db, _index_sql(WIDTH_INDEX)))
        assert not asyncio.run(_fetch(migrated_db, _index_sql(HEIGHT_INDEX)))
        # The 0012 index and the photo itself survive.
        assert asyncio.run(_fetch(migrated_db, _index_sql("ix_photos_rover_id_taken_at")))
        kept = asyncio.run(
            _fetch(migrated_db, "SELECT sol FROM photos WHERE source_id = 'mig-0013-keep'")
        )
        assert kept == [{"sol": 7}]

        command.upgrade(alembic_cfg, "0013")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0013"]
        assert {r["column_name"] for r in asyncio.run(_fetch(migrated_db, _COLUMNS_SQL))} == set(
            COLUMNS
        )
        assert asyncio.run(_fetch(migrated_db, _index_sql(LOCAL_TIME_INDEX)))
        assert asyncio.run(_fetch(migrated_db, _index_sql(WIDTH_INDEX)))
        assert asyncio.run(_fetch(migrated_db, _index_sql(HEIGHT_INDEX)))
        # Re-created columns are empty: the data is gone with the downgrade (backfill-meta refills).
        refilled = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT local_time_s, width, height FROM photos WHERE source_id = 'mig-0013-keep'",
            )
        )
        assert refilled == [{"local_time_s": None, "width": None, "height": None}]
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(
            _fetch_write(migrated_db, "DELETE FROM photos WHERE source_id = 'mig-0013-keep'")
        )


def test_ac12_alembic_check_reports_no_drift(alembic_cfg: AlembicConfig) -> None:
    """Models and migrations agree (columns and indexes are declared in app/db/models.py)."""
    command.check(alembic_cfg)


async def _fetch_write(url: str, sql: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()
