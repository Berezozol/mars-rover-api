"""Migration 0011 (`photos.feed_at`, stage 16): down, up, data backfill, index.

Same safety rules as test_migrations_roundtrip.py (module marker, restore head and clean the
data in `finally`). Spec: docs/proposals/stage-16-rss.md section 5:

- up: `feed_at timestamptz NULL` (no default), partial index `ix_photos_feed`;
- the last 30 days are filled with `created_at` for photos stored inside an `incremental`
  sync run of their rover (`started_at <= created_at <= coalesce(finished_at, now())`);
  everything else (outside any run, inside a backfill run, older than 30 days, another rover's
  run) stays NULL;
- down: the column and the index are gone, other data is kept.
"""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_migrations_roundtrip import _db_state, _execute, _fetch

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]


Statement = tuple[Any, dict[str, Any]]
H = timedelta(hours=1)
D = timedelta(days=1)

_PHOTO_SQL = text(
    "INSERT INTO photos (rover_id, camera_id, source_id, sol, earth_date, taken_at, "
    "sample_type, img_full, created_at) VALUES (:rover, "
    "(SELECT id FROM cameras WHERE rover_id = :rover AND code = :camera), :sid, 100, "
    "DATE '2024-01-01', TIMESTAMPTZ '2024-01-01 00:00:00+00', 'full', "
    "'https://images.example.test/' || :sid || '.png', now() + CAST(:created AS interval))"
)
_RUN_SQL = text(
    "INSERT INTO sync_runs (rover_id, mode, status, started_at, finished_at) VALUES "
    "(:rover, :mode, :status, now() + CAST(:started AS interval), "
    "CASE WHEN CAST(:finished AS interval) IS NULL THEN NULL "
    "ELSE now() + CAST(:finished AS interval) END)"
)


def _photo(source_id: str, rover: str, camera: str, created: timedelta) -> Statement:
    """A photo created `created` from now (negative = in the past)."""
    params = {"sid": source_id, "rover": rover, "camera": camera, "created": created}
    return _PHOTO_SQL, params


def _run(
    rover: str, mode: str, status: str, started: timedelta, finished: timedelta | None
) -> Statement:
    params = {
        "rover": rover,
        "mode": mode,
        "status": status,
        "started": started,
        "finished": finished,
    }
    return _RUN_SQL, params


async def _execute_all(url: str, statements: list[Statement]) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        for statement, params in statements:
            async with engine.begin() as conn:  # one transaction each: now() moves on
                await conn.execute(statement, params)
    finally:
        await engine.dispose()


def _seed_0010_data(url: str) -> None:
    statements = [
        # Incremental run of perseverance 3 days ago, one hour long.
        _run("perseverance", "incremental", "success", -3 * D, -3 * D + H),
        _photo("in-run", "perseverance", "NAVCAM_LEFT", -3 * D + timedelta(minutes=30)),
        _photo("run-start", "perseverance", "NAVCAM_LEFT", -3 * D + timedelta(seconds=1)),
        _photo("outside-run", "perseverance", "NAVCAM_LEFT", -2 * D),
        # Another rover's photo stored at the same time as perseverance's incremental run.
        _photo("other-rover", "curiosity", "FHAZ", -3 * D + timedelta(minutes=30)),
        # Backfill run: its photos are not "new".
        _run("perseverance", "backfill", "success", -5 * D, -5 * D + H),
        _photo("in-backfill", "perseverance", "NAVCAM_LEFT", -5 * D + timedelta(minutes=10)),
        # Incremental run outside the 30-day window.
        _run("perseverance", "incremental", "success", -40 * D, -40 * D + H),
        _photo("too-old", "perseverance", "NAVCAM_LEFT", -40 * D + timedelta(minutes=10)),
        # An unfinished incremental run (finished_at NULL) counts up to now().
        _run("curiosity", "incremental", "running", -D, None),
        _photo("in-unfinished-run", "curiosity", "FHAZ", -D + H),
    ]
    asyncio.run(_execute_all(url, statements))


def _cleanup(url: str) -> None:
    asyncio.run(_execute(url, "TRUNCATE photos, sync_runs RESTART IDENTITY"))


def test_migration_0011_is_the_head_after_0010(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0011")

    assert revision is not None
    assert revision.down_revision == "0010"


def test_migration_0011_up_adds_a_nullable_timestamptz_column_and_the_partial_index(
    migrated_db: str,
) -> None:
    columns = asyncio.run(
        _fetch(
            migrated_db,
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'photos' AND column_name = 'feed_at'",
        )
    )
    indexes = asyncio.run(
        _fetch(
            migrated_db,
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'photos' AND indexname = 'ix_photos_feed'",
        )
    )

    assert columns == [
        {"data_type": "timestamp with time zone", "is_nullable": "YES", "column_default": None}
    ]
    [index] = indexes
    definition = index["indexdef"]
    assert "(rover_id, feed_at DESC, id DESC)" in definition
    for included in ("camera_id", "sample_type", "sol", "taken_at"):
        assert included in definition.split("INCLUDE", 1)[1].split("WHERE")[0], included
    assert "feed_at IS NOT NULL" in definition


def test_migration_0011_down_drops_the_column_and_the_index_and_up_restores_them(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0010")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0010"]
        assert not asyncio.run(
            _fetch(
                migrated_db,
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'photos' AND column_name = 'feed_at'",
            )
        )
        assert not asyncio.run(
            _fetch(migrated_db, "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_photos_feed'")
        )
        # Nothing else is touched.
        assert state["tables"] >= {"photos", "rovers", "cameras", "rover_samples", "sync_runs"}

        command.upgrade(alembic_cfg, "0011")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0011"]
        assert asyncio.run(
            _fetch(migrated_db, "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_photos_feed'")
        )
    finally:
        command.upgrade(alembic_cfg, "head")


def test_migration_0011_down_keeps_photos_and_other_columns(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0010")
        _seed_0010_data(migrated_db)
        command.upgrade(alembic_cfg, "0011")
        command.downgrade(alembic_cfg, "0010")

        rows = asyncio.run(_fetch(migrated_db, "SELECT count(*) AS n FROM photos"))
        assert rows[0]["n"] == 7
    finally:
        command.upgrade(alembic_cfg, "head")
        _cleanup(migrated_db)


def test_migration_0011_up_fills_feed_at_from_incremental_sync_runs_of_the_last_30_days(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0010")
        _seed_0010_data(migrated_db)

        command.upgrade(alembic_cfg, "0011")

        rows = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT source_id, feed_at, created_at, feed_at = created_at AS same "
                "FROM photos ORDER BY source_id",
            )
        )
        feed = {row["source_id"]: row for row in rows}
        filled = {"in-run", "run-start", "in-unfinished-run"}
        empty = {"outside-run", "other-rover", "in-backfill", "too-old"}
        assert set(feed) == filled | empty
        for source_id in filled:
            assert feed[source_id]["feed_at"] is not None, source_id
            assert feed[source_id]["same"] is True, source_id
        for source_id in empty:
            assert feed[source_id]["feed_at"] is None, source_id
    finally:
        command.upgrade(alembic_cfg, "head")
        _cleanup(migrated_db)
