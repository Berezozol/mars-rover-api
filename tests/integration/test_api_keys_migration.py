"""AC-1 (stage 20A): migration 0016 `api_keys` (spec section 6).

Up: `api_accounts`, `api_keys`, `api_key_requests`, `api_key_usage_daily` with the listed
constraints and indexes. Down: the four tables are gone, nothing else is touched; up again works.
Structure and constraint tests run inside the rolled-back `db_session`; the round trip drops and
recreates the schema, so it carries the `migrations_roundtrip` marker (runs last) and restores
head in `finally`, like tests/integration/test_migrations_roundtrip.py.
"""

import asyncio

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_migrations_roundtrip import _db_state, _fetch

pytestmark = pytest.mark.integration
roundtrip = pytest.mark.migrations_roundtrip

TABLES = {"api_accounts", "api_keys", "api_key_requests", "api_key_usage_daily"}
HASH_A = "decode(repeat('aa', 32), 'hex')"
HASH_B = "decode(repeat('bb', 32), 'hex')"


def test_ac1_revision_0016_follows_0015(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0016")

    assert revision is not None
    assert revision.down_revision == "0015"


async def test_ac1_head_has_the_four_tables(db_session: AsyncSession) -> None:
    names = set(
        (
            await db_session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name LIKE 'api\\_%'"
                )
            )
        ).scalars()
    )

    assert names == TABLES


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("api_accounts", {"id", "email", "status", "created_at", "verified_at", "blocked_at"}),
        ("api_accounts", {"block_reason", "note"}),
        (
            "api_keys",
            {"id", "account_id", "key_hash", "prefix", "created_at", "last_used_at", "revoked_at"},
        ),
        ("api_keys", {"revoke_reason"}),
        (
            "api_key_requests",
            {"id", "email", "token_hash", "created_at", "expires_at", "used_at"},
        ),
        (
            "api_key_usage_daily",
            {"account_id", "key_id", "day", "requests", "rejected"},
        ),
    ],
    ids=["accounts", "accounts-extra", "keys", "keys-extra", "requests", "usage"],
)
async def test_ac1_columns_exist(db_session: AsyncSession, table: str, expected: set[str]) -> None:
    columns = set(
        (
            await db_session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
        ).scalars()
    )

    assert expected <= columns


async def test_ac1_column_types(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name LIKE 'api\\_%'"
            )
        )
    ).all()
    types = {(t, c): d for t, c, d in rows}

    assert types[("api_accounts", "id")] == "bigint"
    assert types[("api_accounts", "email")] == "text"
    assert types[("api_accounts", "created_at")] == "timestamp with time zone"
    assert types[("api_keys", "key_hash")] == "bytea"
    assert types[("api_keys", "prefix")] == "text"
    assert types[("api_key_requests", "token_hash")] == "bytea"
    assert types[("api_key_usage_daily", "day")] == "date"
    assert types[("api_key_usage_daily", "requests")] == "bigint"
    assert types[("api_key_usage_daily", "rejected")] == "bigint"


async def test_ac1_indexes_exist(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename LIKE 'api\\_%'"
            )
        )
    ).all()
    by_name = {name: definition for _, name, definition in rows}

    assert (
        "(prefix" in by_name["ix_api_keys_prefix"]
    )  # text_pattern_ops allowed (LIKE prefix search)
    assert "(account_id)" in by_name["ix_api_keys_account_id"]
    usage = [d for t, _, d in rows if t == "api_key_usage_daily"]
    assert any("(account_id, day)" in d for d in usage), usage
    requests = [d for t, _, d in rows if t == "api_key_requests"]
    assert any("(email, created_at)" in d for d in requests), requests
    keys = [d for t, _, d in rows if t == "api_keys"]
    assert any("UNIQUE" in d and "(key_hash)" in d for d in keys), keys
    accounts = [d for t, _, d in rows if t == "api_accounts"]
    assert any("UNIQUE" in d and "(email)" in d for d in accounts), accounts


async def test_ac1_defaults_of_a_minimal_account(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "INSERT INTO api_accounts (email) VALUES ('a@example.org') "
                "RETURNING status, created_at IS NOT NULL AS has_created, verified_at, "
                "blocked_at, block_reason, note"
            )
        )
    ).one()

    assert tuple(row) == ("active", True, None, None, None, None)


async def _account(session: AsyncSession, email: str = "a@example.org") -> int:
    account_id = await session.scalar(
        text("INSERT INTO api_accounts (email) VALUES (:e) RETURNING id"), {"e": email}
    )
    assert isinstance(account_id, int)
    return account_id


async def _key(session: AsyncSession, account_id: int, hash_sql: str = HASH_A) -> int:
    key_id = await session.scalar(
        text(
            "INSERT INTO api_keys (account_id, key_hash, prefix) "
            f"VALUES (:a, {hash_sql}, 'si_AbCd1234') RETURNING id"
        ),
        {"a": account_id},
    )
    assert isinstance(key_id, int)
    return key_id


BAD_ACCOUNTS = [
    pytest.param("INSERT INTO api_accounts (email) VALUES ('a@example.org')", id="duplicate-email"),
    pytest.param("INSERT INTO api_accounts (email) VALUES ('{long}')", id="email-over-254"),
    pytest.param("INSERT INTO api_accounts (email) VALUES (NULL)", id="email-null"),
    pytest.param(
        "INSERT INTO api_accounts (email, status) VALUES ('b@example.org', 'frozen')",
        id="status-not-active-or-blocked",
    ),
    pytest.param(
        "INSERT INTO api_accounts (email, block_reason) VALUES ('b@example.org', '{r501}')",
        id="block-reason-over-500",
    ),
    pytest.param(
        "INSERT INTO api_accounts (email, note) VALUES ('b@example.org', '{r501}')",
        id="note-over-500",
    ),
]


@pytest.mark.parametrize("sql", BAD_ACCOUNTS)
async def test_ac1_account_constraints_reject_bad_rows(db_session: AsyncSession, sql: str) -> None:
    await _account(db_session)
    statement = sql.replace("{long}", "a" * 250 + "@x.org").replace("{r501}", "r" * 501)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text(statement))


async def test_ac1_email_of_254_characters_is_accepted(db_session: AsyncSession) -> None:
    await _account(db_session, "a" * 242 + "@example.org")  # 254


async def test_ac1_blocked_status_is_accepted(db_session: AsyncSession) -> None:
    await db_session.execute(
        text("INSERT INTO api_accounts (email, status) VALUES ('b@example.org', 'blocked')")
    )


BAD_KEYS = [
    pytest.param(
        "INSERT INTO api_keys (account_id, key_hash, prefix) "
        "VALUES (:a, decode(repeat('cc', 31), 'hex'), 'si_AbCd1234')",
        id="hash-31-bytes",
    ),
    pytest.param(
        "INSERT INTO api_keys (account_id, key_hash, prefix) "
        "VALUES (:a, decode(repeat('cc', 33), 'hex'), 'si_AbCd1234')",
        id="hash-33-bytes",
    ),
    pytest.param(
        f"INSERT INTO api_keys (account_id, key_hash, prefix) VALUES (:a, {HASH_A}, 'si_AbCd1234')",
        id="duplicate-hash",
    ),
    pytest.param(
        f"INSERT INTO api_keys (account_id, key_hash, prefix, revoke_reason) "
        f"VALUES (:a, {HASH_B}, 'si_AbCd1234', 'whim')",
        id="revoke-reason-unknown",
    ),
    pytest.param(
        f"INSERT INTO api_keys (account_id, key_hash, prefix) VALUES (999999999, {HASH_B}, 'x')",
        id="account-does-not-exist",
    ),
    pytest.param(
        f"INSERT INTO api_keys (account_id, key_hash, prefix) VALUES (:a, {HASH_B}, NULL)",
        id="prefix-null",
    ),
]


@pytest.mark.parametrize("sql", BAD_KEYS)
async def test_ac1_key_constraints_reject_bad_rows(db_session: AsyncSession, sql: str) -> None:
    account_id = await _account(db_session)
    await _key(db_session, account_id)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text(sql), {"a": account_id})


@pytest.mark.parametrize("reason", ["owner", "admin", "replaced"])
async def test_ac1_key_revoke_reasons_are_accepted(db_session: AsyncSession, reason: str) -> None:
    account_id = await _account(db_session)

    await db_session.execute(
        text(
            "INSERT INTO api_keys (account_id, key_hash, prefix, revoked_at, revoke_reason) "
            f"VALUES (:a, {HASH_B}, 'si_AbCd1234', now(), :r)"
        ),
        {"a": account_id, "r": reason},
    )


async def test_ac1_request_token_hash_is_unique(db_session: AsyncSession) -> None:
    insert = text(
        "INSERT INTO api_key_requests (email, token_hash, expires_at) "
        f"VALUES (:e, {HASH_A}, now() + interval '30 minutes')"
    )
    await db_session.execute(insert, {"e": "a@example.org"})

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(insert, {"e": "b@example.org"})


async def test_ac1_usage_primary_key_is_key_and_day(db_session: AsyncSession) -> None:
    account_id = await _account(db_session)
    key_id = await _key(db_session, account_id)
    insert = text(
        "INSERT INTO api_key_usage_daily (account_id, key_id, day) "
        "VALUES (:a, :k, DATE '2026-12-01')"
    )
    await db_session.execute(insert, {"a": account_id, "k": key_id})

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(insert, {"a": account_id, "k": key_id})
    row = (
        await db_session.execute(
            text("SELECT requests, rejected FROM api_key_usage_daily WHERE key_id = :k"),
            {"k": key_id},
        )
    ).one()
    assert tuple(row) == (0, 0)


async def test_ac1_deleting_an_account_removes_its_keys_and_counters(
    db_session: AsyncSession,
) -> None:
    account_id = await _account(db_session)
    other_id = await _account(db_session, "other@example.org")
    key_id = await _key(db_session, account_id)
    other_key = await _key(db_session, other_id, HASH_B)
    for owner, key in ((account_id, key_id), (other_id, other_key)):
        await db_session.execute(
            text(
                "INSERT INTO api_key_usage_daily (account_id, key_id, day, requests) "
                "VALUES (:a, :k, DATE '2026-12-01', 5)"
            ),
            {"a": owner, "k": key},
        )

    await db_session.execute(text("DELETE FROM api_accounts WHERE id = :a"), {"a": account_id})

    assert await db_session.scalar(text("SELECT count(*) FROM api_keys")) == 1
    assert await db_session.scalar(text("SELECT count(*) FROM api_key_usage_daily")) == 1


async def test_ac1_deleting_a_key_removes_its_counters(db_session: AsyncSession) -> None:
    account_id = await _account(db_session)
    key_id = await _key(db_session, account_id)
    await db_session.execute(
        text(
            "INSERT INTO api_key_usage_daily (account_id, key_id, day) "
            "VALUES (:a, :k, DATE '2026-12-01')"
        ),
        {"a": account_id, "k": key_id},
    )

    await db_session.execute(text("DELETE FROM api_keys WHERE id = :k"), {"k": key_id})

    assert await db_session.scalar(text("SELECT count(*) FROM api_key_usage_daily")) == 0
    assert await db_session.scalar(text("SELECT count(*) FROM api_accounts")) == 1


# --- round trip (drops and recreates the schema: runs last) ---


@roundtrip
def test_ac1_downgrade_removes_the_tables_and_upgrade_restores_them(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0015")
        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0015"]
        assert state["tables"] & TABLES == set()
        # Nothing else is touched: the seed data of other tables survives.
        assert state["tables"] >= {"rovers", "cameras", "photos", "sync_runs"}
        assert state["counts"]["rovers"] == 5

        command.upgrade(alembic_cfg, "head")
        after = asyncio.run(_db_state(migrated_db))
        assert after["tables"] >= TABLES
        assert after["versions"] == [ScriptDirectory.from_config(alembic_cfg).get_current_head()]
    finally:
        command.upgrade(alembic_cfg, "head")


@roundtrip
def test_ac1_downgrade_drops_the_named_indexes(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0015")
        left = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname IN ('ix_api_keys_prefix', 'ix_api_keys_account_id')",
            )
        )
        assert left == []
    finally:
        command.upgrade(alembic_cfg, "head")
