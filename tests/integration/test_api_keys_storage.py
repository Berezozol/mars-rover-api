"""Key storage, key cache and usage counters (stage 20A, AC-2, AC-18).

AC-2: an issued key is `si_` + 32 base62 characters; the database holds only its SHA-256
(`key_hash`, 32 bytes) and the first 11 characters (`prefix`); the key itself is in no table.
AC-18: 1000 requests with one key cost at most one `SELECT` on `api_keys` (the cache, negative
answers included); after a flush `api_key_usage_daily` has `requests = 1000` for today and
`api_keys.last_used_at` is set.

Flush entry point (extension point, see the report): `await app.state.api_key_usage.flush()`;
if the method wants a session factory, the worker factory is passed.
"""

import inspect
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import event

from tests._apikeys import (
    IP_A,
    KEY_RE,
    SMALL_V2,
    KeysApp,
    account_with_key,
    create_account,
    create_key,
    key_hash,
    new_key,
    rows,
    scalar,
    statuses,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
HIGH = 1_000_000
KEY_TABLES = ("api_accounts", "api_keys", "api_key_requests", "api_key_usage_daily")


def hdr(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


async def flush_usage(app: FastAPI) -> None:
    flush = app.state.api_key_usage.flush
    needs_factory = any(
        p.default is inspect.Parameter.empty for p in inspect.signature(flush).parameters.values()
    )
    await (flush(app.state.worker_session_factory) if needs_factory else flush())


# --- AC-2 ---


async def test_ac2_issued_key_has_the_format_and_the_database_holds_only_its_hash(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.post("/api/v2/admin/accounts", json={"email": "a@example.org"})
    assert response.status_code == 201, response.text
    key = response.json()["key"]

    assert KEY_RE.fullmatch(key), key
    stored = await rows(env.factory, "SELECT key_hash, prefix FROM api_keys")
    assert len(stored) == 1
    assert bytes(stored[0]["key_hash"]) == key_hash(key)
    assert len(bytes(stored[0]["key_hash"])) == 32
    assert stored[0]["prefix"] == key[:11]
    assert len(stored[0]["prefix"]) == 11


async def test_ac2_the_key_is_in_no_table(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.post(
            "/api/v2/admin/accounts", json={"email": "a@example.org", "note": "for tests"}
        )
    key = response.json()["key"]

    for table in KEY_TABLES:
        dump = await rows(env.factory, f"SELECT t::text AS row FROM {table} t")  # noqa: S608
        for line in dump:
            assert key not in line["row"], table
            assert key[3:] not in line["row"], table
    assert await scalar(env.factory, "SELECT count(*) FROM api_key_requests") == 0


async def test_ac2_each_issued_key_is_different_and_matches_its_own_hash(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    keys = []
    async with env.admin() as admin:
        for index in range(5):
            response = await admin.post(
                "/api/v2/admin/accounts", json={"email": f"user{index}@example.org"}
            )
            keys.append(response.json()["key"])

    assert len(set(keys)) == 5
    stored = {
        bytes(r["key_hash"]) for r in await rows(env.factory, "SELECT key_hash FROM api_keys")
    }
    assert stored == {key_hash(k) for k in keys}


async def test_ac2_the_issued_key_works(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        key = (await admin.post("/api/v2/admin/accounts", json={"email": "a@example.org"})).json()[
            "key"
        ]
    async with env.client(IP_A, hdr(key)) as client:
        response = await client.get(SMALL_V2)

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


# --- AC-18: cache ---


class SelectCounter:
    """Counts the `SELECT`s on `api_keys` that look a key up (both engines of the app)."""

    def __init__(self, app: FastAPI) -> None:
        self.count = 0
        self.statements: list[str] = []
        for engine in {app.state.engine, app.state.worker_engine}:
            event.listen(engine.sync_engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        lowered = " ".join(statement.lower().split())
        if lowered.startswith("select") and "api_keys" in lowered:
            self.count += 1
            self.statements.append(statement)


async def test_ac18_1000_requests_with_a_key_cost_one_select(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(
        rate_limit_per_minute=HIGH,
        rate_limit_per_hour=HIGH,
        rate_limit_key_per_minute=HIGH,
        rate_limit_key_per_hour=HIGH,
    )
    key = await account_with_key(env.factory)
    counter = SelectCounter(env.app)
    async with env.client(IP_A, hdr(key.value)) as client:
        codes = await statuses(client, SMALL_V2, 1000)

    assert codes == [200] * 1000
    assert counter.count == 1, counter.statements


async def test_ac18_unknown_keys_are_cached_too(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    counter = SelectCounter(env.app)
    stranger = new_key()
    async with env.client(IP_A, hdr(stranger)) as client:
        codes = await statuses(client, SMALL_V2, 15)  # below the lockout of 20

    assert codes == [401] * 15
    assert counter.count == 1, counter.statements


async def test_ac18_two_keys_of_one_account_cost_one_select_each(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "pair@example.org")
    first = await create_key(env.factory, account_id)
    second = await create_key(env.factory, account_id)
    counter = SelectCounter(env.app)
    async with (
        env.client(IP_A, hdr(first.value)) as one,
        env.client(IP_A, hdr(second.value)) as two,
    ):
        for _ in range(10):
            await one.get(SMALL_V2)
            await two.get(SMALL_V2)

    assert counter.count == 2, counter.statements


# --- AC-18: usage counters ---


async def test_ac18_flush_writes_1000_requests_for_today_and_sets_last_used_at(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(
        rate_limit_per_minute=HIGH,
        rate_limit_per_hour=HIGH,
        rate_limit_key_per_minute=HIGH,
        rate_limit_key_per_hour=HIGH,
    )
    key = await account_with_key(env.factory)
    before = datetime.now(UTC)
    async with env.client(IP_A, hdr(key.value)) as client:
        await statuses(client, SMALL_V2, 1000)

    await flush_usage(env.app)

    usage = await rows(
        env.factory, "SELECT account_id, key_id, day, requests, rejected FROM api_key_usage_daily"
    )
    assert len(usage) == 1
    assert (usage[0]["account_id"], usage[0]["key_id"]) == (key.account_id, key.id)
    assert usage[0]["requests"] == 1000
    assert usage[0]["rejected"] == 0
    assert usage[0]["day"] in {before.date(), datetime.now(UTC).date()}
    last_used = await scalar(
        env.factory, "SELECT last_used_at FROM api_keys WHERE id = :k", k=key.id
    )
    assert last_used is not None
    assert before - timedelta(minutes=1) <= last_used <= datetime.now(UTC) + timedelta(minutes=1)


async def test_ac18_a_second_flush_does_not_count_twice_and_later_requests_add_up(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        await statuses(client, SMALL_V2, 7)
        await flush_usage(env.app)
        await flush_usage(env.app)
        first = await scalar(
            env.factory, "SELECT sum(requests) FROM api_key_usage_daily WHERE key_id = :k", k=key.id
        )
        await statuses(client, SMALL_V2, 5)
        await flush_usage(env.app)
        second = await scalar(
            env.factory, "SELECT sum(requests) FROM api_key_usage_daily WHERE key_id = :k", k=key.id
        )

    assert first == 7
    assert second == 12


async def test_ac18_usage_is_kept_per_key_and_per_account(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "pair@example.org")
    first = await create_key(env.factory, account_id)
    second = await create_key(env.factory, account_id)
    other = await account_with_key(env.factory, "other@example.org")
    async with (
        env.client(IP_A, hdr(first.value)) as one,
        env.client(IP_A, hdr(second.value)) as two,
        env.client(IP_A, hdr(other.value)) as three,
    ):
        await statuses(one, SMALL_V2, 3)
        await statuses(two, SMALL_V2, 4)
        await statuses(three, SMALL_V2, 2)

    await flush_usage(env.app)

    by_key = {
        r["key_id"]: (r["account_id"], r["requests"])
        for r in await rows(
            env.factory, "SELECT key_id, account_id, requests FROM api_key_usage_daily"
        )
    }
    assert by_key == {
        first.id: (account_id, 3),
        second.id: (account_id, 4),
        other.id: (other.account_id, 2),
    }


async def test_ac18_rejected_requests_are_counted_as_rejected(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=3)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        codes = await statuses(client, SMALL_V2, 5)

    await flush_usage(env.app)

    assert codes == [200, 200, 200, 429, 429]
    usage = await rows(env.factory, "SELECT requests, rejected FROM api_key_usage_daily")
    assert len(usage) == 1
    assert usage[0]["rejected"] == 2
    assert usage[0]["requests"] == 5  # 429 answers count as requests too


async def test_ac18_requests_without_a_key_and_with_a_failed_key_leave_no_usage(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        await statuses(client, SMALL_V2, 3)
        await statuses(client, SMALL_V2, 3, headers=hdr(new_key()))

    await flush_usage(env.app)

    assert await scalar(env.factory, "SELECT count(*) FROM api_key_usage_daily") == 0


async def test_ac18_restart_loses_nothing_that_was_flushed(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        await statuses(client, SMALL_V2, 6)
    await flush_usage(env.app)

    restarted: KeysApp = await make_keys_app()
    async with restarted.client(IP_A, hdr(key.value)) as client:
        await statuses(client, SMALL_V2, 4)
    await flush_usage(restarted.app)

    assert (
        await scalar(
            env.factory, "SELECT sum(requests) FROM api_key_usage_daily WHERE key_id = :k", k=key.id
        )
        == 10
    )
