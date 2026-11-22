"""API key accounts, keys, confirmation requests and daily usage (stage 20).

Four new tables; no existing table is touched and no data is seeded. The downgrade drops all
four (accounts and keys are lost with them: they cannot be rebuilt from other tables).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'blocked')", name=op.f("ck_api_accounts_status_valid")
        ),
        sa.CheckConstraint(
            "length(block_reason) <= 500", name=op.f("ck_api_accounts_block_reason_length")
        ),
        sa.CheckConstraint("length(email) <= 254", name=op.f("ck_api_accounts_email_length")),
        sa.CheckConstraint("length(note) <= 500", name=op.f("ck_api_accounts_note_length")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_accounts")),
        sa.UniqueConstraint("email", name=op.f("uq_api_accounts_email")),
    )
    op.create_table(
        "api_key_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_key_requests")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_key_requests_token_hash")),
    )
    op.create_index(
        "ix_api_key_requests_email_created_at",
        "api_key_requests",
        ["email", "created_at"],
        unique=False,
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "revoke_reason IN ('owner', 'admin', 'replaced')",
            name=op.f("ck_api_keys_revoke_reason_valid"),
        ),
        sa.CheckConstraint("octet_length(key_hash) = 32", name=op.f("ck_api_keys_key_hash_length")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["api_accounts.id"],
            name=op.f("fk_api_keys_account_id_api_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_index("ix_api_keys_account_id", "api_keys", ["account_id"], unique=False)
    op.create_index(
        "ix_api_keys_prefix",
        "api_keys",
        ["prefix"],
        unique=False,
        postgresql_ops={"prefix": "text_pattern_ops"},
    )
    op.create_table(
        "api_key_usage_daily",
        sa.Column("key_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("requests", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("rejected", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["api_accounts.id"],
            name=op.f("fk_api_key_usage_daily_account_id_api_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["key_id"],
            ["api_keys.id"],
            name=op.f("fk_api_key_usage_daily_key_id_api_keys"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("key_id", "day", name=op.f("pk_api_key_usage_daily")),
    )
    op.create_index(
        "ix_api_key_usage_daily_account_id_day",
        "api_key_usage_daily",
        ["account_id", "day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_api_key_usage_daily_account_id_day", table_name="api_key_usage_daily")
    op.drop_table("api_key_usage_daily")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_account_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_api_key_requests_email_created_at", table_name="api_key_requests")
    op.drop_table("api_key_requests")
    op.drop_table("api_accounts")
