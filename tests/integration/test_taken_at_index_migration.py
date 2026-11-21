"""Migration 0012 (`ix_photos_rover_id_taken_at`, stage 18B-1, AC-12): up, down, up, no drift.

Same safety rules as test_feed_migration.py (module marker, head restored in `finally`).
Spec: docs/proposals/stage-18b-filters.md section 6: index `(rover_id, taken_at DESC, id DESC)`
created and dropped CONCURRENTLY; nothing else changes.
"""

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from tests.integration.test_migrations_roundtrip import _db_state, _fetch

pytestmark = [pytest.mark.integration, pytest.mark.migrations_roundtrip]

INDEX = "ix_photos_rover_id_taken_at"
_INDEX_SQL = f"SELECT indexdef FROM pg_indexes WHERE tablename = 'photos' AND indexname = '{INDEX}'"


def test_ac12_migration_0012_follows_0011(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0012")

    assert revision is not None
    assert revision.down_revision == "0011"


def test_ac12_up_creates_the_index_on_rover_taken_at_desc_id_desc(migrated_db: str) -> None:
    rows = asyncio.run(_fetch(migrated_db, _INDEX_SQL))

    [row] = rows
    definition = " ".join(row["indexdef"].split())
    assert "(rover_id, taken_at DESC, id DESC)" in definition
    assert "WHERE" not in definition  # not partial: the cross-rover list needs every row


def test_ac12_the_index_is_valid(migrated_db: str) -> None:
    """A failed CREATE INDEX CONCURRENTLY leaves an invalid index behind."""
    rows = asyncio.run(
        _fetch(
            migrated_db,
            "SELECT i.indisvalid, i.indisready FROM pg_index i "
            f"JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = '{INDEX}'",
        )
    )

    assert rows == [{"indisvalid": True, "indisready": True}]


def test_ac12_down_drops_only_the_index_and_up_restores_it(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0011")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0011"]
        assert not asyncio.run(_fetch(migrated_db, _INDEX_SQL))
        # The older photo indexes and the feed index are untouched.
        remaining = asyncio.run(
            _fetch(migrated_db, "SELECT indexname FROM pg_indexes WHERE tablename = 'photos'")
        )
        names = {row["indexname"] for row in remaining}
        assert "ix_photos_feed" in names
        assert len(names) >= 6

        command.upgrade(alembic_cfg, "0012")
        assert asyncio.run(_db_state(migrated_db))["versions"] == ["0012"]
        assert asyncio.run(_fetch(migrated_db, _INDEX_SQL))
    finally:
        command.upgrade(alembic_cfg, "head")


def test_ac12_alembic_check_reports_no_drift(alembic_cfg: AlembicConfig) -> None:
    """Models and migrations agree (the index is declared in app/db/models.py)."""
    command.check(alembic_cfg)
