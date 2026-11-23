"""Weather per sol: raw files, daily summaries, hourly means and the state of the data.

Four new tables (stage 17); no existing table is touched and no data is seeded. The downgrade
drops all four: the data is rebuilt by ``weather parse`` or ``weather import`` of an export file.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weather_daily",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("sol", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("earth_date", sa.Date(), nullable=False),
        sa.Column("ls", sa.Float(), nullable=True),
        sa.Column("coverage", sa.REAL(), nullable=False),
        sa.Column("hours_covered", sa.SmallInteger(), nullable=False),
        sa.Column("air_min_k", sa.Float(), nullable=True),
        sa.Column("air_max_k", sa.Float(), nullable=True),
        sa.Column("air_mean_k", sa.Float(), nullable=True),
        sa.Column("pressure_min_pa", sa.Float(), nullable=True),
        sa.Column("pressure_max_pa", sa.Float(), nullable=True),
        sa.Column("pressure_mean_pa", sa.Float(), nullable=True),
        sa.Column("humidity_min_pct", sa.Float(), nullable=True),
        sa.Column("humidity_max_pct", sa.Float(), nullable=True),
        sa.Column("humidity_mean_pct", sa.Float(), nullable=True),
        sa.Column("ground_min_k", sa.Float(), nullable=True),
        sa.Column("ground_max_k", sa.Float(), nullable=True),
        sa.Column("ground_mean_k", sa.Float(), nullable=True),
        sa.Column("uv_max_wm2", sa.Float(), nullable=True),
        sa.Column("uv_mean_wm2", sa.Float(), nullable=True),
        sa.Column("wind_mean_ms", sa.Float(), nullable=True),
        sa.Column("wind_gust_ms", sa.Float(), nullable=True),
        sa.Column("wind_dir_mode_deg", sa.Float(), nullable=True),
        sa.Column("wind_dir_r", sa.Float(), nullable=True),
        sa.Column("n_air", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_pressure", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_humidity", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_ground", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_uv", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_wind", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_version", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parser_revision", sa.SmallInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(air_min_k IS NULL) = (air_max_k IS NULL) AND (air_max_k IS NULL) = (air_mean_k IS NULL)",
            name=op.f("ck_weather_daily_air_triple"),
        ),
        sa.CheckConstraint(
            "(ground_min_k IS NULL) = (ground_max_k IS NULL) AND (ground_max_k IS NULL) = (ground_mean_k IS NULL)",
            name=op.f("ck_weather_daily_ground_triple"),
        ),
        sa.CheckConstraint(
            "(humidity_min_pct IS NULL) = (humidity_max_pct IS NULL) AND (humidity_max_pct IS NULL) = (humidity_mean_pct IS NULL)",
            name=op.f("ck_weather_daily_humidity_triple"),
        ),
        sa.CheckConstraint(
            "(pressure_min_pa IS NULL) = (pressure_max_pa IS NULL) AND (pressure_max_pa IS NULL) = (pressure_mean_pa IS NULL)",
            name=op.f("ck_weather_daily_pressure_triple"),
        ),
        sa.CheckConstraint(
            "(uv_max_wm2 IS NULL) = (uv_mean_wm2 IS NULL)", name=op.f("ck_weather_daily_uv_pair")
        ),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 1", name=op.f("ck_weather_daily_coverage_range")
        ),
        sa.CheckConstraint(
            "hours_covered >= 0 AND hours_covered <= 24",
            name=op.f("ck_weather_daily_hours_covered_range"),
        ),
        sa.CheckConstraint("sol >= 0", name=op.f("ck_weather_daily_sol_not_negative")),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_weather_daily_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "sol", name=op.f("pk_weather_daily")),
    )
    op.create_table(
        "weather_dataset",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_max_sol", sa.Integer(), nullable=True),
        sa.Column("files_parsed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("files_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_weather_dataset_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", name=op.f("pk_weather_dataset")),
    )
    op.create_table(
        "weather_files",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sol", sa.Integer(), nullable=False),
        sa.Column("product", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("foreign_sol_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("label_start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label_ls", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "parser_revision", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "parsed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('parsed', 'superseded', 'failed')",
            name=op.f("ck_weather_files_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["rover_id"], ["rovers.id"], name=op.f("fk_weather_files_rover_id_rovers")
        ),
        sa.PrimaryKeyConstraint("rover_id", "path", name=op.f("pk_weather_files")),
    )
    op.create_index(
        "ix_weather_files_rover_sol", "weather_files", ["rover_id", "sol"], unique=False
    )
    op.create_table(
        "weather_hourly",
        sa.Column("rover_id", sa.Text(), nullable=False),
        sa.Column("sol", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("hour", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("coverage", sa.REAL(), server_default=sa.text("0"), nullable=False),
        sa.Column("air_k", sa.Float(), nullable=True),
        sa.Column("pressure_pa", sa.Float(), nullable=True),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("ground_k", sa.Float(), nullable=True),
        sa.Column("uv_wm2", sa.Float(), nullable=True),
        sa.Column("wind_ms", sa.Float(), nullable=True),
        sa.Column("wind_dir_deg", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 1", name=op.f("ck_weather_hourly_coverage_range")
        ),
        sa.CheckConstraint("hour >= 0 AND hour <= 23", name=op.f("ck_weather_hourly_hour_range")),
        sa.ForeignKeyConstraint(
            ["rover_id", "sol"],
            ["weather_daily.rover_id", "weather_daily.sol"],
            name=op.f("fk_weather_hourly_rover_id_weather_daily"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rover_id", "sol", "hour", name=op.f("pk_weather_hourly")),
    )


def downgrade() -> None:
    op.drop_table("weather_hourly")
    op.drop_index("ix_weather_files_rover_sol", table_name="weather_files")
    op.drop_table("weather_files")
    op.drop_table("weather_dataset")
    op.drop_table("weather_daily")
