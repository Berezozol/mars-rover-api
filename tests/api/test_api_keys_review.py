"""Stage 20A review round: lockout details, bursts of unknown keys, redirects, v1 header
handling, admin input limits, key lookup failure, concurrent issue.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.api.apikeys import ResolvedKey
from tests._apikeys import (
    IP_A,
    SMALL_V1,
    SMALL_V2,
    V2_BODY_429,
    KeysApp,
    account_with_key,
    new_key,
    retry_after,
    scalar,
)
from tests.api.v2.conftest import assert_error

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
HIGH = 100_000
ACCOUNTS = "/api/v2/admin/accounts"


def hdr(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


# --- lockout ---


async def lock_out(env: KeysApp, client: Any) -> None:
    for _ in range(20):
        assert (await client.get(SMALL_V2, headers=hdr(new_key()))).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(hdr("DEMO_KEY"), id="v2-header-foreign-format"),
        pytest.param(hdr("SI_" + "a" * 32), id="v2-header-upper-case-prefix"),
        pytest.param(hdr(new_key()), id="v2-header-unknown-key"),
    ],
)
async def test_ac13_locked_ip_gets_429_for_every_non_ignored_key(
    make_keys_app: MakeApp, headers: dict[str, str]
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        await lock_out(env, client)

        response = await client.get(SMALL_V2, headers=headers)

    assert response.status_code == 429, response.text
    assert response.json() == V2_BODY_429


async def test_ac13_requests_during_the_lockout_are_not_counted_as_new_failures(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    good = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        await lock_out(env, client)
        env.clock.advance(600)
        for headers in (hdr("DEMO_KEY"), hdr(new_key()), hdr(good.value)):
            assert (await client.get(SMALL_V2, headers=headers)).status_code == 429
        env.clock.advance(301)  # 901 s after the 20 failures, 301 s after the locked requests

        response = await client.get(SMALL_V2, headers=hdr(good.value))

    assert response.status_code == 200, response.text


async def test_ac13_burst_of_unknown_keys_reaches_the_resolver_at_most_concurrency_times(
    make_keys_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH, rate_limit_concurrent=4)
    gate = asyncio.Event()
    calls: list[str] = []

    async def slow_resolve(key: str) -> ResolvedKey:
        calls.append(key)
        await gate.wait()
        return ResolvedKey("invalid")

    monkeypatch.setattr(env.app.state.api_key_resolver, "resolve", slow_resolve)
    tasks = [
        asyncio.ensure_future(env.client(IP_A).get(SMALL_V2, headers=hdr(new_key())))
        for _ in range(12)
    ]
    try:
        for _ in range(200_000):
            if len(calls) >= 4 and sum(t.done() for t in tasks) >= 8:
                break
            await asyncio.sleep(0)
    finally:
        gate.set()
    responses = await asyncio.gather(*tasks)

    assert len(calls) == 4
    assert sorted(r.status_code for r in responses) == [401] * 4 + [429] * 8


# --- v1 header handling ---


async def test_v1_foreign_header_does_not_hide_a_valid_query_key(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V1, headers=hdr("DEMO_KEY"), params={"api_key": key.value}
        )

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


# --- redirects do not echo the key ---


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/api/v2/rovers/", id="v2"),
        pytest.param("/api/v1/rovers/", id="v1"),
        pytest.param("/mars-photos/api/v1/rovers/", id="v1-mars-photos"),
    ],
)
async def test_ac32_trailing_slash_redirect_location_has_no_key_value(
    make_keys_app: MakeApp, path: str
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(path, params={"api_key": key.value})

    assert response.status_code in (301, 302, 307, 308), response.text
    location = response.headers["location"]
    assert key.value not in location
    assert key.value[3:] not in location


# --- admin input limits ---

HUGE_IDS = ["9223372036854775808", "99999999999999999999999"]
ID_ROUTES = [
    pytest.param("GET", ACCOUNTS + "/{id}", {}, id="detail"),
    pytest.param("POST", ACCOUNTS + "/{id}/block", {"json": {"reason": "x"}}, id="block"),
    pytest.param("POST", ACCOUNTS + "/{id}/unblock", {}, id="unblock"),
    pytest.param("DELETE", ACCOUNTS + "/{id}", {}, id="delete"),
    pytest.param("POST", "/api/v2/admin/keys/{id}/revoke", {}, id="revoke"),
]


@pytest.mark.parametrize("big", HUGE_IDS)
@pytest.mark.parametrize(("method", "path", "kwargs"), ID_ROUTES)
async def test_ac30_an_id_beyond_bigint_is_404_not_a_server_error(
    make_keys_app: MakeApp, method: str, path: str, kwargs: dict[str, Any], big: str
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.request(method, path.format(id=big), **kwargs)

    assert_error(response, 404, "not_found")


@pytest.mark.parametrize(
    "email",
    [
        pytest.param("a\x00b@example.org", id="nul"),
        pytest.param("a@example.org\x00", id="trailing-nul"),
        pytest.param("ü@example.org", id="non-ascii-local"),
        pytest.param("a@exämple.org", id="non-ascii-domain"),
        pytest.param("почта@example.org", id="cyrillic"),
    ],
)
async def test_ac29_email_with_nul_or_non_ascii_is_422(make_keys_app: MakeApp, email: str) -> None:
    env: KeysApp = await make_keys_app()
    async with env.admin() as admin:
        response = await admin.post(ACCOUNTS, json={"email": email})

    assert_error(response, 422, "validation_error")
    assert await scalar(env.factory, "SELECT count(*) FROM api_accounts") == 0


# --- concurrent issue ---


async def test_ac29_three_concurrent_issues_for_a_new_email_give_two_201_and_one_409(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    clients = [env.admin() for _ in range(3)]

    responses = await asyncio.gather(
        *(c.post(ACCOUNTS, json={"email": "race@example.org"}) for c in clients)
    )

    assert sorted(r.status_code for r in responses) == [201, 201, 409], [
        (r.status_code, r.text) for r in responses
    ]
    assert await scalar(env.factory, "SELECT count(*) FROM api_accounts") == 1
    assert await scalar(env.factory, "SELECT count(*) FROM api_keys") == 2
    keys = [r.json()["key"] for r in responses if r.status_code == 201]
    assert len(set(keys)) == 2


# --- key lookup failure ---


async def failing_resolve(key: str) -> ResolvedKey:
    raise OperationalError("SELECT 1", {}, Exception("connection refused"))


async def test_a_database_failure_during_key_lookup_is_503_service_busy_in_v2(
    make_keys_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    env: KeysApp = await make_keys_app()
    monkeypatch.setattr(env.app.state.api_key_resolver, "resolve", failing_resolve)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, headers=hdr(new_key()))

    assert response.status_code == 503, response.text
    assert response.json() == {
        "error": {"code": "service_busy", "message": "Server is busy, retry later", "details": None}
    }
    assert retry_after(response) >= 1
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("how", ["header", "query"])
async def test_a_database_failure_during_key_lookup_is_503_in_v1(
    make_keys_app: MakeApp, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    env: KeysApp = await make_keys_app()
    monkeypatch.setattr(env.app.state.api_key_resolver, "resolve", failing_resolve)
    key = new_key()
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V1,
            headers=hdr(key) if how == "header" else None,
            params={"api_key": key} if how == "query" else None,
        )

    assert response.status_code == 503, response.text
    assert response.json() == {"errors": "Server is busy"}
    assert retry_after(response) >= 1
    assert response.headers["cache-control"] == "no-store"


async def test_a_lookup_failure_is_not_a_failed_key_and_never_locks_the_ip(
    make_keys_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH)
    good = await account_with_key(env.factory)
    with monkeypatch.context() as patch:
        patch.setattr(env.app.state.api_key_resolver, "resolve", failing_resolve)
        async with env.client(IP_A) as client:
            codes = [
                (await client.get(SMALL_V2, headers=hdr(good.value))).status_code for _ in range(25)
            ]
    async with env.client(IP_A) as client:
        after = await client.get(SMALL_V2, headers=hdr(good.value))

    assert codes == [503] * 25
    assert after.status_code == 200, after.text
