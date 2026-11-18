"""Index photos by (rover_id, sample_type, sol DESC, taken_at DESC, id DESC).

Serves v2 ``sample_type`` filters without ``sol``: the newest-first list and the count
(Index Only Scan). Built and dropped CONCURRENTLY outside the migration transaction, so
upserts into photos are not blocked. If the build fails it leaves an INVALID index: drop it
(``DROP INDEX CONCURRENTLY ix_photos_rover_id_sample_type_sol``) and upgrade again.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_photos_rover_id_sample_type_sol"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "photos",
            [
                "rover_id",
                "sample_type",
                sa.text("sol DESC"),
                sa.text("taken_at DESC"),
                sa.text("id DESC"),
            ],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(INDEX_NAME, table_name="photos", postgresql_concurrently=True)
