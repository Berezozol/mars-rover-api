"""rovers.end_date: official end of the mission (reference data, not from a source).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dates of the official announcements of the end of the missions.
_END_DATES = {"spirit": "2011-05-25", "opportunity": "2019-02-13"}


def upgrade() -> None:
    op.add_column("rovers", sa.Column("end_date", sa.Date(), nullable=True))
    for rover_id, end_date in _END_DATES.items():
        op.execute(
            sa.text(
                "UPDATE rovers SET end_date = CAST(:end_date AS date) WHERE id = :id"
            ).bindparams(end_date=end_date, id=rover_id)
        )


def downgrade() -> None:
    op.drop_column("rovers", "end_date")
