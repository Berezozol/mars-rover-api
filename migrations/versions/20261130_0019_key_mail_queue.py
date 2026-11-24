"""Mail queue in api_key_requests (stage 20B).

The table of 0016 becomes the outbox of the key mails: purpose, status, attempts, the time of
the next attempt and the time of sending; ``token_hash`` and ``expires_at`` stay empty until
the mail is sent. About 200 rows live at once at most, so no data is moved. The downgrade
deletes the rows that have no token (mails not sent yet; they live minutes) and restores the
0016 shape.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "api_key_requests"


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column("purpose", sa.Text(), server_default=sa.text("'key'"), nullable=False)
    )
    op.add_column(
        _TABLE,
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    )
    op.add_column(
        _TABLE,
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(_TABLE, sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    # Rows of 0016 (none in practice) were created with a token: they count as sent.
    op.execute("UPDATE api_key_requests SET status = 'sent', sent_at = created_at")
    op.alter_column(_TABLE, "token_hash", existing_type=sa.LargeBinary(), nullable=True)
    op.alter_column(_TABLE, "expires_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.create_check_constraint(
        op.f("ck_api_key_requests_purpose_valid"), _TABLE, "purpose IN ('key', 'delete')"
    )
    op.create_check_constraint(
        op.f("ck_api_key_requests_status_valid"),
        _TABLE,
        "status IN ('pending', 'sent', 'failed', 'skipped', 'dropped')",
    )
    op.create_check_constraint(
        op.f("ck_api_key_requests_sent_complete"),
        _TABLE,
        "status <> 'sent' OR (token_hash IS NOT NULL AND expires_at IS NOT NULL "
        "AND sent_at IS NOT NULL)",
    )
    op.create_index(
        "ix_api_key_requests_pending",
        _TABLE,
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM api_key_requests WHERE token_hash IS NULL OR expires_at IS NULL")
    op.drop_index("ix_api_key_requests_pending", table_name=_TABLE)
    op.drop_constraint(op.f("ck_api_key_requests_sent_complete"), _TABLE, type_="check")
    op.drop_constraint(op.f("ck_api_key_requests_status_valid"), _TABLE, type_="check")
    op.drop_constraint(op.f("ck_api_key_requests_purpose_valid"), _TABLE, type_="check")
    op.alter_column(_TABLE, "expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column(_TABLE, "token_hash", existing_type=sa.LargeBinary(), nullable=False)
    op.drop_column(_TABLE, "sent_at")
    op.drop_column(_TABLE, "next_attempt_at")
    op.drop_column(_TABLE, "attempts")
    op.drop_column(_TABLE, "status")
    op.drop_column(_TABLE, "purpose")
