"""Index photos (rover_id, taken_at DESC, id DESC) for sort=taken_at and date ranges (18B).

Serves the v2 ``sort=-taken_at`` / ``sort=taken_at`` lists and ``date_min``/``date_max`` ranges
(the latter through a redundant ``taken_at`` bound). Built and dropped CONCURRENTLY outside the
migration transaction, so upserts into photos are not blocked. If the build fails it leaves an
INVALID index: drop it (``DROP INDEX CONCURRENTLY ix_photos_rover_id_taken_at``) and upgrade
again.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_photos_rover_id_taken_at"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "photos",
            ["rover_id", sa.text("taken_at DESC"), sa.text("id DESC")],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX_NAME, table_name="photos", postgresql_concurrently=True)
