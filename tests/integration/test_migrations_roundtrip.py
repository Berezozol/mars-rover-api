"""Migration round trips: downgrade to base / step by step, then upgrade back to head.

These tests drop and recreate the whole schema of the shared, session-scoped test database
(`migrated_db`). They are therefore NOT safe under pytest-xdist or alongside any other
process using TEST_DATABASE_URL. Safety measures:
- the `migrations_roundtrip` marker makes conftest run them after all other tests;
- every test restores the database to head (with seed data) in `finally`;
- they are sync on purpose: migrations/env.py calls asyncio.run() itself.
"""

import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

APP_TABLES = {
    "rovers",
    "cameras",
    "photos",
    "sol_summaries",
    "sync_runs",
    "camera_sol_counts",
    "sol_source_counts",
}


def _head(alembic_cfg: AlembicConfig) -> str:
    head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    assert head is not None
    return head


async def _db_state(url: str) -> dict[str, Any]:
    """Snapshot of what matters for reversibility, read on a fresh connection."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).scalars()
            )
            enum_types = set(
                (
                    await conn.execute(
                        text(
                            "SELECT t.typname FROM pg_type t "
                            "JOIN pg_namespace n ON n.oid = t.typnamespace "
                            "WHERE n.nspname = 'public' AND t.typtype = 'e'"
                        )
                    )
                ).scalars()
            )
            versions = (
                list(
                    (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars()
                )
                if "alembic_version" in tables
                else []
            )
            counts: dict[str, int] = {}
            for table in ("rovers", "cameras"):
                if table in tables:
                    counts[table] = (
                        await conn.execute(text(f"SELECT count(*) FROM {table}"))
                    ).scalar_one()
            return {"tables": tables, "enums": enum_types, "versions": versions, "counts": counts}
    finally:
        await engine.dispose()


def test_migrations_downgrade_to_base_and_upgrade_restores_schema_and_seed(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    head = _head(alembic_cfg)
    try:
        command.downgrade(alembic_cfg, "base")
        after_down = asyncio.run(_db_state(migrated_db))
        assert after_down["tables"] & APP_TABLES == set()
        assert after_down["enums"] == set()
        assert after_down["versions"] == []
    finally:
        # Always leave the session-scoped database at head for the other tests.
        command.upgrade(alembic_cfg, "head")

    after_up = asyncio.run(_db_state(migrated_db))
    assert after_up["tables"] >= APP_TABLES
    assert after_up["versions"] == [head]
    assert after_up["counts"] == {"rovers": 4, "cameras": 17 + 7 + 6 + 6}


def test_migrations_step_down_and_up_each_revision(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    revisions = [rev.revision for rev in script.walk_revisions()]  # head first
    try:
        for expected_after in [*revisions[1:], None]:
            command.downgrade(alembic_cfg, "-1")
            state = asyncio.run(_db_state(migrated_db))
            assert state["versions"] == ([expected_after] if expected_after else [])
    finally:
        command.upgrade(alembic_cfg, "head")
    assert asyncio.run(_db_state(migrated_db))["versions"] == [revisions[0]]


async def _sync_run_columns_and_rows(url: str) -> tuple[set[str], list[Any]]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            columns = set(
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'sync_runs'"
                        )
                    )
                ).scalars()
            )
            rows = list((await conn.execute(text("SELECT * FROM sync_runs"))).mappings())
            return columns, [dict(row) for row in rows]
    finally:
        await engine.dispose()


async def _execute(url: str, sql: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


def test_migration_0003_failed_sols_down_and_up_keeps_existing_runs(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0002")
        columns, _ = asyncio.run(_sync_run_columns_and_rows(migrated_db))
        assert "failed_sols" not in columns
        # A run written before 0003 gets an empty failed_sols array after the upgrade.
        asyncio.run(
            _execute(
                migrated_db,
                "INSERT INTO sync_runs (rover_id, mode, status, error) "
                "VALUES ('curiosity', 'backfill', 'partial', 'pre-0003')",
            )
        )
        command.upgrade(alembic_cfg, "0003")
        columns, rows = asyncio.run(_sync_run_columns_and_rows(migrated_db))
        assert "failed_sols" in columns
        [row] = [r for r in rows if r["error"] == "pre-0003"]
        assert row["failed_sols"] == []
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(_execute(migrated_db, "DELETE FROM sync_runs WHERE error = 'pre-0003'"))


# --- 0004: camera_sol_counts / sol_source_counts / sync_runs.source_total ----------------

# Photos written before 0004: (source_id, sol, camera code, sample_type).
PRE_0004_PHOTOS = [
    ("pre0004-1", 50, "FHAZ", "full"),
    ("pre0004-2", 50, "FHAZ", "full"),
    ("pre0004-3", 50, "FHAZ", "subframe"),
    ("pre0004-4", 50, "NAVCAM", "downsampled"),
    ("pre0004-5", 51, "NAVCAM", "full"),
]


async def _fetch(url: str, sql: str) -> list[dict[str, Any]]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return [dict(row) for row in (await conn.execute(text(sql))).mappings()]
    finally:
        await engine.dispose()


async def _insert_pre_0004_photos(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for source_id, sol, code, sample_type in PRE_0004_PHOTOS:
                await conn.execute(
                    text(
                        "INSERT INTO photos (rover_id, camera_id, source_id, sol, earth_date, "
                        "taken_at, sample_type, img_full) "
                        "SELECT 'curiosity', c.id, :source_id, :sol, DATE '2012-09-01', "
                        "TIMESTAMPTZ '2012-09-01 12:00:00+00', :sample_type, 'https://x.test/i' "
                        "FROM cameras c WHERE c.rover_id = 'curiosity' AND c.code = :code"
                    ),
                    {"source_id": source_id, "sol": sol, "code": code, "sample_type": sample_type},
                )
    finally:
        await engine.dispose()


def _tables_and_sync_run_columns(url: str) -> tuple[set[str], set[str]]:
    state = asyncio.run(_db_state(url))
    columns, _ = asyncio.run(_sync_run_columns_and_rows(url))
    return state["tables"], columns


def test_migration_0004_downgrade_removes_count_tables_and_source_total(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0003")
        tables, columns = _tables_and_sync_run_columns(migrated_db)
        assert {"camera_sol_counts", "sol_source_counts"} & tables == set()
        assert "source_total" not in columns
        command.upgrade(alembic_cfg, "0004")
        tables, columns = _tables_and_sync_run_columns(migrated_db)
        assert {"camera_sol_counts", "sol_source_counts"} <= tables
        assert "source_total" in columns
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0004_upgrade_fills_camera_sol_counts_from_existing_photos(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0003")
        asyncio.run(_insert_pre_0004_photos(migrated_db))
        command.upgrade(alembic_cfg, "0004")

        rows = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT s.sol, c.code, s.total_photos, s.full_photos "
                "FROM camera_sol_counts s JOIN cameras c ON c.id = s.camera_id "
                "WHERE s.rover_id = 'curiosity' ORDER BY s.sol, c.code",
            )
        )
        assert [(r["sol"], r["code"], r["total_photos"], r["full_photos"]) for r in rows] == [
            (50, "FHAZ", 3, 2),
            (50, "NAVCAM", 1, 0),  # no full frames: row kept, full_photos 0
            (51, "NAVCAM", 1, 1),
        ]
        # Cameras without photos get no rows; nothing invented for other rovers.
        others = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT count(*) AS n FROM camera_sol_counts WHERE rover_id <> 'curiosity'",
            )
        )
        assert others == [{"n": 0}]
        # sol_source_counts cannot be derived from photos: it starts empty.
        assert asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM sol_source_counts")) == [
            {"n": 0}
        ]
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(_execute(migrated_db, "DELETE FROM photos WHERE source_id LIKE 'pre0004-%'"))
        asyncio.run(
            _execute(
                migrated_db,
                "DO $$ BEGIN IF to_regclass('public.camera_sol_counts') IS NOT NULL THEN "
                "DELETE FROM camera_sol_counts WHERE rover_id = 'curiosity' AND sol IN (50, 51); "
                "END IF; END $$",
            )
        )


# --- 0005: photos (rover_id, sample_type, sol DESC, taken_at DESC, id DESC) index ----------

SAMPLE_TYPE_INDEX_COLUMNS = "(rover_id, sample_type, sol DESC, taken_at DESC, id DESC)"


def _sample_type_indexdefs(url: str) -> list[str]:
    rows = asyncio.run(
        _fetch(
            url,
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'photos'",
        )
    )
    return [r["indexdef"] for r in rows if SAMPLE_TYPE_INDEX_COLUMNS in r["indexdef"]]


def test_migration_0005_downgrade_drops_sample_type_index_and_upgrade_recreates_it(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        assert len(_sample_type_indexdefs(migrated_db)) == 1
        command.downgrade(alembic_cfg, "0004")
        assert _sample_type_indexdefs(migrated_db) == []
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0004"]
        command.upgrade(alembic_cfg, "0005")
        defs = _sample_type_indexdefs(migrated_db)
        assert len(defs) == 1
        assert "WHERE" not in defs[0]
    finally:
        command.upgrade(alembic_cfg, "head")
