"""Partial index photos (rover_id, site, drive) WHERE site IS NOT NULL.

Serves the v2 ``/photos`` filters ``site``, ``drive_from`` and ``drive_to``. Built and dropped
CONCURRENTLY outside the migration transaction, so upserts into photos are not blocked. If the
build fails it leaves an INVALID index: drop it (``DROP INDEX CONCURRENTLY
ix_photos_rover_id_site_drive``) and upgrade again.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_photos_rover_id_site_drive"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "photos",
            ["rover_id", "site", "drive"],
            unique=False,
            postgresql_where=sa.text("site IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX_NAME, table_name="photos", postgresql_concurrently=True)
