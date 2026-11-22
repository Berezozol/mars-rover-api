"""Two access tiers and how a key is read (stage 20A, AC-3 ... AC-8, AC-14, AC-16).

Without a key the limit is per IP (60 per minute, 2000 per hour by default, 4 at a time); with
a valid key it is per ACCOUNT (120 / 10 000 / 8), whatever the IP and the number of keys of the
account. The key is read from `X-Api-Key` or `api_key` (the header wins); a value of a foreign
format in `api_key` (api.nasa.gov keys, `DEMO_KEY`) is ignored. Test routes `__small`,
`__blob`, `__hold` are added by `tests/_apikeys.py`; nothing sleeps, time is a fake clock.

Owner decision (2026-11-22): the anonymous hourly limit stays 2000, not the 1000 of the spec's
AC-3 text.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from tests._apikeys import (
    BLOB_V1,
    HOLD_V2,
    IP_A,
    IP_B,
    IP_C,
    SMALL_V1,
    SMALL_V1_ALT,
    SMALL_V2,
    UNIT,
    V1_BODY_429,
    V2_BODY_429,
    KeysApp,
    account_with_key,
    create_account,
    create_key,
    nasa_key,
    new_key,
    remaining,
    retry_after,
    statuses,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
HIGH = 100_000


def hdr(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


# --- AC-3: without a key, per IP ---


async def test_ac3_anonymous_v2_61st_request_in_a_minute_is_429(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        responses = [await client.get(SMALL_V2) for _ in range(60)]
        blocked = await client.get(SMALL_V2)

    assert [r.status_code for r in responses] == [200] * 60
    assert {r.headers["x-ratelimit-limit"] for r in responses} == {"60"}
    assert blocked.status_code == 429
    assert blocked.json() == V2_BODY_429
    assert blocked.headers["x-ratelimit-limit"] == "60"
    assert blocked.headers["cache-control"] == "no-store"
    assert 1 <= retry_after(blocked) <= 60


async def test_ac3_anonymous_hour_window_is_2000_by_default(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH)
    async with env.client(IP_A) as client:
        first = await client.get(SMALL_V2)

    assert first.headers["x-ratelimit-limit"] == "2000"
    assert remaining(first) == 1999


async def test_ac3_anonymous_hour_limit_blocks_with_retry_after_up_to_an_hour(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH, rate_limit_per_hour=5)
    async with env.client(IP_A) as client:
        codes = await statuses(client, SMALL_V2, 5)
        blocked = await client.get(SMALL_V2)

    assert codes == [200] * 5
    assert blocked.status_code == 429
    assert blocked.headers["x-ratelimit-limit"] == "5"
    assert 3000 < retry_after(blocked) <= 3600


async def test_ac3_anonymous_limit_is_per_ip(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=2)
    async with env.client(IP_A) as one, env.client(IP_B) as two:
        assert await statuses(one, SMALL_V2, 3) == [200, 200, 429]
        assert await statuses(two, SMALL_V2, 2) == [200, 200]


# --- AC-4: with a key, per account ---


async def test_ac4_key_gets_120_per_minute_and_the_121st_is_429(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        responses = [await client.get(SMALL_V2) for _ in range(120)]
        blocked = await client.get(SMALL_V2)

    assert [r.status_code for r in responses] == [200] * 120
    assert {r.headers["x-ratelimit-limit"] for r in responses} == {"120"}
    assert remaining(responses[0]) == 119
    assert remaining(responses[-1]) == 0
    assert blocked.status_code == 429
    assert blocked.json() == V2_BODY_429
    assert blocked.headers["x-ratelimit-limit"] == "120"
    assert 1 <= retry_after(blocked) <= 60


async def test_ac4_anonymous_requests_from_the_same_ip_are_not_limited_by_the_key_counter(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as keyed, env.client(IP_A) as anonymous:
        assert await statuses(keyed, SMALL_V2, 121) == [200] * 120 + [429]

        response = await anonymous.get(SMALL_V2)

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "60"
    assert remaining(response) == 59  # the IP counter was not touched by the keyed requests


async def test_ac4_key_hour_window_is_10000_by_default(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=HIGH)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        response = await client.get(SMALL_V2)

    assert response.headers["x-ratelimit-limit"] == "10000"
    assert remaining(response) == 9999


async def test_ac4_key_hour_limit_blocks_the_account(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=HIGH, rate_limit_key_per_hour=4)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        codes = await statuses(client, SMALL_V2, 4)
        blocked = await client.get(SMALL_V2)

    assert codes == [200] * 4
    assert blocked.status_code == 429
    assert blocked.headers["x-ratelimit-limit"] == "4"


async def test_ac4_headers_describe_the_tier_the_request_was_counted_in(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as anonymous, env.client(IP_A, hdr(key.value)) as keyed:
        anon = await anonymous.get(SMALL_V2)
        with_key = await keyed.get(SMALL_V2)

    assert (anon.headers["x-ratelimit-limit"], remaining(anon)) == ("60", 59)
    assert (with_key.headers["x-ratelimit-limit"], remaining(with_key)) == ("120", 119)


async def test_ac4_key_budget_refills_when_the_window_passes(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=3)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        await statuses(client, SMALL_V2, 3)
        blocked = await client.get(SMALL_V2)
        env.clock.advance(retry_after(blocked) + 0.01)

        again = await client.get(SMALL_V2)

    assert again.status_code == 200


# --- AC-5: one budget for the account ---


async def test_ac5_two_keys_of_one_account_from_two_ips_share_120_per_minute(
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
        from_first = await statuses(one, SMALL_V2, 70)
        from_second = await statuses(two, SMALL_V2, 70)
        extra_first = await one.get(SMALL_V2)
        extra_second = await two.get(SMALL_V2)

    assert from_first == [200] * 70
    assert from_second == [200] * 50 + [429] * 20  # 70 + 50 = 120, then the account is spent
    assert extra_first.status_code == 429
    assert extra_second.status_code == 429
    assert extra_first.json() == V2_BODY_429


async def test_ac5_another_account_and_anonymous_clients_have_their_own_budgets(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    spent = await account_with_key(env.factory, "spent@example.org")
    other = await account_with_key(env.factory, "other@example.org")
    async with (
        env.client(IP_A, hdr(spent.value)) as one,
        env.client(IP_B, hdr(other.value)) as two,
        env.client(IP_A) as anonymous,
    ):
        assert (await statuses(one, SMALL_V2, 121))[-1] == 429

        assert (await two.get(SMALL_V2)).status_code == 200
        assert (await anonymous.get(SMALL_V2)).status_code == 200


async def test_ac5_the_ip_of_a_keyed_client_does_not_matter(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    codes = []
    for index in range(122):
        async with env.client(f"203.0.113.{index % 200 + 20}", hdr(key.value)) as client:
            codes.append((await client.get(SMALL_V2)).status_code)

    assert codes == [200] * 120 + [429] * 2


# --- AC-6: v1 ---

V1_URLS = [
    pytest.param(SMALL_V1, id="api-v1"),
    pytest.param(SMALL_V1_ALT, id="mars-photos-api-v1"),
]


@pytest.mark.parametrize("url", V1_URLS)
@pytest.mark.parametrize("how", ["query", "header"])
async def test_ac6_v1_key_gets_the_account_tier(make_keys_app: MakeApp, url: str, how: str) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    headers = hdr(key.value) if how == "header" else None
    params = {"api_key": key.value} if how == "query" else None
    async with env.client(IP_A, headers) as client:
        responses = [await client.get(url, params=params) for _ in range(120)]
        blocked = await client.get(url, params=params)

    assert [r.status_code for r in responses] == [200] * 120
    assert {r.headers["x-ratelimit-limit"] for r in responses} == {"120"}
    assert blocked.status_code == 429
    assert blocked.json() == V1_BODY_429
    assert 1 <= retry_after(blocked) <= 60


async def test_ac6_v1_and_v2_share_the_account_counter(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=3)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 200
        assert (await client.get(SMALL_V1)).status_code == 200
        assert (await client.get(SMALL_V1_ALT)).status_code == 200

        assert (await client.get(SMALL_V2)).status_code == 429
        assert (await client.get(SMALL_V1)).status_code == 429


# --- AC-7: header against query ---


async def test_ac7_v2_query_key_without_header_gets_the_account_tier(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, params={"api_key": key.value})

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


async def test_ac7_header_wins_over_a_query_key_that_does_not_exist(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, headers=hdr(key.value), params={"api_key": new_key()})

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


async def test_ac7_v1_header_wins_over_a_query_key_that_does_not_exist(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V1, headers=hdr(key.value), params={"api_key": new_key()})

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


@pytest.mark.parametrize(
    ("url", "status", "body"),
    [
        pytest.param(SMALL_V2, 401, {"error": {"code": "invalid_api_key"}}, id="v2"),
        pytest.param(SMALL_V1, 403, {"errors": "Invalid API key"}, id="v1"),
    ],
)
async def test_ac7_a_bad_header_is_not_rescued_by_a_good_query_key(
    make_keys_app: MakeApp, url: str, status: int, body: dict[str, Any]
) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(url, headers=hdr(new_key()), params={"api_key": key.value})

    assert response.status_code == status, response.text
    assert response.json().get("error", {}).get("code") == body.get("error", {}).get("code")
    if "errors" in body:
        assert response.json() == body


async def test_ac7_v1_header_with_a_foreign_value_is_ignored_like_a_foreign_api_key(
    make_keys_app: MakeApp,
) -> None:
    """v1 reads the header like `api_key`: a foreign value is ignored, the request goes on."""
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V1, headers=hdr("DEMO_KEY"))

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "60"


async def test_ac7_repeated_api_key_takes_the_first_value(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        good_first = await client.get(f"{SMALL_V2}?api_key={key.value}&api_key={new_key()}")
        bad_first = await client.get(f"{SMALL_V2}?api_key={new_key()}&api_key={key.value}")

    assert good_first.status_code == 200
    assert good_first.headers["x-ratelimit-limit"] == "120"
    assert bad_first.status_code == 401
    assert bad_first.json()["error"]["code"] == "invalid_api_key"


async def test_ac7_empty_header_is_the_same_as_no_header(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        alone = await client.get(SMALL_V2, headers={"X-Api-Key": ""})
        with_query = await client.get(
            SMALL_V2, headers={"X-Api-Key": ""}, params={"api_key": key.value}
        )

    assert alone.status_code == 200
    assert alone.headers["x-ratelimit-limit"] == "60"
    assert with_query.status_code == 200
    assert with_query.headers["x-ratelimit-limit"] == "120"


async def test_ac7_spaces_around_the_header_value_are_trimmed(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, headers={"X-Api-Key": f"  {key.value} "})

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "120"


async def test_ac7_header_name_is_case_insensitive(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V2, headers={"x-API-key": key.value})

    assert response.headers["x-ratelimit-limit"] == "120"


# --- AC-8: foreign formats are ignored in `api_key` ---

FOREIGN = [
    pytest.param("DEMO_KEY", id="demo-key"),
    pytest.param(nasa_key(), id="nasa-40-chars"),
    pytest.param("SI_" + "a" * 32, id="upper-case-prefix"),
    pytest.param("si_" + "a" * 31, id="31-chars"),
    pytest.param("si_" + "a" * 33, id="33-chars"),
    pytest.param("si_" + "a" * 31 + "-", id="dash-inside"),
]


@pytest.mark.parametrize("value", FOREIGN)
@pytest.mark.parametrize("url", [SMALL_V2, SMALL_V1], ids=["v2", "v1"])
async def test_ac8_foreign_api_key_in_the_query_is_ignored(
    make_keys_app: MakeApp, url: str, value: str
) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        response = await client.get(url, params={"api_key": value})

    assert response.status_code == 200, response.text
    assert response.headers["x-ratelimit-limit"] == "60"
    assert remaining(response) == 59


@pytest.mark.parametrize("value", FOREIGN)
async def test_ac8_v1_foreign_header_is_ignored(make_keys_app: MakeApp, value: str) -> None:
    env: KeysApp = await make_keys_app()
    async with env.client(IP_A) as client:
        response = await client.get(SMALL_V1, headers=hdr(value))

    assert response.status_code == 200, response.text
    assert response.headers["x-ratelimit-limit"] == "60"


async def test_ac8_foreign_keys_are_counted_per_ip(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=3)
    async with env.client(IP_A) as client:
        codes = [
            (await client.get(SMALL_V1, params={"api_key": value})).status_code
            for value in ("DEMO_KEY", nasa_key(), "other")
        ]
        blocked = await client.get(SMALL_V1, params={"api_key": "DEMO_KEY"})

    assert codes == [200, 200, 200]
    assert blocked.status_code == 429
    assert blocked.json() == V1_BODY_429


# --- HEAD and 304 are counted for the account ---


async def test_head_with_a_key_is_counted_for_the_account_not_the_ip(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=3)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client, env.client(IP_A) as anonymous:
        heads = [await client.head(SMALL_V2) for _ in range(3)]
        blocked = await client.head(SMALL_V2)
        anon = await anonymous.get(SMALL_V2)

    assert [h.status_code for h in heads] == [200] * 3
    assert blocked.status_code == 429
    assert blocked.content == b""
    assert anon.status_code == 200
    assert remaining(anon) == 59


async def test_not_modified_with_a_key_is_counted_for_the_account(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=3)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        first = await client.get("/api/v2/rovers")
        conditional = {"If-None-Match": first.headers["etag"]}
        assert (await client.get("/api/v2/rovers", headers=conditional)).status_code == 304
        assert (await client.get("/api/v2/rovers", headers=conditional)).status_code == 304

        blocked = await client.get("/api/v2/rovers", headers=conditional)

    assert blocked.status_code == 429


# --- restart: counters are in memory, data is not ---


async def test_restart_resets_the_memory_counters_but_the_key_still_works(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_key_per_minute=3)
    key = await account_with_key(env.factory)
    async with env.client(IP_A, hdr(key.value)) as client:
        await statuses(client, SMALL_V2, 3)
        assert (await client.get(SMALL_V2)).status_code == 429

    restarted: KeysApp = await make_keys_app(rate_limit_key_per_minute=3)
    async with restarted.client(IP_A, hdr(key.value)) as client:
        response = await client.get(SMALL_V2)

    assert response.status_code == 200
    assert remaining(response) == 2


# --- AC-14: v1 response size is charged to the account ---

PLAIN = {"Accept-Encoding": "identity"}


@pytest.mark.parametrize("how", ["query", "header"])
async def test_ac14_big_v1_response_is_charged_to_the_account_not_the_ip(
    make_keys_app: MakeApp, how: str
) -> None:
    """A whole sol (31 units without gzip) costs 31 requests, in the account windows."""
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    params = {"api_key": key.value} if how == "query" else {}
    headers = {**PLAIN, **(hdr(key.value) if how == "header" else {})}
    async with env.client(IP_A) as client:
        big = await client.get(BLOB_V1, params={**params, "n": 31 * UNIT}, headers=headers)
        assert big.status_code == 200
        assert len(big.content) == 31 * UNIT
        keyed_probe = await client.get(SMALL_V2, params=params, headers=headers)
        anon_probe = await client.get(SMALL_V2, headers=PLAIN)

    assert keyed_probe.headers["x-ratelimit-limit"] == "120"
    assert remaining(keyed_probe) == 120 - 31 - 1
    assert anon_probe.headers["x-ratelimit-limit"] == "60"
    assert remaining(anon_probe) == 59  # the IP counter was not touched


async def test_ac14_the_charge_can_exhaust_the_account_budget(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    key = await account_with_key(env.factory)
    async with env.client(IP_A, {**PLAIN, **hdr(key.value)}) as client:
        statuses_ = [
            (await client.get(BLOB_V1, params={"n": 50 * UNIT})).status_code for _ in range(3)
        ]
        blocked = await client.get(SMALL_V2)

    assert statuses_ == [200, 200, 200]  # 50 + 50 + 50 > 120: the third is served, then 429
    assert blocked.status_code == 429


async def test_ac14_two_keys_of_an_account_share_the_charge(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app()
    account_id = await create_account(env.factory, "pair@example.org")
    first = await create_key(env.factory, account_id)
    second = await create_key(env.factory, account_id)
    async with env.client(IP_A, {**PLAIN, **hdr(first.value)}) as one:
        await one.get(BLOB_V1, params={"n": 10 * UNIT})
    async with env.client(IP_B, {**PLAIN, **hdr(second.value)}) as two:
        probe = await two.get(SMALL_V2)

    assert remaining(probe) == 120 - 10 - 1


async def warm(env: KeysApp, key: Any, ip: str = IP_A) -> None:
    """One request, so the key is cached: a cold burst from one IP is limited while resolving."""
    async with env.client(ip, hdr(key.value)) as client:
        assert (await client.get(SMALL_V2)).status_code == 200


# --- AC-16: concurrency per tier ---


async def test_ac16_key_allows_8_requests_at_a_time_and_the_9th_is_429(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH, rate_limit_key_per_minute=HIGH)
    key = await account_with_key(env.factory)
    await warm(env, key)
    clients = [env.client(IP_A, hdr(key.value)) for _ in range(9)]
    held = [asyncio.ensure_future(c.get(HOLD_V2)) for c in clients[:8]]
    try:
        await env.wait_entered(8)
        ninth = await clients[8].get(HOLD_V2)
    finally:
        env.release.set()
        results = await asyncio.gather(*held)

    assert ninth.status_code == 429
    assert ninth.json() == V2_BODY_429
    assert ninth.headers["retry-after"] == "1"
    assert ninth.headers["cache-control"] == "no-store"
    assert [r.status_code for r in results] == [200] * 8


async def test_ac16_concurrency_is_per_account_across_keys_and_ips(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH, rate_limit_key_per_minute=HIGH)
    account_id = await create_account(env.factory, "pair@example.org")
    first = await create_key(env.factory, account_id)
    second = await create_key(env.factory, account_id)
    ips = [f"203.0.113.{40 + i}" for i in range(9)]
    held = []
    for index in range(8):
        key = first if index % 2 == 0 else second
        held.append(asyncio.ensure_future(env.client(ips[index], hdr(key.value)).get(HOLD_V2)))
    try:
        await env.wait_entered(8)
        ninth = await env.client(ips[8], hdr(first.value)).get(HOLD_V2)
    finally:
        env.release.set()
        await asyncio.gather(*held)

    assert ninth.status_code == 429
    assert ninth.headers["retry-after"] == "1"


async def test_ac16_after_the_slots_are_free_the_key_works_again(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH, rate_limit_key_per_minute=HIGH)
    key = await account_with_key(env.factory)
    await warm(env, key)
    held = [asyncio.ensure_future(env.client(IP_A, hdr(key.value)).get(HOLD_V2)) for _ in range(8)]
    await env.wait_entered(8)
    env.release.set()
    await asyncio.gather(*held)

    async with env.client(IP_A, hdr(key.value)) as client:
        response = await client.get(SMALL_V2)

    assert response.status_code == 200


async def test_ac16_without_a_key_the_5th_concurrent_request_is_429(make_keys_app: MakeApp) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH, rate_limit_key_per_minute=HIGH)
    held = [asyncio.ensure_future(env.client(IP_A).get(HOLD_V2)) for _ in range(4)]
    try:
        await env.wait_entered(4)
        fifth = await env.client(IP_A).get(HOLD_V2)
    finally:
        env.release.set()
        await asyncio.gather(*held)

    assert fifth.status_code == 429
    assert fifth.headers["retry-after"] == "1"


async def test_ac16_keyed_requests_in_flight_do_not_use_the_anonymous_slots_of_the_same_ip(
    make_keys_app: MakeApp,
) -> None:
    env: KeysApp = await make_keys_app(rate_limit_per_minute=HIGH, rate_limit_key_per_minute=HIGH)
    key = await account_with_key(env.factory)
    await warm(env, key)
    held = [asyncio.ensure_future(env.client(IP_C, hdr(key.value)).get(HOLD_V2)) for _ in range(8)]
    try:
        await env.wait_entered(8)
        anonymous = await env.client(IP_C).get(SMALL_V2)
    finally:
        env.release.set()
        await asyncio.gather(*held)

    assert anonymous.status_code == 200
