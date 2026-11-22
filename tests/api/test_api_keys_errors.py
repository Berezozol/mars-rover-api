"""Errors of a bad key, the failed-key lockout and cache invalidation (stage 20A, AC-9 ... AC-13,
AC-17).

Notation of the spec: K - an active key, R - a revoked key, B - a key of a blocked account,
N - a well-formed key that is not in the database, NASA - a value of a foreign format.
v2 answers `401 invalid_api_key | api_key_revoked` and `403 account_blocked`; v1 answers
`403 {"errors": "Invalid API key" | "API key revoked" | "Account blocked"}`. Both carry
`Cache-Control: no-store` and the `X-RateLimit-*` headers of the IP tier.
"""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text

from tests._apikeys import (
    IP_A,
    IP_B,
    SMALL_V1,
    SMALL_V1_ALT,
    SMALL_V2,
    V1_BODY_429,
    V2_BODY_429,
    KeysApp,
    account_with_key,
    assert_v1_error,
    assert_v2_error,
    create_account,
    create_key,
    nasa_key,
    new_key,
    remaining,
    retry_after,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]


def hdr(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


async def revoked_key(env: KeysApp) -> str:
    account = await create_account(env.factory, "revoked@example.org")
    return (await create_key(env.factory, account, revoked=True)).value


async def blocked_key(env: KeysApp) -> str:
    account = await create_account(
        env.factory, "blocked@example.org", blocked=True, block_reason="abuse"
    )
    return (await create_key(env.factory, account)).value


# --- AC-9: a foreign value in the v2 header ---

FOREIGN = [
    pytest.param("DEMO_KEY", id="demo-key"),
    pytest.param(nasa_key(), id="nasa-40-chars"),
    pytest.param("SI_" + "a" * 32, id="upper-case-prefix"),
    pytest.param("si_short", id="short"),
]


@pytest.mark.parametrize("value", FOREIGN)
async def test_ac9_v2_header_of_a_foreign_format_is_401_invalid_api_key(
    make_keys_app: MakeApp, value: str
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, headers=hdr(value))

    assert_v2_error(response, 401, "invalid_api_key")


@pytest.mark.parametrize("value", FOREIGN)
async def test_ac9_the_same_value_in_the_v2_query_is_ignored(
    make_keys_app: MakeApp, value: str
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, params={"api_key": value})

    assert response.status_code == 200


# --- AC-10: N ---


@pytest.mark.parametrize("how", ["header", "query"])
async def test_ac10_v2_unknown_key_is_401_invalid_api_key(make_keys_app: MakeApp, how: str) -> None:
    env: KeysApp = await make_keys_app()
    key = new_key()
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V2,
            headers=hdr(key) if how == "header" else None,
            params={"api_key": key} if how == "query" else None,
        )

    error = assert_v2_error(response, 401, "invalid_api_key")
    assert response.headers["x-ratelimit-limit"] == "60"
    assert key not in response.text
    assert error["message"]


@pytest.mark.parametrize("url", [SMALL_V1, SMALL_V1_ALT], ids=["api-v1", "mars-photos-api-v1"])
@pytest.mark.parametrize("how", ["header", "query"])
async def test_ac10_v1_unknown_key_is_403_invalid_api_key(
    make_keys_app: MakeApp, url: str, how: str
) -> None:
    env: KeysApp = await make_keys_app()
    key = new_key()
    async with env.client(IP_A) as client:
        response = await client.get(
            url,
            headers=hdr(key) if how == "header" else None,
            params={"api_key": key} if how == "query" else None,
        )

    assert_v1_error(response, 403, "Invalid API key")
    assert response.headers["x-ratelimit-limit"] == "60"
    assert key not in response.text


async def test_ac10_real_v1_and_v2_endpoints_answer_the_same_way(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = new_key()
    async with env.client(IP_A) as client:
        v2 = await client.get("/api/v2/rovers", headers=hdr(key))
        v1 = await client.get("/api/v1/rovers", params={"api_key": key})
        v1_photos = await client.get("/api/v1/rovers/curiosity/photos?sol=1", headers=hdr(key))

    assert_v2_error(v2, 401, "invalid_api_key")
    assert_v1_error(v1, 403, "Invalid API key")
    assert_v1_error(v1_photos, 403, "Invalid API key")


async def test_ac10_a_failed_key_is_charged_to_the_ip_limit(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        failed = await client.get(SMALL_V2, headers=hdr(new_key()))
        anonymous = await client.get(SMALL_V2)

    assert remaining(failed) == 59
    assert remaining(anonymous) == 58


async def test_ac10_a_failed_key_is_still_limited_by_the_ip_budget(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=2)
    async with env.client(IP_A) as client:
        await client.get(SMALL_V2)
        failed = await client.get(SMALL_V2, headers=hdr(new_key()))
        limited = await client.get(SMALL_V2, headers=hdr(new_key()))

    assert failed.status_code == 401
    assert limited.status_code == 429
    assert limited.json() == V2_BODY_429


@pytest.mark.parametrize("path", ["/health", "/"])
async def test_ac10_paths_without_a_limit_do_not_check_the_key(
    make_keys_app: MakeApp, path: str
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        response = await client.get(path, headers=hdr(new_key()))

    assert response.status_code not in (401, 403, 429)


# --- AC-11: R ---


@pytest.mark.parametrize("how", ["header", "query"])
async def test_ac11_v2_revoked_key_is_401_api_key_revoked(make_keys_app: MakeApp, how: str) -> None:
    env: KeysApp = await make_keys_app()
    key = await revoked_key(env)
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V2,
            headers=hdr(key) if how == "header" else None,
            params={"api_key": key} if how == "query" else None,
        )

    error = assert_v2_error(response, 401, "api_key_revoked")
    assert error["message"] == "API key has been revoked"
    assert response.headers["x-ratelimit-limit"] == "60"


@pytest.mark.parametrize("how", ["header", "query"])
async def test_ac11_v1_revoked_key_is_403_api_key_revoked(make_keys_app: MakeApp, how: str) -> None:
    env: KeysApp = await make_keys_app()
    key = await revoked_key(env)
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V1,
            headers=hdr(key) if how == "header" else None,
            params={"api_key": key} if how == "query" else None,
        )

    assert_v1_error(response, 403, "API key revoked")


async def test_ac11_a_revoked_key_does_not_fall_back_to_the_anonymous_tier(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await revoked_key(env)
    async with env.client(IP_A) as client:
        response = await client.get("/api/v2/rovers", headers=hdr(key))

    assert response.status_code == 401
    assert "items" not in response.json()


# --- AC-12: B ---


@pytest.mark.parametrize("how", ["header", "query"])
async def test_ac12_v2_key_of_a_blocked_account_is_403_account_blocked(
    make_keys_app: MakeApp, how: str
) -> None:
    env: KeysApp = await make_keys_app()
    key = await blocked_key(env)
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V2,
            headers=hdr(key) if how == "header" else None,
            params={"api_key": key} if how == "query" else None,
        )

    assert_v2_error(response, 403, "account_blocked")
    assert response.headers["x-ratelimit-limit"] == "60"


@pytest.mark.parametrize("how", ["header", "query"])
async def test_ac12_v1_key_of_a_blocked_account_is_403_account_blocked(
    make_keys_app: MakeApp, how: str
) -> None:
    env: KeysApp = await make_keys_app()
    key = await blocked_key(env)
    async with env.client(IP_A) as client:
        response = await client.get(
            SMALL_V1,
            headers=hdr(key) if how == "header" else None,
            params={"api_key": key} if how == "query" else None,
        )

    assert_v1_error(response, 403, "Account blocked")


async def test_ac12_the_block_message_does_not_leak_the_reason(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await blocked_key(env)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, headers=hdr(key))

    assert "abuse" not in response.text


# --- AC-13: lockout of failed keys per IP ---


async def test_ac13_21st_failed_key_from_an_ip_is_429_and_even_a_good_key_is_not_checked(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    good = await account_with_key(env.factory)
    spent = await revoked_key(env)
    async with env.client(IP_A) as client, env.client(IP_B) as other, env.client(IP_A) as anon:
        failures = []
        for index in range(20):
            key = new_key() if index % 2 == 0 else spent
            failures.append((await client.get(SMALL_V2, headers=hdr(key))).status_code)
        locked = await client.get(SMALL_V2, headers=hdr(new_key()))
        locked_good = await client.get(SMALL_V2, headers=hdr(good.value))
        locked_query = await client.get(SMALL_V2, params={"api_key": good.value})
        anonymous = await anon.get(SMALL_V2)
        other_ip = await other.get(SMALL_V2, headers=hdr(good.value))

    assert failures == [401] * 20
    for response in (locked, locked_good, locked_query):
        assert response.status_code == 429, response.text
        assert response.json() == V2_BODY_429
        assert 890 <= retry_after(response) <= 900  # to the end of the 15 minute window
        assert response.headers["cache-control"] == "no-store"
    assert anonymous.status_code == 200  # requests without a key still go by the IP limit
    assert other_ip.status_code == 200  # another IP is not affected


async def test_ac13_19_failures_do_not_lock_the_ip(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    good = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        for _ in range(19):
            assert (await client.get(SMALL_V2, headers=hdr(new_key()))).status_code == 401

        response = await client.get(SMALL_V2, headers=hdr(good.value))

    assert response.status_code == 200


async def test_ac13_v1_lockout_uses_the_v1_body(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        for _ in range(20):
            assert (await client.get(SMALL_V1, params={"api_key": new_key()})).status_code == 403

        locked = await client.get(SMALL_V1, params={"api_key": new_key()})

    assert locked.status_code == 429
    assert locked.json() == V1_BODY_429
    assert 890 <= retry_after(locked) <= 900


async def test_ac13_lockout_is_shared_by_v1_and_v2(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        for index in range(20):
            url = SMALL_V1 if index % 2 else SMALL_V2
            await client.get(url, headers=hdr(new_key()))

        locked = await client.get(SMALL_V2, headers=hdr(new_key()))

    assert locked.status_code == 429


async def test_ac13_the_lockout_ends_with_the_window(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    good = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        for _ in range(20):
            await client.get(SMALL_V2, headers=hdr(new_key()))
        locked = await client.get(SMALL_V2, headers=hdr(good.value))
        env.clock.advance(retry_after(locked) + 1)

        response = await client.get(SMALL_V2, headers=hdr(good.value))

    assert locked.status_code == 429
    assert response.status_code == 200


async def test_ac13_foreign_query_keys_are_not_failures(make_keys_app: MakeApp) -> None:
    """`DEMO_KEY` and friends are ignored, so they never count towards the lockout."""
    env: KeysApp = await make_keys_app(rate_limit_per_minute=1000)
    good = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        for _ in range(30):
            assert (await client.get(SMALL_V1, params={"api_key": "DEMO_KEY"})).status_code == 200

        response = await client.get(SMALL_V2, headers=hdr(good.value))

    assert response.status_code == 200


async def test_ac13_the_failure_limit_follows_the_setting(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(api_key_auth_max_failures=3)
    async with env.client(IP_A) as client:
        codes = [(await client.get(SMALL_V2, headers=hdr(new_key()))).status_code for _ in range(4)]

    assert codes == [401, 401, 401, 429]


# --- AC-17: revoke / block act at once, direct database changes by the cache TTL ---


async def admin_post(env: KeysApp, path: str, **kwargs: Any) -> Any:
    async with env.admin() as admin:
        return await admin.post(path, **kwargs)


async def test_ac17_admin_revoke_acts_on_the_next_request_in_both_versions(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 200  # the key is cached

        revoke = await admin_post(env, f"/api/v2/admin/keys/{key.id}/revoke")
        assert revoke.status_code == 200, revoke.text
        v2 = await client.get(SMALL_V2)
        v1 = await client.get(SMALL_V1)

    assert_v2_error(v2, 401, "api_key_revoked")
    assert_v1_error(v1, 403, "API key revoked")


async def test_ac17_admin_block_acts_at_once_and_unblock_restores_the_key(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 200

        block = await admin_post(
            env, f"/api/v2/admin/accounts/{key.account_id}/block", json={"reason": "abuse"}
        )
        assert block.status_code == 200, block.text
        blocked_v2 = await client.get(SMALL_V2)
        blocked_v1 = await client.get(SMALL_V1)
        unblock = await admin_post(env, f"/api/v2/admin/accounts/{key.account_id}/unblock")
        assert unblock.status_code == 200, unblock.text
        restored = await client.get(SMALL_V2)

    assert_v2_error(blocked_v2, 403, "account_blocked")
    assert_v1_error(blocked_v1, 403, "Account blocked")
    assert restored.status_code == 200
    assert restored.headers["x-ratelimit-limit"] == "120"


async def test_ac17_revoking_one_key_leaves_the_other_key_of_the_account_working(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "pair@example.org")
    first = await create_key(env.factory, account_id)
    second = await create_key(env.factory, account_id)
    async with (
        env.client(IP_A, hdr(first.value)) as one,
        env.client(IP_B, hdr(second.value)) as two,
    ):
        await one.get(SMALL_V2)
        await two.get(SMALL_V2)

        await admin_post(env, f"/api/v2/admin/keys/{first.id}/revoke")

        assert (await one.get(SMALL_V2)).status_code == 401
        assert (await two.get(SMALL_V2)).status_code == 200


async def test_ac17_admin_delete_of_the_account_acts_at_once(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 200
        async with env.admin() as admin:
            deleted = await admin.delete(f"/api/v2/admin/accounts/{key.account_id}")
        assert deleted.status_code == 204

        response = await client.get(SMALL_V2)

    assert_v2_error(response, 401, "invalid_api_key")


async def test_ac17_a_key_added_after_a_miss_is_found_after_the_cache_time(
    make_keys_app: MakeApp,
) -> None:
    """Negative answers are cached too, for no longer than API_KEY_CACHE_SECONDS."""
    env: KeysApp = await make_keys_app(api_key_cache_seconds=60)
    resolver = env.app.state.api_key_resolver
    assert hasattr(resolver, "clock"), "ApiKeyResolver needs a clock attribute (fake time source)"
    account_id = await create_account(env.factory, "late@example.org")
    value = new_key()
    async with env.client(IP_A, hdr(value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 401
        await create_key(env.factory, account_id, key=value)
        env.clock.advance(61)

        response = await client.get(SMALL_V2)

    assert response.status_code == 200


async def test_ac17_a_change_made_behind_the_apps_back_acts_within_the_cache_time(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(api_key_cache_seconds=60)
    resolver = env.app.state.api_key_resolver
    assert hasattr(resolver, "clock"), "ApiKeyResolver needs a clock attribute (fake time source)"
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 200

        async with env.factory() as session:  # what the CLI would do
            await session.execute(
                text("UPDATE api_keys SET revoked_at = now(), revoke_reason = 'admin'")
            )
            await session.commit()
        cached = await client.get(SMALL_V2)
        env.clock.advance(61)
        after_ttl = await client.get(SMALL_V2)

    assert cached.status_code == 200  # still the cached answer inside the TTL
    assert_v2_error(after_ttl, 401, "api_key_revoked")


async def test_ac17_a_block_made_behind_the_apps_back_acts_within_the_cache_time(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(api_key_cache_seconds=60)
    resolver = env.app.state.api_key_resolver
    assert hasattr(resolver, "clock"), "ApiKeyResolver needs a clock attribute (fake time source)"
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 200
        async with env.factory() as session:
            await session.execute(
                text("UPDATE api_accounts SET status = 'blocked', blocked_at = now()")
            )
            await session.commit()
        env.clock.advance(61)

        response = await client.get(SMALL_V2)

    assert_v2_error(response, 403, "account_blocked")
