"""Cache, counters and housekeeping of API keys under stress (stage 20A review).

- resolver cache: LRU at `max_entries`; negative answers do not push valid keys out;
- usage counters: a failed flush gives the counters back, the next flush writes them; shutdown
  flushes what is in memory;
- `cleanup_api_keys` drops old counters and long expired confirmation requests only.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.apikeys import ApiKeyResolver
from app.core.config import Settings
from app.services.api_keys import cleanup_api_keys
from tests._apikeys import (
    IP_A,
    SMALL_V2,
    Clock,
    KeysApp,
    account_with_key,
    create_account,
    create_key,
    key_hash,
    keys_app,
    new_key,
    rows,
    scalar,
    statuses,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
Factory = async_sessionmaker[AsyncSession]


class CountingFactory:
    """Session factory that counts how often the resolver goes to the database."""

    def __init__(self, factory: Factory) -> None:
        self.factory = factory
        self.calls = 0

    def __call__(self) -> AsyncSession:
        self.calls += 1
        return self.factory()


def resolver_for(factory: Factory, max_entries: int) -> tuple[ApiKeyResolver, CountingFactory]:
    counting = CountingFactory(factory)
    resolver = ApiKeyResolver(counting, 60, max_entries=max_entries, clock=Clock())  # type: ignore[arg-type]
    return resolver, counting


# --- resolver cache ---


async def test_lru_the_cache_never_holds_more_than_max_entries(session_factory: Factory) -> None:
    account_id = await create_account(session_factory)
    keys = [await create_key(session_factory, account_id) for _ in range(12)]
    resolver, _ = resolver_for(session_factory, max_entries=5)

    for key in keys:
        assert (await resolver.resolve(key.value)).status == "ok"

    assert len(resolver) == 5


async def test_lru_the_least_recently_used_key_is_the_one_evicted(session_factory: Factory) -> None:
    account_id = await create_account(session_factory)
    keys = [await create_key(session_factory, account_id) for _ in range(3)]
    resolver, counting = resolver_for(session_factory, max_entries=2)
    await resolver.resolve(keys[0].value)
    await resolver.resolve(keys[1].value)
    await resolver.resolve(keys[0].value)  # keys[1] is now the least recently used
    await resolver.resolve(keys[2].value)  # evicts keys[1]
    counting.calls = 0

    await resolver.resolve(keys[0].value)
    kept = counting.calls
    await resolver.resolve(keys[1].value)
    evicted = counting.calls - kept

    assert kept == 0
    assert evicted == 1


async def test_many_negative_lookups_do_not_evict_valid_keys(session_factory: Factory) -> None:
    key = await account_with_key(session_factory)
    resolver, counting = resolver_for(session_factory, max_entries=10)
    assert (await resolver.resolve(key.value)).status == "ok"

    for _ in range(60):
        assert (await resolver.resolve(new_key())).status == "invalid"
    counting.calls = 0
    again = await resolver.resolve(key.value)

    assert again.status == "ok"
    assert counting.calls == 0, "the valid key was pushed out of the cache by unknown keys"


# --- usage counters ---


class FailingFactory:
    def __call__(self) -> AsyncSession:
        raise OperationalError("INSERT", {}, Exception("connection refused"))


async def test_a_failed_flush_returns_the_counters_and_the_next_flush_writes_them(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    usage = env.app.state.api_key_usage
    async with env.client(IP_A, {"X-Api-Key": key.value}) as client:
        await statuses(client, SMALL_V2, 5)
        assert usage.pending == 5
        working = usage.session_factory
        usage.session_factory = FailingFactory()
        with pytest.raises(OperationalError):
            await usage.flush()
        assert usage.pending == 5, "the counters must not be lost by a failed write"
        await statuses(client, SMALL_V2, 3)  # more requests arrive meanwhile
        usage.session_factory = working

        await usage.flush()

    total = await scalar(
        env.factory, "SELECT sum(requests) FROM api_key_usage_daily WHERE key_id = :k", k=key.id
    )
    assert total == 8
    assert usage.pending == 0
    assert await scalar(env.factory, "SELECT count(*) FROM api_key_usage_daily") == 1


async def test_shutdown_flushes_the_counters_that_are_still_in_memory(
    settings: Settings, session_factory: Factory
) -> None:
    async with keys_app(settings) as env:
        key = await account_with_key(env.factory)
        async with env.client(IP_A, {"X-Api-Key": key.value}) as client:
            await statuses(client, SMALL_V2, 6)
        assert await scalar(session_factory, "SELECT count(*) FROM api_key_usage_daily") == 0

    total = await scalar(
        session_factory, "SELECT sum(requests) FROM api_key_usage_daily WHERE key_id = :k", k=key.id
    )
    assert total == 6
    last_used = await scalar(
        session_factory, "SELECT last_used_at FROM api_keys WHERE id = :k", k=key.id
    )
    assert last_used is not None


# --- cleanup_api_keys ---


async def add_usage_row(factory: Factory, key_id: int, account_id: int, days_ago: int) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO api_key_usage_daily (account_id, key_id, day, requests) "
                "VALUES (:a, :k, CURRENT_DATE - CAST(:d AS int), 1)"
            ),
            {"a": account_id, "k": key_id, "d": days_ago},
        )
        await session.commit()


async def add_request(factory: Factory, email: str, expires_in_hours: float) -> None:
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO api_key_requests (email, token_hash, expires_at) "
                "VALUES (:e, :h, now() + make_interval(secs => :s))"
            ),
            {"e": email, "h": key_hash(email), "s": expires_in_hours * 3600},
        )
        await session.commit()


async def test_cleanup_drops_old_usage_and_long_expired_requests_only(
    settings: Settings, session_factory: Factory
) -> None:
    key = await account_with_key(session_factory)
    for days in (200, 91, 89, 0):
        await add_usage_row(session_factory, key.id, key.account_id, days)
    await add_request(session_factory, "ancient@example.org", -48)  # expired 48 h ago
    await add_request(session_factory, "recent@example.org", -1)  # expired 1 h ago
    await add_request(session_factory, "live@example.org", 0.5)  # still valid
    config = settings.model_copy(
        update={"api_key_usage_retention_days": 90, "key_request_retention_hours": 24}
    )

    await cleanup_api_keys(session_factory, config, datetime.now(UTC))

    ages = sorted(
        r["age"]
        for r in await rows(
            session_factory, "SELECT (CURRENT_DATE - day) AS age FROM api_key_usage_daily"
        )
    )
    assert ages == [0, 89]
    left = {r["email"] for r in await rows(session_factory, "SELECT email FROM api_key_requests")}
    assert left == {"recent@example.org", "live@example.org"}
    # Accounts and keys are never touched by the housekeeping.
    assert await scalar(session_factory, "SELECT count(*) FROM api_keys") == 1
    assert await scalar(session_factory, "SELECT count(*) FROM api_accounts") == 1


async def test_cleanup_on_an_empty_database_is_harmless(
    settings: Settings, session_factory: Factory
) -> None:
    await cleanup_api_keys(session_factory, settings, datetime.now(UTC) + timedelta(days=1))

    assert await scalar(session_factory, "SELECT count(*) FROM api_key_usage_daily") == 0
