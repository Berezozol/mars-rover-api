"""Hourly extremes and gust of the weather (stage 22).

``weather_hourly`` gets the lowest and highest valid value of each parameter of the hour and the
gust (99th percentile of the wind speed) of the hour. All new columns are NULL until the sol is
parsed again (``WEATHER_PARSER_REVISION`` 5). The downgrade drops only the new columns and
checks; the hourly means stay.

Revision ID: 0020
Revises: 0019
Create Date: 2026-12-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "weather_hourly"
# (parameter, unit suffix) of the parameters that have a lowest and a highest value.
_PAIRS = (("air", "k"), ("pressure", "pa"), ("humidity", "pct"), ("ground", "k"), ("uv", "wm2"))
_COLUMNS = [f"{name}_{kind}_{unit}" for name, unit in _PAIRS for kind in ("min", "max")]
_COLUMNS.append("wind_gust_ms")


def _pair_name(name: str) -> str:
    return op.f(f"ck_{_TABLE}_{name}_extremes_pair")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column(_TABLE, sa.Column(column, sa.Float(), nullable=True))
    for name, unit in _PAIRS:
        op.create_check_constraint(
            _pair_name(name),
            _TABLE,
            f"({name}_min_{unit} IS NULL) = ({name}_max_{unit} IS NULL)",
        )


def downgrade() -> None:
    for name, _ in _PAIRS:
        op.drop_constraint(_pair_name(name), _TABLE, type_="check")
    for column in reversed(_COLUMNS):
        op.drop_column(_TABLE, column)
