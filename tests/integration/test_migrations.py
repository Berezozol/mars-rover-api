"""Alembic migrations: head revision, schema shape, no model/migration drift.

Downgrade/upgrade round trips live in test_migrations_roundtrip.py.
"""

import re
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_COLUMNS = {
    "rovers": {
        "id", "legacy_id", "name", "mission", "launch_date", "landing_date", "status",
        "end_date", "max_sol", "max_date", "total_photos", "last_synced_at",
        "full_max_sol", "full_max_date", "full_total_photos",
    },
    "cameras": {"id", "rover_id", "code", "name", "sort_order"},
    "photos": {
        "id", "rover_id", "camera_id", "source_id", "sol", "earth_date", "taken_at",
        "received_at", "sample_type", "img_full", "img_small", "img_medium", "img_large",
        "title", "caption", "credit", "source_link", "site", "drive", "extra",
        "created_at", "updated_at", "feed_at",
    },
    "sol_summaries": {
        "rover_id", "sol", "earth_date", "total_photos", "cameras", "full_photos", "full_cameras",
    },
    "sync_runs": {
        "id", "rover_id", "mode", "from_sol", "to_sol", "status", "started_at", "finished_at",
        "photos_upserted", "error", "failed_sols", "source_total",
    },
    "camera_sol_counts": {"rover_id", "sol", "camera_id", "total_photos", "full_photos"},
    "sol_source_counts": {"rover_id", "sol", "received", "dropped", "source_total", "fetched_at"},
    "rover_waypoints": {
        "rover_id", "site", "drive", "sol", "lat", "lon", "elev_geoid", "easting", "northing",
        "roll", "pitch", "yaw", "tilt", "dist_m", "dist_total_m",
    },
    "ingenuity_flights": {
        "flight", "sol", "date", "distance_m", "max_altitude_m", "max_groundspeed_mps",
        "duration_s", "route_from", "route_to",
    },
    "rover_traverse_segments": {"rover_id", "seq", "sol", "length_m", "coordinates"},
    "location_datasets": {
        "rover_id", "kind", "checked_at", "succeeded_at", "changed_at", "etag", "last_modified",
        "content_sha256", "items", "dropped", "max_sol", "last_error", "created_at",
    },
}  # fmt: skip


def _head(alembic_cfg: AlembicConfig) -> str:
    head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    assert head is not None
    return head


def test_migrations_head_revision_is_at_least_0010(alembic_cfg: AlembicConfig) -> None:
    assert _head(alembic_cfg) >= "0010"


def test_migrations_single_head(alembic_cfg: AlembicConfig) -> None:
    assert len(ScriptDirectory.from_config(alembic_cfg).get_heads()) == 1


async def test_migrations_database_is_at_head_revision(
    db_session: AsyncSession, alembic_cfg: AlembicConfig
) -> None:
    result = await db_session.execute(text("SELECT version_num FROM alembic_version"))
    assert result.scalars().all() == [_head(alembic_cfg)]


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
async def test_schema_table_has_contract_columns(db_session: AsyncSession, table: str) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    )
    assert set(result.scalars()) == EXPECTED_COLUMNS[table]


@pytest.mark.parametrize(
    ("table", "column", "data_type"),
    [
        ("rovers", "id", "text"),
        ("rovers", "legacy_id", "smallint"),
        ("rovers", "launch_date", "date"),
        ("rovers", "landing_date", "date"),
        ("rovers", "end_date", "date"),
        ("photos", "id", "bigint"),
        ("photos", "earth_date", "date"),
        ("photos", "taken_at", "timestamp with time zone"),
        ("photos", "received_at", "timestamp with time zone"),
        ("photos", "created_at", "timestamp with time zone"),
        ("photos", "feed_at", "timestamp with time zone"),
        ("photos", "updated_at", "timestamp with time zone"),
        ("photos", "extra", "jsonb"),
        ("sol_summaries", "earth_date", "date"),
        ("sol_summaries", "cameras", "ARRAY"),
        ("sol_summaries", "full_cameras", "ARRAY"),
        ("sync_runs", "failed_sols", "ARRAY"),
        ("sync_runs", "source_total", "bigint"),
        ("sol_source_counts", "source_total", "integer"),
        ("sol_source_counts", "fetched_at", "timestamp with time zone"),
    ],
    ids=lambda v: str(v),
)
async def test_schema_column_type_matches_contract(
    db_session: AsyncSession, table: str, column: str, data_type: str
) -> None:
    result = await db_session.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    assert result.scalar_one() == data_type


@pytest.mark.parametrize(
    ("table", "column"),
    [("rovers", "legacy_id"), ("rovers", "name"), ("rovers", "landing_date"),
     ("cameras", "code"), ("cameras", "sort_order"), ("photos", "source_id"),
     ("photos", "sol"), ("photos", "earth_date"), ("photos", "taken_at"),
     ("photos", "sample_type"), ("photos", "img_full"), ("photos", "created_at"),
     ("photos", "updated_at"), ("sync_runs", "failed_sols"),
     ("camera_sol_counts", "total_photos"), ("camera_sol_counts", "full_photos"),
     ("sol_source_counts", "received"), ("sol_source_counts", "dropped"),
     ("sol_source_counts", "fetched_at")],
    ids=lambda v: str(v),
)  # fmt: skip
async def test_schema_required_column_is_not_null(
    db_session: AsyncSession, table: str, column: str
) -> None:
    result = await db_session.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    assert result.scalar_one() == "NO"


async def _unique_column_sets(session: AsyncSession, table: str) -> set[tuple[str, ...]]:
    """Unique constraints, unique indexes and the primary key, as column tuples."""
    conn = await session.connection()

    def collect(sync_conn: Any) -> set[tuple[str, ...]]:
        insp = inspect(sync_conn)
        sets = {tuple(uc["column_names"]) for uc in insp.get_unique_constraints(table)}
        sets |= {tuple(ix["column_names"]) for ix in insp.get_indexes(table) if ix["unique"]}
        sets.add(tuple(insp.get_pk_constraint(table)["constrained_columns"]))
        return sets

    return await conn.run_sync(collect)


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("rovers", ("id",)),
        ("rovers", ("legacy_id",)),
        ("cameras", ("rover_id", "code")),
        ("photos", ("rover_id", "source_id")),
        ("sol_summaries", ("rover_id", "sol")),
        ("camera_sol_counts", ("rover_id", "sol", "camera_id")),
        ("sol_source_counts", ("rover_id", "sol")),
    ],
    ids=lambda v: str(v),
)
async def test_schema_uniqueness_matches_contract(
    db_session: AsyncSession, table: str, columns: tuple[str, ...]
) -> None:
    assert columns in await _unique_column_sets(db_session, table)


async def _photo_indexdefs(session: AsyncSession) -> list[str]:
    result = await session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'photos'")
    )
    return list(result.scalars())


@pytest.mark.parametrize(
    "columns",
    [
        "(rover_id, sol DESC, taken_at DESC, id DESC)",
        "(rover_id, earth_date)",
        "(rover_id, camera_id, sol)",
        "(rover_id, sample_type, sol DESC, taken_at DESC, id DESC)",
    ],
    ids=["sol_taken_at_desc", "earth_date", "camera_sol", "sample_type_sol_desc"],
)
async def test_schema_photos_index_exists(db_session: AsyncSession, columns: str) -> None:
    defs = await _photo_indexdefs(db_session)
    assert any(columns in d and "WHERE" not in d for d in defs), defs


async def test_schema_photos_partial_full_frame_index_exists(db_session: AsyncSession) -> None:
    defs = await _photo_indexdefs(db_session)
    partial = [d for d in defs if "(rover_id, sol, camera_id, id)" in d]
    assert len(partial) == 1, defs
    assert "WHERE" in partial[0]
    assert "sample_type" in partial[0]
    assert "'full'" in partial[0]


async def test_schema_models_match_migrations_no_autogenerate_diff(
    db_session: AsyncSession,
) -> None:
    from app.db import models  # noqa: F401  (registers models on Base.metadata)
    from app.db.base import Base

    conn = await db_session.connection()

    def diff(sync_conn: Any) -> list[Any]:
        context = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    assert await conn.run_sync(diff) == []


async def test_schema_sync_runs_failed_sols_is_integer_array_default_empty(
    db_session: AsyncSession,
) -> None:
    element_type = await db_session.scalar(
        text(
            "SELECT e.data_type FROM information_schema.columns c "
            "JOIN information_schema.element_types e "
            "ON e.object_catalog = c.table_catalog AND e.object_schema = c.table_schema "
            "AND e.object_name = c.table_name AND e.object_type = 'TABLE' "
            "AND e.collection_type_identifier = c.dtd_identifier "
            "WHERE c.table_schema = 'public' AND c.table_name = 'sync_runs' "
            "AND c.column_name = 'failed_sols'"
        )
    )
    assert element_type == "integer"
    run_id = await db_session.scalar(
        text(
            "INSERT INTO sync_runs (rover_id, mode, status) "
            "VALUES ('curiosity', 'backfill', 'running') RETURNING id"
        )
    )
    stored = await db_session.scalar(
        text("SELECT failed_sols FROM sync_runs WHERE id = :id"), {"id": run_id}
    )
    assert stored == []


@pytest.mark.parametrize(
    ("table", "column"),
    [("sync_runs", "source_total"), ("sol_source_counts", "source_total")],
    ids=lambda v: str(v),
)
async def test_schema_source_total_is_nullable(
    db_session: AsyncSession, table: str, column: str
) -> None:
    result = await db_session.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    assert result.scalar_one() == "YES"


@pytest.mark.parametrize("table", ["camera_sol_counts", "sol_source_counts"])
async def test_schema_count_table_primary_key_matches_contract(
    db_session: AsyncSession, table: str
) -> None:
    conn = await db_session.connection()

    def pk(sync_conn: Any) -> list[str]:
        return inspect(sync_conn).get_pk_constraint(table)["constrained_columns"]

    expected = {
        "camera_sol_counts": ["rover_id", "sol", "camera_id"],
        "sol_source_counts": ["rover_id", "sol"],
    }
    assert await conn.run_sync(pk) == expected[table]


# --- 0005: the sample_type index is built without locking photos ----------------------------

MIGRATION_0005 = sorted((ROOT / "migrations" / "versions").glob("*_0005_*.py"))


def test_migration_0005_creates_index_concurrently() -> None:
    assert len(MIGRATION_0005) == 1, MIGRATION_0005
    source = MIGRATION_0005[0].read_text(encoding="utf-8")
    upgrade = source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert re.search(r"postgresql_concurrently\s*=\s*True|CREATE INDEX CONCURRENTLY", upgrade, re.I)
    # CREATE INDEX CONCURRENTLY cannot run inside the migration transaction.
    assert "autocommit_block" in upgrade


async def test_migration_0005_index_is_valid(db_session: AsyncSession) -> None:
    """A failed concurrent build leaves an INVALID index behind; this one must be valid."""
    rows = await db_session.execute(
        text(
            "SELECT i.indisvalid, i.indisready, pg_get_indexdef(i.indexrelid) AS indexdef "
            "FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid "
            "WHERE t.relname = 'photos'"
        )
    )
    matching = [
        r for r in rows if "(rover_id, sample_type, sol DESC, taken_at DESC, id DESC)" in r.indexdef
    ]
    assert len(matching) == 1
    assert matching[0].indisvalid and matching[0].indisready


async def test_migration_0007_end_date_is_nullable_and_seeded_for_mer_only(
    db_session: AsyncSession,
) -> None:
    nullable = await db_session.scalar(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'rovers' AND column_name = 'end_date'"
        )
    )
    rows = await db_session.execute(text("SELECT id, end_date::text FROM rovers ORDER BY id"))

    assert nullable == "YES"
    assert dict(rows.all()) == {
        "curiosity": None,
        "ingenuity": "2024-01-25",  # stage 12 (0008)
        "opportunity": "2019-02-13",
        "perseverance": None,
        "spirit": "2011-05-25",
    }
