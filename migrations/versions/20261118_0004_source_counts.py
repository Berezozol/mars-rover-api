"""camera_sol_counts, sol_source_counts and sync_runs.source_total.

camera_sol_counts is filled from the existing photos.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "camera_sol_counts",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("sol", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("total_photos", sa.Integer(), nullable=False),
        sa.Column("full_photos", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.id"], name=op.f("fk_camera_sol_counts_camera_id_cameras")
        ),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_camera_sol_counts_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "sol", "camera_id", name=op.f("pk_camera_sol_counts")),
    )
    op.create_table(
        "sol_source_counts",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("sol", sa.Integer(), nullable=False),
        sa.Column("received", sa.Integer(), nullable=False),
        sa.Column("dropped", sa.Integer(), nullable=False),
        sa.Column("source_total", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_sol_source_counts_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "sol", name=op.f("pk_sol_source_counts")),
    )
    op.add_column("sync_runs", sa.Column("source_total", sa.BigInteger(), nullable=True))
    op.execute(
        """
        INSERT INTO camera_sol_counts (rover_id, sol, camera_id, total_photos, full_photos)
        SELECT rover_id, sol, camera_id, count(*), count(*) FILTER (WHERE sample_type = 'full')
        FROM photos
        GROUP BY rover_id, sol, camera_id
        """
    )


def downgrade() -> None:
    op.drop_column("sync_runs", "source_total")
    op.drop_table("sol_source_counts")
    op.drop_table("camera_sol_counts")
