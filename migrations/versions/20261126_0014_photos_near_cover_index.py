"""Covering index for the near search, replacing ix_photos_rover_id_site_drive (stage 18B-3).

``ix_photos_near_cover`` is (rover_id, site, drive) INCLUDE (sol, taken_at, id) with the same
predicate as the index it replaces (``site IS NOT NULL``), so every query that used the old
index (site, site range, drive range, stop lookups) can use the new one. The near search reads
the sort keys of the photos of each stop straight from it (Index Only Scan, no table access),
which turned a cold-cache query of radius 50 km from a 5 s timeout into about 0.2 s. The index
is larger (about 79 MB against 31 MB on 1.7M rows).

Index-only scans need an up-to-date visibility map: run ``VACUUM (ANALYZE, PARALLEL 0) photos``
after the migration (and autovacuum keeps it fresh afterwards).

The new index is built before the old one is dropped, both CONCURRENTLY outside the migration
transaction, with ``lock_timeout = 5s`` (reset at the end of each block). Run it with the sync
and the scheduler paused: CREATE INDEX CONCURRENTLY waits for older transactions, and with the
lock timeout it can fail late, leaving an INVALID index. The migration is safe to rerun after
any partial failure: an INVALID leftover is dropped, a VALID index is never rebuilt or dropped,
and the old index is dropped only after the new one is valid, so the table is never left without
an index. Downgrade mirrors this.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_INDEX = "ix_photos_rover_id_site_drive"
NEW_INDEX = "ix_photos_near_cover"
WHERE = sa.text("site IS NOT NULL")


def _index_state(name: str) -> bool | None:
    """None if the index does not exist, else whether it is VALID."""
    row = op.get_bind().execute(
        sa.text("SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = to_regclass(:name)"),
        {"name": name},
    )
    return row.scalar_one_or_none()


def _rebuild(name: str, other: str, *, include: list[str] | None) -> None:
    """Make ``name`` valid (build only if missing or INVALID), then drop ``other`` if present."""
    with op.get_context().autocommit_block():
        try:
            op.execute("SET lock_timeout = '5s'")
            state = _index_state(name)
            if state is False:
                op.drop_index(name, table_name="photos", postgresql_concurrently=True)
            if state is not True:
                op.create_index(
                    name,
                    "photos",
                    ["rover_id", "site", "drive"],
                    unique=False,
                    postgresql_include=include,
                    postgresql_where=WHERE,
                    postgresql_concurrently=True,
                )
            op.drop_index(other, table_name="photos", postgresql_concurrently=True, if_exists=True)
        finally:
            op.execute("RESET lock_timeout")


def upgrade() -> None:
    _rebuild(NEW_INDEX, OLD_INDEX, include=["sol", "taken_at", "id"])


def downgrade() -> None:
    _rebuild(OLD_INDEX, NEW_INDEX, include=None)
