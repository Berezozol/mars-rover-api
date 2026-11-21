"""Add sample_type and camera_id to the ix_photos_near_cover INCLUDE list (overload protection).

``ix_photos_near_cover`` becomes (rover_id, site, drive) INCLUDE (sol, taken_at, id, sample_type,
camera_id) WHERE site IS NOT NULL (same key and predicate as before). Counts by stop and the near
search with a ``sample_type`` or camera filter then run as Index Only Scans instead of reading
the table: the cold-cache count of ``site BETWEEN .. AND sample_type = 'full'`` went from 2.4 s to
about 80 ms. The index grows by about 23 MB.

Index-only scans need an up-to-date visibility map: run ``VACUUM (ANALYZE, PARALLEL 0) photos``
after the migration (and autovacuum keeps it fresh afterwards).

The name stays the same, so the swap is: build ``ix_photos_near_cover_swap`` with the new
definition, drop ``ix_photos_near_cover``, rename the swap index. All steps run CONCURRENTLY
(the rename is a quick catalog change) outside the migration transaction, with
``lock_timeout = 5s`` (reset at the end). Run it with the sync and the scheduler paused:
CREATE INDEX CONCURRENTLY waits for older transactions, and with the lock timeout it can fail
late, leaving an INVALID index.

The migration is safe to rerun after any partial failure. The state is read from the catalog on
every run: nothing to do when ``ix_photos_near_cover`` is VALID and already has the wanted
INCLUDE list; otherwise the swap index is built if it is missing or INVALID (a VALID one is
never rebuilt or dropped unless its INCLUDE count is wrong for the direction), the old index is
dropped only after the swap index is valid (the table is never left without an index), and the
swap index is renamed only if the main name is free.
Downgrade mirrors this with the old INCLUDE list.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_photos_near_cover"
SWAP_INDEX = "ix_photos_near_cover_swap"
WHERE = sa.text("site IS NOT NULL")


def _index_state(name: str) -> tuple[bool, int] | None:
    """None if the index does not exist, else (is VALID, number of INCLUDE columns)."""
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT i.indisvalid, i.indnatts - i.indnkeyatts FROM pg_index i "
                "WHERE i.indexrelid = to_regclass(:name)"
            ),
            {"name": name},
        )
        .first()
    )
    return None if row is None else (bool(row[0]), int(row[1]))


def _swap(include: list[str]) -> None:
    """Make ``INDEX`` a valid index with exactly the ``include`` columns, rerun-safe."""
    with op.get_context().autocommit_block():
        try:
            op.execute("SET lock_timeout = '5s'")
            main = _index_state(INDEX)
            if main is not None and main == (True, len(include)):
                # Done (a rerun after the rename): only a leftover swap index may remain.
                op.drop_index(
                    SWAP_INDEX, table_name="photos", postgresql_concurrently=True, if_exists=True
                )
                return
            swap = _index_state(SWAP_INDEX)
            # A VALID swap index is kept only if it has the wanted INCLUDE list (it may be a
            # leftover of the opposite direction).
            reusable = swap == (True, len(include))
            if swap is not None and not reusable:
                op.drop_index(SWAP_INDEX, table_name="photos", postgresql_concurrently=True)
            if not reusable:
                op.create_index(
                    SWAP_INDEX,
                    "photos",
                    ["rover_id", "site", "drive"],
                    unique=False,
                    postgresql_include=include,
                    postgresql_where=WHERE,
                    postgresql_concurrently=True,
                )
            op.drop_index(INDEX, table_name="photos", postgresql_concurrently=True, if_exists=True)
            op.execute(f"ALTER INDEX {SWAP_INDEX} RENAME TO {INDEX}")
        finally:
            op.execute("RESET lock_timeout")


def upgrade() -> None:
    _swap(["sol", "taken_at", "id", "sample_type", "camera_id"])


def downgrade() -> None:
    _swap(["sol", "taken_at", "id"])
