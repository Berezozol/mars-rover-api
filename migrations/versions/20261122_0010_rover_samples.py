"""rover_samples and sample_datasets (stage 13).

No data is seeded: the tables are filled by the samples sync (CLI ``samples``). The downgrade
drops both; one ``samples`` run restores the data.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rover_samples",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("number", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("feature_name", sa.Text(), nullable=True),
        sa.Column("sampling_location", sa.Text(), nullable=True),
        sa.Column("current_location", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("sample_type", sa.Text(), nullable=True),
        sa.Column("sample_type_label", sa.Text(), nullable=True),
        sa.Column("rock_type", sa.Text(), nullable=True),
        sa.Column("sealed_on", sa.Date(), nullable=True),
        sa.Column("sol", sa.Integer(), nullable=True),
        sa.Column("height_cm", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("height_note", sa.Text(), nullable=True),
        sa.Column("dossier_url", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "sample_type IN ('rock_core', 'regolith', 'atmospheric')",
            name=op.f("ck_rover_samples_sample_type"),
        ),
        sa.CheckConstraint("status IN ('on_rover', 'depot')", name=op.f("ck_rover_samples_status")),
        sa.CheckConstraint("number > 0", name=op.f("ck_rover_samples_number_positive")),
        sa.CheckConstraint("sol >= 0", name=op.f("ck_rover_samples_sol_non_negative")),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_rover_samples_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "number", name=op.f("pk_rover_samples")),
    )
    op.create_table(
        "sample_datasets",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("items", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_sample_datasets_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", name=op.f("pk_sample_datasets")),
    )


def downgrade() -> None:
    op.drop_table("sample_datasets")
    op.drop_table("rover_samples")
