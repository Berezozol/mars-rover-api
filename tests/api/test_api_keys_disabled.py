"""Kill switch: `API_KEYS_ENABLED=false` means the behaviour before stage 20 (AC-19).

Any `api_key` / `X-Api-Key` is ignored (a good, an unknown and a foreign one alike), the limit
is per IP with the old windows (60 per minute, 2000 per hour, 4 at a time), and the routes of
the stage (`/api/v2/keys*`, `/api/v2/account*`, the admin account and key routes) do not exist
(404) even with a valid admin token. The keys are put into the database by hand.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from tests._apikeys import (
    HOLD_V2,
    IP_A,
    SMALL_V1,
    SMALL_V2,
    V1_BODY_429,
    V2_BODY_429,
    KeysApp,
    account_with_key,
    create_account,
    create_key,
    new_key,
    remaining,
    statuses,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
HIGH = 100_000


def hdr(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


@pytest.mark.parametrize("how", ["header", "query"])
async def test_ac19_a_valid_key_gets_no_extended_limit(make_keys_app: MakeApp, how: str) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V2,
            headers=hdr(key.value) if how == "header" else None,
            params={"api_key": key.value} if how == "query" else None,
        )

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "60"
    assert remaining(response) == 59


async def test_ac19_the_limit_is_per_ip_with_the_old_windows(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        codes = await statuses(client, SMALL_V2, 61)
        blocked = await client.get(SMALL_V2)

    assert codes == [200] * 60 + [429]
    assert blocked.json() == V2_BODY_429


async def test_ac19_the_hour_window_is_still_2000(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False, rate_limit_per_minute=HIGH)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2)

    assert response.headers["x-ratelimit-limit"] == "2000"


async def test_ac19_v1_keys_are_ignored_and_share_the_ip_counter(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False, rate_limit_per_minute=2)
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        codes = [
            (await client.get(SMALL_V1, params={"api_key": key.value})).status_code,
            (await client.get(SMALL_V1, headers=hdr(key.value))).status_code,
            (await client.get(SMALL_V1, params={"api_key": key.value})).status_code,
        ]
        blocked = await client.get(SMALL_V1)

    assert codes == [200, 200, 429]
    assert blocked.json() == V1_BODY_429


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(hdr("si_" + "a" * 32), id="unknown-key"),
        pytest.param(hdr("DEMO_KEY"), id="foreign-value"),
        pytest.param(hdr("si_short"), id="malformed"),
    ],
)
async def test_ac19_bad_keys_are_not_errors(
    make_keys_app: MakeApp, headers: dict[str, str]
) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    async with env.client(IP_A) as client:
        v2 = await client.get(SMALL_V2, headers=headers)
        v1 = await client.get(SMALL_V1, headers=headers)
        by_query = await client.get(SMALL_V2, params={"api_key": new_key()})

    assert (v2.status_code, v1.status_code, by_query.status_code) == (200, 200, 200)


async def test_ac19_revoked_and_blocked_keys_are_not_errors(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    revoked = await create_key(
        env.factory, await create_account(env.factory, "r@example.org"), revoked=True
    )
    blocked = await create_key(
        env.factory, await create_account(env.factory, "b@example.org", blocked=True)
    )
    async with env.client(IP_A) as client:
        codes = [
            (await client.get(SMALL_V2, headers=hdr(revoked.value))).status_code,
            (await client.get(SMALL_V2, headers=hdr(blocked.value))).status_code,
        ]

    assert codes == [200, 200]


async def test_ac19_failed_keys_do_not_lock_the_ip(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False, rate_limit_per_minute=HIGH)
    async with env.client(IP_A) as client:
        codes = await statuses(client, SMALL_V2, 30, headers=hdr(new_key()))

    assert codes == [200] * 30


async def test_ac19_concurrency_stays_at_4_for_a_keyed_client(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False, rate_limit_per_minute=HIGH)
    key = await account_with_key(env.factory)
    held = [asyncio.ensure_future(env.client(IP_A, hdr(key.value)).get(HOLD_V2)) for _ in range(4)]
    try:
        await env.wait_entered(4)
        fifth = await env.client(IP_A, hdr(key.value)).get(HOLD_V2)
    finally:
        env.release.set()
        await asyncio.gather(*held)

    assert fifth.status_code == 429
    assert fifth.headers["retry-after"] == "1"


KEY_ROUTES = [
    pytest.param(
        "POST", "/api/v2/keys/requests", {"json": {"email": "a@example.org"}}, id="request"
    ),
    pytest.param("POST", "/api/v2/keys/confirm", {"json": {"token": "x"}}, id="confirm"),
    pytest.param("GET", "/api/v2/account", {}, id="account"),
    pytest.param("POST", "/api/v2/account/keys", {}, id="account-new-key"),
    pytest.param("DELETE", "/api/v2/account/keys/1", {}, id="account-revoke-key"),
    pytest.param("DELETE", "/api/v2/account", {}, id="account-delete"),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), KEY_ROUTES)
async def test_ac19_key_and_account_routes_do_not_exist(
    make_keys_app: MakeApp, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        response = await client.request(method, path, **kwargs)

    assert response.status_code == 404, response.text


ADMIN_ROUTES = [
    pytest.param("GET", "/api/v2/admin/accounts", {}, id="list"),
    pytest.param("GET", "/api/v2/admin/accounts/1", {}, id="detail"),
    pytest.param(
        "POST", "/api/v2/admin/accounts", {"json": {"email": "a@example.org"}}, id="create"
    ),
    pytest.param("POST", "/api/v2/admin/accounts/1/block", {"json": {"reason": "x"}}, id="block"),
    pytest.param("POST", "/api/v2/admin/accounts/1/unblock", {}, id="unblock"),
    pytest.param("POST", "/api/v2/admin/keys/1/revoke", {}, id="revoke"),
    pytest.param("DELETE", "/api/v2/admin/accounts/1", {}, id="delete"),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), ADMIN_ROUTES)
async def test_ac19_admin_account_routes_do_not_exist_even_with_the_admin_token(
    make_keys_app: MakeApp, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    async with env.admin() as admin:
        response = await admin.request(method, path, **kwargs)

    assert response.status_code == 404, response.text


async def test_ac19_the_other_admin_routes_still_work(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    async with env.admin() as admin:
        response = await admin.get("/api/v2/admin/sync/runs")

    assert response.status_code == 200, response.text


async def test_ac19_nothing_is_written_to_the_key_tables(make_keys_app: MakeApp) -> None:
    from tests._apikeys import scalar

    env: KeysApp = await make_keys_app(api_keys_enabled=False)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        await statuses(client, SMALL_V2, 5)

    assert await scalar(env.factory, "SELECT count(*) FROM api_key_usage_daily") == 0
