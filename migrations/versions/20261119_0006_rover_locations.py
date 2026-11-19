"""rover_waypoints, rover_traverse_segments and location_datasets (stage 11).

No data is seeded: the tables are filled by the location sync.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "location_datasets",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("items", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dropped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_sol", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('waypoints', 'traverse')", name=op.f("ck_location_datasets_kind")
        ),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_location_datasets_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "kind", name=op.f("pk_location_datasets")),
    )
    op.create_table(
        "rover_traverse_segments",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("sol", sa.Integer(), nullable=True),
        sa.Column("length_m", sa.Float(), nullable=True),
        sa.Column("coordinates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_rover_traverse_segments_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "seq", name=op.f("pk_rover_traverse_segments")),
    )
    op.create_table(
        "rover_waypoints",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("site", sa.Integer(), nullable=False),
        sa.Column("drive", sa.Integer(), nullable=False),
        sa.Column("sol", sa.Integer(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("elev_geoid", sa.Float(), nullable=True),
        sa.Column("easting", sa.Float(), nullable=True),
        sa.Column("northing", sa.Float(), nullable=True),
        sa.Column("roll", sa.Float(), nullable=True),
        sa.Column("pitch", sa.Float(), nullable=True),
        sa.Column("yaw", sa.Float(), nullable=True),
        sa.Column("tilt", sa.Float(), nullable=True),
        sa.Column("dist_m", sa.Float(), nullable=True),
        sa.Column("dist_total_m", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_rover_waypoints_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "site", "drive", name=op.f("pk_rover_waypoints")),
    )
    op.create_index(
        "ix_rover_waypoints_rover_id_sol",
        "rover_waypoints",
        ["rover_id", "sol", "site", "drive"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rover_waypoints_rover_id_sol", table_name="rover_waypoints")
    op.drop_table("rover_waypoints")
    op.drop_table("rover_traverse_segments")
    op.drop_table("location_datasets")
