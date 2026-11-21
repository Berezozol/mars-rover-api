"""Columns photos.local_time_s, width, height and their partial indexes (stage 18B-2).

The columns hold values the API already derives from ``extra`` (local solar time as the second
of the day, frame size in pixels) so the v2 filters ``local_time_min/max``, ``golden_hour`` and
``min_width/min_height`` can use indexes. They are NULL until written: new and re-synced photos
fill them on upsert, history is filled by ``python -m app.cli backfill-meta``. Responses still
read ``extra``, so nothing visible changes between the migration and the backfill.

ADD COLUMN without a default only touches the catalog. The indexes are built and dropped
CONCURRENTLY outside the migration transaction (on empty columns they are tiny; the backfill
maintains them). On a partial failure drop every 0013 index that exists, valid or INVALID
(``DROP INDEX CONCURRENTLY IF EXISTS ix_photos_rover_id_local_time, ix_photos_rover_id_width,
ix_photos_rover_id_height``), then upgrade again.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_TIME_INDEX = "ix_photos_rover_id_local_time"
WIDTH_INDEX = "ix_photos_rover_id_width"
HEIGHT_INDEX = "ix_photos_rover_id_height"


def upgrade() -> None:
    # Do not queue behind a long read while taking the brief ACCESS EXCLUSIVE lock.
    op.execute("SET LOCAL lock_timeout = '5s'")
    # IF NOT EXISTS: the columns are committed before the concurrent index builds, so a rerun
    # after a failed build must not stumble over them.
    op.execute(
        "ALTER TABLE photos ADD COLUMN IF NOT EXISTS local_time_s integer, "
        "ADD COLUMN IF NOT EXISTS width smallint, ADD COLUMN IF NOT EXISTS height smallint"
    )
    with op.get_context().autocommit_block():
        op.create_index(
            LOCAL_TIME_INDEX,
            "photos",
            ["rover_id", "local_time_s"],
            unique=False,
            postgresql_where=sa.text("local_time_s IS NOT NULL"),
            postgresql_concurrently=True,
        )
        op.create_index(
            WIDTH_INDEX,
            "photos",
            ["rover_id", "width"],
            unique=False,
            postgresql_where=sa.text("width IS NOT NULL"),
            postgresql_concurrently=True,
        )
        # min_height alone would otherwise scan the whole table (about 3 s on 1.7M rows).
        op.create_index(
            HEIGHT_INDEX,
            "photos",
            ["rover_id", "height"],
            unique=False,
            postgresql_where=sa.text("height IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(HEIGHT_INDEX, table_name="photos", postgresql_concurrently=True)
        op.drop_index(WIDTH_INDEX, table_name="photos", postgresql_concurrently=True)
        op.drop_index(LOCAL_TIME_INDEX, table_name="photos", postgresql_concurrently=True)
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE photos DROP COLUMN IF EXISTS height, DROP COLUMN IF EXISTS width, "
        "DROP COLUMN IF EXISTS local_time_s"
    )
