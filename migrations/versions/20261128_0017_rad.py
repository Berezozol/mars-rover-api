"""MSL RAD radiation: files of the PDS volume, daily summaries and the state of the volume.

Three new tables (stage 21); no existing table is touched and no data is seeded. The downgrade
drops all three: the data is rebuilt by ``rad sync`` or ``rad import`` of an export file.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rad_daily",
        sa.Column("sol", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("earth_date", sa.Date(), nullable=False),
        sa.Column("dose_b_mean", sa.Float(), nullable=False),
        sa.Column("dose_b_min", sa.Float(), nullable=False),
        sa.Column("dose_b_max", sa.Float(), nullable=False),
        sa.Column("dose_e_mean", sa.Float(), nullable=False),
        sa.Column("dose_e_min", sa.Float(), nullable=False),
        sa.Column("dose_e_max", sa.Float(), nullable=False),
        sa.Column("bu_count", sa.BigInteger(), nullable=False),
        sa.Column("live_s", sa.Float(), nullable=False),
        sa.Column("n_used", sa.Integer(), nullable=False),
        sa.Column("n_dropped_qc", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_dropped_low_rate", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_dropped_invalid", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("qc_bits", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("timing_caveat", sa.Boolean(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("live_s > 0", name=op.f("ck_rad_daily_live_positive")),
        sa.CheckConstraint("n_used > 0", name=op.f("ck_rad_daily_used_positive")),
        sa.PrimaryKeyConstraint("sol", name=op.f("pk_rad_daily")),
    )
    op.create_table(
        "rad_dataset",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checksums_etag", sa.Text(), nullable=True),
        sa.Column("checksums_last_modified", sa.Text(), nullable=True),
        sa.Column("checksums_sha256", sa.Text(), nullable=True),
        sa.Column("timing_etag", sa.Text(), nullable=True),
        sa.Column("timing_last_modified", sa.Text(), nullable=True),
        sa.Column("timing_sha256", sa.Text(), nullable=True),
        sa.Column("files_listed", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_rad_dataset_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rad_dataset")),
    )
    op.create_table(
        "rad_files",
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sol", sa.Integer(), nullable=False),
        sa.Column("md5", sa.CHAR(length=32), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("duplicate_of", sa.Text(), nullable=True),
        sa.Column("dup_check", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("creation_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rdr_version", sa.SmallInteger(), nullable=True),
        sa.Column("blocks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dose_obs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("timing_listed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dup_check IS NULL OR dup_check IN ('tail', 'size')",
            name=op.f("ck_rad_files_dup_check_valid"),
        ),
        sa.CheckConstraint(
            "(md5 IS NULL) = (status IN ('failed', 'md5_mismatch'))",
            name=op.f("ck_rad_files_md5_present"),
        ),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('primary', 'extra')", name=op.f("ck_rad_files_role_valid")
        ),
        sa.CheckConstraint(
            "status IN ('loaded', 'duplicate', 'empty', 'failed', 'md5_mismatch')",
            name=op.f("ck_rad_files_status_valid"),
        ),
        sa.PrimaryKeyConstraint("path", name=op.f("pk_rad_files")),
    )
    op.create_index("ix_rad_files_sol", "rad_files", ["sol"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rad_files_sol", table_name="rad_files")
    op.drop_table("rad_files")
    op.drop_table("rad_dataset")
    op.drop_table("rad_daily")
