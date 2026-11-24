"""AC-1 (stage 20B): migration 0019 `key_mail_queue` (spec section 6).

Up: `api_key_requests` gets `purpose`, `status`, `attempts`, `next_attempt_at`, `sent_at`;
`token_hash` and `expires_at` accept NULL; checks on purpose, status and the "sent row is
complete" rule; a partial index for the sender. Down: rows without a token are deleted, the
schema is that of 0018, nothing else is touched; up again works. Structure tests run in the
rolled-back `db_session`; the round trip changes the schema and carries `migrations_roundtrip`
(runs last), restoring head in `finally`.
"""

import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_migrations_roundtrip import _db_state, _fetch

pytestmark = pytest.mark.integration
roundtrip = pytest.mark.migrations_roundtrip

NEW_COLUMNS = {"purpose", "status", "attempts", "next_attempt_at", "sent_at"}
OLD_COLUMNS = {"id", "email", "token_hash", "created_at", "expires_at", "used_at"}
HASH_A = "decode(repeat('aa', 32), 'hex')"
HASH_B = "decode(repeat('bb', 32), 'hex')"


def test_ac1_revision_0019_follows_0018(alembic_cfg: AlembicConfig) -> None:
    revision = ScriptDirectory.from_config(alembic_cfg).get_revision("0019")

    assert revision is not None
    assert revision.down_revision == "0018"


async def columns(session: AsyncSession) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'api_key_requests'"
        )
    )
    return {row.column_name: dict(row._mapping) for row in result}


async def test_ac1_the_new_columns_exist_with_the_right_types(db_session: AsyncSession) -> None:
    found = await columns(db_session)

    assert set(found) >= NEW_COLUMNS | OLD_COLUMNS
    assert found["purpose"]["data_type"] == "text"
    assert found["status"]["data_type"] == "text"
    assert found["attempts"]["data_type"] == "smallint"
    assert found["next_attempt_at"]["data_type"] == "timestamp with time zone"
    assert found["sent_at"]["data_type"] == "timestamp with time zone"


async def test_ac1_nullability_token_and_expiry_may_be_null(db_session: AsyncSession) -> None:
    found = await columns(db_session)

    assert found["token_hash"]["is_nullable"] == "YES"
    assert found["expires_at"]["is_nullable"] == "YES"
    assert found["sent_at"]["is_nullable"] == "YES"
    for name in ("purpose", "status", "attempts", "next_attempt_at", "email", "created_at"):
        assert found[name]["is_nullable"] == "NO", name


async def test_ac1_defaults_of_a_bare_request_row(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "INSERT INTO api_key_requests (email) VALUES ('a@example.org') RETURNING purpose, "
                "status, attempts, token_hash, expires_at, sent_at, used_at, "
                "next_attempt_at <= now() AS due, created_at IS NOT NULL AS has_created"
            )
        )
    ).one()

    assert tuple(row) == ("key", "pending", 0, None, None, None, None, True, True)


async def test_ac1_a_pending_row_needs_no_token_and_no_expiry(db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "INSERT INTO api_key_requests (email, purpose, status) "
            "VALUES ('a@example.org', 'delete', 'pending')"
        )
    )


async def test_ac1_token_hash_stays_unique_but_many_rows_may_have_none(
    db_session: AsyncSession,
) -> None:
    for email in ("a@example.org", "b@example.org", "c@example.org"):
        await db_session.execute(
            text("INSERT INTO api_key_requests (email) VALUES (:e)"), {"e": email}
        )
    insert = text(
        "INSERT INTO api_key_requests (email, status, token_hash, expires_at, sent_at) "
        f"VALUES (:e, 'sent', {HASH_A}, now() + interval '1 hour', now())"
    )
    await db_session.execute(insert, {"e": "d@example.org"})

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(insert, {"e": "e@example.org"})


BAD_ROWS = [
    pytest.param(
        "INSERT INTO api_key_requests (email, purpose) VALUES ('a@example.org', 'reset')",
        id="purpose-unknown",
    ),
    pytest.param(
        "INSERT INTO api_key_requests (email, status) VALUES ('a@example.org', 'done')",
        id="status-unknown",
    ),
    pytest.param(
        "INSERT INTO api_key_requests (email, status, expires_at, sent_at) "
        "VALUES ('a@example.org', 'sent', now(), now())",
        id="sent-without-token",
    ),
    pytest.param(
        f"INSERT INTO api_key_requests (email, status, token_hash, sent_at) "
        f"VALUES ('a@example.org', 'sent', {HASH_B}, now())",
        id="sent-without-expiry",
    ),
    pytest.param(
        f"INSERT INTO api_key_requests (email, status, token_hash, expires_at) "
        f"VALUES ('a@example.org', 'sent', {HASH_B}, now())",
        id="sent-without-sent-at",
    ),
    pytest.param(
        "INSERT INTO api_key_requests (email, attempts) VALUES ('a@example.org', NULL)",
        id="attempts-null",
    ),
    pytest.param(
        "INSERT INTO api_key_requests (email, purpose) VALUES ('a@example.org', NULL)",
        id="purpose-null",
    ),
]


@pytest.mark.parametrize("sql", BAD_ROWS)
async def test_ac1_constraints_reject_bad_rows(db_session: AsyncSession, sql: str) -> None:
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text(sql))


@pytest.mark.parametrize("purpose", ["key", "delete"])
@pytest.mark.parametrize("status", ["pending", "failed", "skipped", "dropped"])
async def test_ac1_unsent_rows_of_both_purposes_are_accepted(
    db_session: AsyncSession, purpose: str, status: str
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO api_key_requests (email, purpose, status) VALUES ('a@example.org', :p, :s)"
        ),
        {"p": purpose, "s": status},
    )


async def test_ac1_a_complete_sent_row_is_accepted(db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "INSERT INTO api_key_requests (email, status, token_hash, expires_at, sent_at) "
            f"VALUES ('a@example.org', 'sent', {HASH_A}, now() + interval '1 hour', now())"
        )
    )


async def test_ac1_the_sender_has_a_partial_index_on_pending_rows(
    db_session: AsyncSession,
) -> None:
    definition = await db_session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND indexname = 'ix_api_key_requests_pending'"
        )
    )

    assert definition is not None
    assert "(next_attempt_at)" in definition
    assert "pending" in definition and "WHERE" in definition


async def test_ac1_the_older_indexes_are_kept(db_session: AsyncSession) -> None:
    definitions = (
        await db_session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                "AND tablename = 'api_key_requests'"
            )
        )
    ).scalars()
    joined = "\n".join(definitions)

    assert "(email, created_at)" in joined
    assert "UNIQUE" in joined and "(token_hash)" in joined


# --- round trip (changes the schema: runs last) ---


async def _execute(url: str, sql: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


SEED = [
    "TRUNCATE api_key_requests RESTART IDENTITY",
    "INSERT INTO api_key_requests (email, purpose, status) "
    "VALUES ('pending@example.org', 'key', 'pending')",
    "INSERT INTO api_key_requests (email, purpose, status) "
    "VALUES ('failed@example.org', 'delete', 'failed')",
    "INSERT INTO api_key_requests (email, status, token_hash, expires_at, sent_at) "
    f"VALUES ('sent@example.org', 'sent', {HASH_A}, now() + interval '1 hour', now())",
    "INSERT INTO api_accounts (email) VALUES ('keep@example.org')",
]


@roundtrip
def test_ac1_downgrade_drops_unsent_rows_and_restores_the_0018_shape(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        for statement in SEED:
            asyncio.run(_execute(migrated_db, statement))

        command.downgrade(alembic_cfg, "0018")

        state = asyncio.run(_db_state(migrated_db))
        assert state["versions"] == ["0018"]
        names = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'api_key_requests'",
            )
        )
        by_name = {row["column_name"]: row["is_nullable"] for row in names}
        assert set(by_name) == OLD_COLUMNS
        assert by_name["token_hash"] == "NO" and by_name["expires_at"] == "NO"
        left = asyncio.run(_fetch(migrated_db, "SELECT email FROM api_key_requests"))
        assert [row["email"] for row in left] == ["sent@example.org"]
        accounts = asyncio.run(_fetch(migrated_db, "SELECT email FROM api_accounts"))
        assert [row["email"] for row in accounts] == ["keep@example.org"]
        index = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT indexname FROM pg_indexes WHERE indexname = 'ix_api_key_requests_pending'",
            )
        )
        assert index == []
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(_execute(migrated_db, "TRUNCATE api_key_requests, api_accounts CASCADE"))


@roundtrip
def test_ac1_upgrade_after_the_downgrade_works_and_marks_old_rows_sent(
    migrated_db: str, alembic_cfg: AlembicConfig
) -> None:
    try:
        command.downgrade(alembic_cfg, "0018")
        asyncio.run(
            _execute(
                migrated_db,
                "INSERT INTO api_key_requests (email, token_hash, expires_at) "
                f"VALUES ('old@example.org', {HASH_B}, now() + interval '1 hour')",
            )
        )

        command.upgrade(alembic_cfg, "head")

        after = asyncio.run(_db_state(migrated_db))
        assert after["versions"] == [ScriptDirectory.from_config(alembic_cfg).get_current_head()]
        rows = asyncio.run(
            _fetch(
                migrated_db,
                "SELECT email, purpose, status, attempts FROM api_key_requests ORDER BY id",
            )
        )
        assert rows == [
            {"email": "old@example.org", "purpose": "key", "status": "sent", "attempts": 0}
        ]
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(_execute(migrated_db, "TRUNCATE api_key_requests, api_accounts CASCADE"))
