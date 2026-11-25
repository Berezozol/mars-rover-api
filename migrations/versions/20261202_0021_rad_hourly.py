"""Hourly radiation of Curiosity (stage 22, phase C).

New table ``rad_hourly`` (only the hours of a sol that have observations) and three columns of
``rad_daily``: ``hourly_revision`` (0 until the hours of the sol are calculated), ``hourly_reject``
(why the hours of the sol were refused) and ``hourly_dropped`` (used observations that are not in
the hours). Existing rows keep revision 0 and no hours. The downgrade drops the table and the
columns with their checks; the daily summaries stay as they are.

Revision ID: 0021
Revises: 0020
Create Date: 2026-12-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REJECT_CHECK = "ck_rad_daily_hourly_reject_valid"
_DROPPED_CHECK = "ck_rad_daily_hourly_dropped_nonneg"


def upgrade() -> None:
    op.add_column(
        "rad_daily",
        sa.Column(
            "hourly_revision", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column("rad_daily", sa.Column("hourly_reject", sa.Text(), nullable=True))
    op.add_column(
        "rad_daily",
        sa.Column("hourly_dropped", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        op.f(_REJECT_CHECK),
        "rad_daily",
        "hourly_reject IS NULL OR hourly_reject IN ('overlap', 'off_sol')",
    )
    op.create_check_constraint(op.f(_DROPPED_CHECK), "rad_daily", "hourly_dropped >= 0")
    op.create_table(
        "rad_hourly",
        sa.Column("sol", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("hour", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("n_obs", sa.SmallInteger(), nullable=False),
        sa.Column("live_s", sa.Float(), nullable=False),
        sa.Column("dose_b_mean", sa.Float(), nullable=False),
        sa.Column("dose_e_mean", sa.Float(), nullable=False),
        sa.Column("bu_count", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("hour >= 0 AND hour <= 23", name=op.f("ck_rad_hourly_hour_range")),
        sa.CheckConstraint("n_obs > 0", name=op.f("ck_rad_hourly_n_obs_positive")),
        sa.CheckConstraint("live_s > 0", name=op.f("ck_rad_hourly_live_positive")),
        sa.ForeignKeyConstraint(
            ["sol"],
            ["rad_daily.sol"],
            name=op.f("fk_rad_hourly_sol_rad_daily"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sol", "hour", name=op.f("pk_rad_hourly")),
    )


def downgrade() -> None:
    op.drop_table("rad_hourly")
    op.drop_constraint(op.f(_DROPPED_CHECK), "rad_daily", type_="check")
    op.drop_constraint(op.f(_REJECT_CHECK), "rad_daily", type_="check")
    op.drop_column("rad_daily", "hourly_dropped")
    op.drop_column("rad_daily", "hourly_reject")
    op.drop_column("rad_daily", "hourly_revision")
