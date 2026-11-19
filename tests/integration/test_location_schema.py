"""Schema of migration 0006 (docs/proposals/stage-11-locations.md section 2)."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _all(session: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (await session.execute(text(sql), params)).mappings()]


@pytest.mark.parametrize(
    ("table", "primary_key"),
    [
        ("rover_waypoints", ["rover_id", "site", "drive"]),
        ("rover_traverse_segments", ["rover_id", "seq"]),
        ("location_datasets", ["rover_id", "kind"]),
    ],
)
async def test_location_table_primary_key_matches_contract(
    db_session: AsyncSession, table: str, primary_key: list[str]
) -> None:
    rows = await _all(
        db_session,
        "SELECT a.attname AS name FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary "
        "ORDER BY array_position(i.indkey::int2[], a.attnum)",
        t=f"public.{table}",
    )
    assert [row["name"] for row in rows] == primary_key


@pytest.mark.parametrize(
    ("table", "column", "data_type", "nullable"),
    [
        ("rover_waypoints", "sol", "integer", "NO"),
        ("rover_waypoints", "site", "integer", "NO"),
        ("rover_waypoints", "lat", "double precision", "NO"),
        ("rover_waypoints", "lon", "double precision", "NO"),
        ("rover_waypoints", "elev_geoid", "double precision", "YES"),
        ("rover_waypoints", "dist_total_m", "double precision", "YES"),
        ("rover_traverse_segments", "sol", "integer", "YES"),
        ("rover_traverse_segments", "length_m", "double precision", "YES"),
        ("rover_traverse_segments", "coordinates", "jsonb", "NO"),
        ("location_datasets", "succeeded_at", "timestamp with time zone", "YES"),
        ("location_datasets", "changed_at", "timestamp with time zone", "YES"),
        ("location_datasets", "checked_at", "timestamp with time zone", "YES"),
        ("location_datasets", "etag", "text", "YES"),
        ("location_datasets", "content_sha256", "text", "YES"),
        ("location_datasets", "items", "integer", "NO"),
        ("location_datasets", "dropped", "integer", "NO"),
        ("location_datasets", "max_sol", "integer", "YES"),
        ("location_datasets", "last_error", "text", "YES"),
        ("location_datasets", "created_at", "timestamp with time zone", "NO"),
    ],
    ids=lambda v: str(v),
)
async def test_location_column_type_and_nullability(
    db_session: AsyncSession, table: str, column: str, data_type: str, nullable: str
) -> None:
    [row] = await _all(
        db_session,
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c",
        t=table,
        c=column,
    )
    assert (row["data_type"], row["is_nullable"]) == (data_type, nullable)


async def test_location_waypoints_index_by_rover_and_sol_exists(db_session: AsyncSession) -> None:
    rows = await _all(
        db_session,
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
        "AND tablename = 'rover_waypoints' AND indexname = 'ix_rover_waypoints_rover_id_sol'",
    )
    assert len(rows) == 1
    assert "(rover_id, sol, site, drive)" in rows[0]["indexdef"]


async def test_location_datasets_kind_check_rejects_unknown_value(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(DBAPIError):
        await db_session.execute(
            text("INSERT INTO location_datasets (rover_id, kind) VALUES ('perseverance', 'bogus')")
        )


async def test_location_datasets_counters_default_to_zero(db_session: AsyncSession) -> None:
    await db_session.execute(
        text("INSERT INTO location_datasets (rover_id, kind) VALUES ('perseverance', 'waypoints')")
    )
    [row] = await _all(db_session, "SELECT items, dropped FROM location_datasets")
    assert (row["items"], row["dropped"]) == (0, 0)


@pytest.mark.parametrize(
    "table", ["rover_waypoints", "rover_traverse_segments", "location_datasets"]
)
async def test_location_table_rover_id_is_a_foreign_key(
    db_session: AsyncSession, table: str
) -> None:
    rows = await _all(
        db_session,
        "SELECT 1 FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'f' "
        "AND confrelid = 'public.rovers'::regclass",
        t=f"public.{table}",
    )
    assert rows, f"{table}.rover_id must reference rovers"
