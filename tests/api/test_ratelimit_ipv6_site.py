"""Shared IPv6 budget of a /48 network (docs/API.md "Ограничение частоты", HIGH-2).

Without an API key an IPv6 client is a /64 network; all /64 networks of one /48 additionally
share a budget `RATE_LIMIT_IPV6_SITE_FACTOR` times larger (windows and concurrency). A request
counts in both; the first to refuse wins. `X-RateLimit-*` always describe the /64 client.
Admin failures, bad API keys, mail requests and heavy searches are counted per /48 directly
(the last one lives in test_overload_gate.py; sign-up in test_key_signup_request.py).

Time is a fake clock set on every `RateLimiter` held by `app.state` (the site limiter's
attribute name is the implementation's business). Nothing sleeps.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.core.ratelimit import RateLimiter
from tests._apikeys import (
    BLOB_V1,
    BOOM_V2,
    HOLD_V2,
    SMALL_V1,
    SMALL_V2,
    UNIT,
    V1_BODY_429,
    V2_BODY_429,
    KeysApp,
    account_with_key,
    assert_v2_error,
    new_key,
    remaining,
    retry_after,
)
from tests._keysignup import DELETION_REQUESTS, assert_error, make_signup_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
MakeSite = Callable[..., Awaitable[KeysApp]]

NET_A = 0xA  # the /48 under test: 2001:db8:a::/48
NET_B = 0xB  # another /48
RUNS = "/api/v2/admin/sync/runs"
PLAIN = {"Accept-Encoding": "identity"}
ADMIN_TOKEN = "test-admin-token"
GOOD = {"X-Admin-Token": ADMIN_TOKEN}
BAD = {"X-Admin-Token": "wrong-token"}


def ip6(site: int, net: int, host: int = 1) -> str:
    """An address of the /64 number `net` of the /48 number `site`."""
    return f"2001:db8:{site:x}:{net:x}::{host:x}"


def set_every_clock(env: KeysApp) -> None:
    """The fake clock on every limiter of the application, whatever it is called."""
    for value in env.app.state._state.values():
        if isinstance(value, RateLimiter):
            value.clock = env.clock


@pytest.fixture
async def make_site(make_keys_app: MakeApp) -> MakeSite:
    """App with a /64 limit of 3 per minute and a factor of 2 (a /48 gets 6 per minute)."""

    async def make(**extra: Any) -> KeysApp:
        values: dict[str, Any] = {
            "rate_limit_per_minute": 3,
            "rate_limit_per_hour": 1000,
            "rate_limit_ipv6_site_factor": 2,
            **extra,
        }
        env: KeysApp = await make_keys_app(**values)
        set_every_clock(env)
        return env

    return make


async def burst(env: KeysApp, ip: str, n: int, url: str = SMALL_V2) -> list[int]:
    client = env.client(ip)
    return [(await client.get(url)).status_code for _ in range(n)]


# --- 2: the /48 budget over several /64 networks ---


async def test_site_budget_spent_by_other_networks_rejects_a_new_network(
    make_site: MakeSite,
) -> None:
    env = await make_site()

    first = await burst(env, ip6(NET_A, 1), 3)
    second = await burst(env, ip6(NET_A, 2), 3)
    refused = await env.client(ip6(NET_A, 3)).get(SMALL_V2)

    assert first + second == [200] * 6  # every /64 stays inside its own limit
    assert refused.status_code == 429
    assert_v2_error(refused, 429, "rate_limited")
    assert refused.json() == V2_BODY_429
    assert 1 <= retry_after(refused) <= 60  # from the counter of the /48
    assert refused.headers["cache-control"] == "no-store"
    assert refused.headers["x-ratelimit-limit"] == "3"  # the limit of the /64 client


async def test_site_budget_refusal_of_a_v1_path_uses_the_legacy_body(make_site: MakeSite) -> None:
    env = await make_site()
    await burst(env, ip6(NET_A, 1), 3, SMALL_V1)
    await burst(env, ip6(NET_A, 2), 3, SMALL_V1)

    refused = await env.client(ip6(NET_A, 3)).get(SMALL_V1)

    assert refused.status_code == 429
    assert refused.json() == V1_BODY_429
    assert retry_after(refused) >= 1
    assert refused.headers["cache-control"] == "no-store"


async def test_site_budget_of_one_48_does_not_touch_another_48(make_site: MakeSite) -> None:
    env = await make_site()
    await burst(env, ip6(NET_A, 1), 3)
    await burst(env, ip6(NET_A, 2), 3)
    assert (await env.client(ip6(NET_A, 3)).get(SMALL_V2)).status_code == 429

    other = await burst(env, ip6(NET_B, 1), 3)
    still_refused = await env.client(ip6(NET_A, 4)).get(SMALL_V2)

    assert other == [200, 200, 200]
    assert still_refused.status_code == 429


async def test_site_budget_a_network_under_the_budget_is_served_with_its_own_headers(
    make_site: MakeSite,
) -> None:
    env = await make_site()
    await burst(env, ip6(NET_A, 1), 2)

    response = await env.client(ip6(NET_A, 2)).get(SMALL_V2)

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "3"
    assert remaining(response) == 2  # the /64 client has spent one request, not three


async def test_site_budget_frees_up_when_the_window_slides(make_site: MakeSite) -> None:
    env = await make_site()
    await burst(env, ip6(NET_A, 1), 3)
    await burst(env, ip6(NET_A, 2), 3)
    refused = await env.client(ip6(NET_A, 3)).get(SMALL_V2)
    assert refused.status_code == 429

    env.clock.advance(retry_after(refused) + 1)

    assert (await env.client(ip6(NET_A, 3)).get(SMALL_V2)).status_code == 200


async def test_site_budget_hour_window_applies_too(make_site: MakeSite) -> None:
    """Per-hour budget of the /48 = factor x the per-hour limit of a client."""
    env = await make_site(rate_limit_per_minute=4, rate_limit_per_hour=4)

    a = await burst(env, ip6(NET_A, 1), 4)
    b = await burst(env, ip6(NET_A, 2), 4)
    refused = await env.client(ip6(NET_A, 3)).get(SMALL_V2)

    assert a + b == [200] * 8
    assert refused.status_code == 429
    assert retry_after(refused) > 60  # waiting for the hour window


# --- bad keys, HEAD and 304 are counted in the /48 ---


async def fill_site_then_expect_refusal(env: KeysApp, spend: Callable[[str], Any]) -> None:
    """Spend 3 + 3 requests from two /64 networks of one /48, then a third one is refused."""
    await spend(ip6(NET_A, 1))
    await spend(ip6(NET_A, 2))
    refused = await env.client(ip6(NET_A, 3)).get(SMALL_V2)
    assert refused.status_code == 429
    assert_v2_error(refused, 429, "rate_limited")


async def test_site_budget_counts_requests_with_a_foreign_key_header(make_site: MakeSite) -> None:
    env = await make_site(api_key_auth_max_failures=1000)

    async def spend(ip: str) -> None:
        client = env.client(ip, {"X-Api-Key": "DEMO_KEY"})
        for _ in range(3):
            await client.get(SMALL_V2)

    await fill_site_then_expect_refusal(env, spend)


async def test_site_budget_counts_v1_requests_with_a_foreign_key_param(make_site: MakeSite) -> None:
    env = await make_site(api_key_auth_max_failures=1000)

    async def spend(ip: str) -> None:
        client = env.client(ip)
        for _ in range(3):
            assert (await client.get(SMALL_V1, params={"api_key": "DEMO_KEY"})).status_code == 200

    await fill_site_then_expect_refusal(env, spend)


async def test_site_budget_counts_head_requests(make_site: MakeSite) -> None:
    env = await make_site()

    async def spend(ip: str) -> None:
        client = env.client(ip)
        for _ in range(3):
            assert (await client.head(SMALL_V2)).status_code == 200

    await fill_site_then_expect_refusal(env, spend)


async def test_site_budget_counts_not_modified_responses(make_site: MakeSite) -> None:
    env = await make_site()
    url = "/api/v2/rovers"
    etag = (await env.client(ip6(NET_A, 9)).get(url)).headers["etag"]  # 1 of 6 in the /48

    async def spend(ip: str) -> None:
        client = env.client(ip)
        for _ in range(2):
            assert (await client.get(url, headers={"If-None-Match": etag})).status_code == 304

    await spend(ip6(NET_A, 1))  # 3 of 6
    await spend(ip6(NET_A, 2))  # 5 of 6
    last = await env.client(ip6(NET_A, 3)).get(url, headers={"If-None-Match": etag})
    over = await env.client(ip6(NET_A, 4)).get(url, headers={"If-None-Match": etag})

    assert last.status_code == 304  # the sixth is still allowed
    assert over.status_code == 429


async def test_site_slot_is_freed_when_the_client_cancels_the_request(make_site: MakeSite) -> None:
    env = await make_site(rate_limit_per_minute=100, rate_limit_concurrent=1)
    held = await start_holds(env, ip6(NET_A, 1), ip6(NET_A, 2))
    await env.wait_entered(2)
    for task in held:
        task.cancel()
    await asyncio.gather(*held, return_exceptions=True)
    env.release.clear()
    env.entered.clear()

    again = await start_holds(env, ip6(NET_A, 3), ip6(NET_A, 4))
    await env.wait_entered(2)  # both get in: the cancelled requests leaked nothing
    third = await env.client(ip6(NET_A, 5)).get(SMALL_V2)
    env.release.set()
    await asyncio.gather(*again)

    assert third.status_code == 429


# --- 3 and 4: refused requests are not counted ---


async def test_one_network_over_its_own_limit_is_refused_and_does_not_spend_the_site_budget(
    make_site: MakeSite,
) -> None:
    env = await make_site()

    own = await burst(env, ip6(NET_A, 1), 7)
    refused = await env.client(ip6(NET_A, 1, 2)).get(SMALL_V2)
    other = await burst(env, ip6(NET_A, 2), 3)  # exactly what is left of the 6
    over = await env.client(ip6(NET_A, 3)).get(SMALL_V2)

    assert own == [200, 200, 200, 429, 429, 429, 429]
    assert refused.status_code == 429
    assert other == [200, 200, 200]
    assert over.status_code == 429


async def test_request_refused_by_the_site_is_not_counted_for_its_own_network(
    make_site: MakeSite,
) -> None:
    """Minute windows free up, hour windows do not: the hour of /64 number 3 stays empty."""
    env = await make_site(rate_limit_per_minute=3, rate_limit_per_hour=4)
    await burst(env, ip6(NET_A, 1), 3)
    await burst(env, ip6(NET_A, 2), 3)
    client = env.client(ip6(NET_A, 3))
    assert [(await client.get(SMALL_V2)).status_code for _ in range(2)] == [429, 429]
    env.clock.advance(61)

    response = await client.get(SMALL_V2)

    assert response.status_code == 200
    # Not counted: minute 3 - 1 = 2 left, hour 4 - 1 = 3 left. Counted twice: hour 1 left.
    assert response.headers["x-ratelimit-limit"] == "3"
    assert remaining(response) == 2


# --- 5: IPv4 is unchanged ---


@pytest.mark.parametrize("factor", [1, 2, 8], ids=lambda f: f"factor-{f}")
async def test_ipv4_addresses_have_no_shared_budget(make_site: MakeSite, factor: int) -> None:
    env = await make_site(rate_limit_ipv6_site_factor=factor)

    codes = [
        await burst(env, ip, 3) for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3")
    ]  # 9 requests > 6 = what a factor-2 site budget would allow

    assert codes == [[200] * 3] * 3


async def test_ipv4_address_over_its_limit_is_refused_as_before(make_site: MakeSite) -> None:
    env = await make_site()

    codes = await burst(env, "203.0.113.1", 4)
    other = await burst(env, "203.0.113.2", 1)

    assert codes == [200, 200, 200, 429]
    assert other == [200]


async def test_ipv4_traffic_does_not_spend_the_budget_of_an_ipv6_site(make_site: MakeSite) -> None:
    env = await make_site()
    for ip in ("203.0.113.1", "203.0.113.2"):
        await burst(env, ip, 3)

    assert await burst(env, ip6(NET_A, 1), 3) == [200, 200, 200]


async def test_ipv4_mapped_ipv6_address_is_an_ipv4_client(make_site: MakeSite) -> None:
    env = await make_site()

    codes = [await burst(env, f"::ffff:203.0.113.{n}", 3) for n in (1, 2, 3)]

    assert codes == [[200] * 3] * 3


# --- 6: concurrency ---


async def start_holds(env: KeysApp, *ips: str) -> list["asyncio.Future[Any]"]:
    return [asyncio.ensure_future(env.client(ip).get(HOLD_V2)) for ip in ips]


async def test_third_concurrent_request_of_a_48_is_refused_with_retry_after_1(
    make_site: MakeSite,
) -> None:
    env = await make_site(rate_limit_per_minute=100, rate_limit_concurrent=1)
    held = await start_holds(env, ip6(NET_A, 1), ip6(NET_A, 2))
    await env.wait_entered(2)
    try:
        third = await env.client(ip6(NET_A, 3)).get(SMALL_V2)
        other_site = await env.client(ip6(NET_B, 1)).get(SMALL_V2)
    finally:
        env.release.set()
    done = await asyncio.gather(*held)
    after = await env.client(ip6(NET_A, 3)).get(SMALL_V2)

    assert third.status_code == 429
    assert third.json() == V2_BODY_429
    assert third.headers["retry-after"] == "1"
    assert third.headers["cache-control"] == "no-store"
    assert third.headers["x-ratelimit-limit"] == "100"
    assert other_site.status_code == 200
    assert [r.status_code for r in done] == [200, 200]
    assert after.status_code == 200


async def test_slots_of_a_48_are_given_back_after_refused_and_finished_requests(
    make_site: MakeSite,
) -> None:
    env = await make_site(rate_limit_per_minute=100, rate_limit_concurrent=1)
    held = await start_holds(env, ip6(NET_A, 1), ip6(NET_A, 2))
    await env.wait_entered(2)
    refused = [await env.client(ip6(NET_A, n)).get(SMALL_V2) for n in (3, 4)]
    env.release.set()
    await asyncio.gather(*held)
    env.release.clear()
    env.entered.clear()

    again = await start_holds(env, ip6(NET_A, 5), ip6(NET_A, 6))
    await env.wait_entered(2)  # both get in: nothing leaked
    third = await env.client(ip6(NET_A, 7)).get(SMALL_V2)
    env.release.set()
    await asyncio.gather(*again)

    assert [r.status_code for r in refused] == [429, 429]
    assert third.status_code == 429


async def test_request_refused_by_the_network_slot_does_not_take_a_slot_of_the_48(
    make_site: MakeSite,
) -> None:
    env = await make_site(rate_limit_per_minute=100, rate_limit_concurrent=1)
    held = await start_holds(env, ip6(NET_A, 1))
    await env.wait_entered(1)
    same_network = await env.client(ip6(NET_A, 1, 2)).get(SMALL_V2)  # refused: /64 is at 1

    second = await start_holds(env, ip6(NET_A, 2))
    await env.wait_entered(2)  # a leaked slot would have refused this one
    third = await env.client(ip6(NET_A, 3)).get(SMALL_V2)
    env.release.set()
    await asyncio.gather(*held, *second)

    assert same_network.status_code == 429
    assert third.status_code == 429


async def test_slots_are_given_back_after_a_failing_handler(make_site: MakeSite) -> None:
    env = await make_site(rate_limit_per_minute=100, rate_limit_concurrent=1)
    crashed = [await env.client(ip6(NET_A, n)).get(BOOM_V2) for n in (1, 2, 3)]

    held = await start_holds(env, ip6(NET_A, 4), ip6(NET_A, 5))
    await env.wait_entered(2)
    third = await env.client(ip6(NET_A, 6)).get(SMALL_V2)
    env.release.set()
    await asyncio.gather(*held)

    assert [r.status_code for r in crashed] == [500, 500, 500]
    assert third.status_code == 429


async def test_concurrency_of_ipv4_is_not_shared(make_site: MakeSite) -> None:
    env = await make_site(rate_limit_per_minute=100, rate_limit_concurrent=1)
    held = await start_holds(env, "203.0.113.1", "203.0.113.2", "203.0.113.3")
    try:
        await env.wait_entered(3)
    finally:
        env.release.set()

    assert [r.status_code for r in await asyncio.gather(*held)] == [200, 200, 200]


# --- 7: surcharge for a v1 response ---


async def test_surcharge_for_a_big_v1_response_is_charged_to_the_48_too(
    make_site: MakeSite,
) -> None:
    env = await make_site(rate_limit_per_minute=10, rate_limit_per_hour=1000)
    for net in (1, 2):
        big = await env.client(ip6(NET_A, net), PLAIN).get(BLOB_V1, params={"n": 8 * UNIT})
        assert big.status_code == 200
    # Spent in the /48: 8 + 8 = 16 of 20; each /64 holds 8 of 10, a third one is untouched.

    codes = await burst(env, ip6(NET_A, 3), 6)

    assert codes == [200, 200, 200, 200, 429, 429]


# --- 8: a valid key is counted on the account ---


async def test_valid_key_is_counted_on_the_account_not_on_the_48(make_site: MakeSite) -> None:
    env = await make_site(rate_limit_key_per_minute=10, rate_limit_key_per_hour=1000)
    key = await account_with_key(env.factory)
    headers = {"X-Api-Key": key.value}

    responses = [
        await env.client(ip6(NET_A, n), headers).get(SMALL_V2) for n in range(1, 11)
    ]  # ten /64 networks, twice the /48 budget of 6
    over = await env.client(ip6(NET_A, 11), headers).get(SMALL_V2)

    assert [r.status_code for r in responses] == [200] * 10
    assert {r.headers["x-ratelimit-limit"] for r in responses} == {"10"}
    assert over.status_code == 429  # the account budget, not the /48 one
    assert over.headers["x-ratelimit-limit"] == "10"


async def test_requests_with_a_key_do_not_spend_the_anonymous_budget_of_the_48(
    make_site: MakeSite,
) -> None:
    env = await make_site(rate_limit_key_per_minute=20, rate_limit_key_per_hour=1000)
    key = await account_with_key(env.factory)
    for n in range(1, 9):
        client = env.client(ip6(NET_A, n), {"X-Api-Key": key.value})
        assert (await client.get(SMALL_V2)).status_code == 200

    assert await burst(env, ip6(NET_A, 20), 3) == [200, 200, 200]


async def test_key_concurrency_is_the_account_one_not_the_48_one(make_site: MakeSite) -> None:
    env = await make_site(
        rate_limit_per_minute=100, rate_limit_concurrent=1, rate_limit_key_concurrent=8
    )
    key = await account_with_key(env.factory)

    def client(n: int) -> Any:
        return env.client(ip6(NET_A, n), {"X-Api-Key": key.value})

    # Warm the key cache: a cold burst is limited while the key is looked up.
    assert (await client(99).get(SMALL_V2)).status_code == 200
    held = [asyncio.ensure_future(client(n).get(HOLD_V2)) for n in range(1, 5)]
    try:
        await env.wait_entered(4)  # four in flight from one /48: more than 1 x factor 2
    finally:
        env.release.set()

    assert [r.status_code for r in await asyncio.gather(*held)] == [200] * 4


# --- 9: counters of failures are kept per /48 ---


async def test_admin_failures_of_several_networks_of_a_48_lock_the_whole_48(
    make_site: MakeSite,
) -> None:
    env = await make_site(admin_auth_max_failures=3, rate_limit_per_minute=100)
    for n in (1, 2, 3):
        failed = await env.client(ip6(NET_A, n), BAD).get(RUNS)
        assert failed.status_code == 401

    valid_new_network = await env.client(ip6(NET_A, 4), GOOD).get(RUNS)
    wrong_known_network = await env.client(ip6(NET_A, 1), BAD).get(RUNS)
    other_48_wrong = await env.client(ip6(NET_B, 1), BAD).get(RUNS)
    other_48_valid = await env.client(ip6(NET_B, 2), GOOD).get(RUNS)

    for locked in (valid_new_network, wrong_known_network):
        assert_error(locked, 429, "rate_limited")
        assert 1 <= retry_after(locked) <= 15 * 60
    assert_error(other_48_wrong, 401, "unauthorized")
    assert other_48_valid.status_code == 200


async def test_admin_failures_below_the_limit_across_a_48_do_not_lock(make_site: MakeSite) -> None:
    env = await make_site(admin_auth_max_failures=3, rate_limit_per_minute=100)
    for n in (1, 2):
        assert (await env.client(ip6(NET_A, n), BAD).get(RUNS)).status_code == 401

    response = await env.client(ip6(NET_A, 3), GOOD).get(RUNS)

    assert response.status_code == 200


async def test_admin_lock_of_a_48_ends_with_its_window(make_site: MakeSite) -> None:
    env = await make_site(admin_auth_max_failures=2, rate_limit_per_minute=100)
    for n in (1, 2):
        await env.client(ip6(NET_A, n), BAD).get(RUNS)
    locked = await env.client(ip6(NET_A, 3), GOOD).get(RUNS)
    assert locked.status_code == 429

    env.clock.advance(retry_after(locked) + 1)

    assert (await env.client(ip6(NET_A, 3), GOOD).get(RUNS)).status_code == 200


async def test_bad_api_keys_from_several_networks_of_a_48_lock_the_whole_48(
    make_site: MakeSite,
) -> None:
    env = await make_site(api_key_auth_max_failures=3, rate_limit_per_minute=100)

    def hdr(value: str) -> dict[str, str]:
        return {"X-Api-Key": value}

    for n in (1, 2, 3):
        failed = await env.client(ip6(NET_A, n), hdr(new_key())).get(SMALL_V2)
        assert_v2_error(failed, 401, "invalid_api_key")

    locked = await env.client(ip6(NET_A, 4), hdr(new_key())).get(SMALL_V2)
    other_48 = await env.client(ip6(NET_B, 1), hdr(new_key())).get(SMALL_V2)

    assert_v2_error(locked, 429, "rate_limited")
    assert 1 <= retry_after(locked) <= 15 * 60
    assert_v2_error(other_48, 401, "invalid_api_key")


async def test_bad_api_key_failures_of_a_48_do_not_lock_requests_without_a_key(
    make_site: MakeSite,
) -> None:
    env = await make_site(api_key_auth_max_failures=2, rate_limit_per_minute=100)
    for n in (1, 2):
        await env.client(ip6(NET_A, n), {"X-Api-Key": new_key()}).get(SMALL_V2)

    anonymous = await env.client(ip6(NET_A, 3)).get(SMALL_V2)

    assert anonymous.status_code == 200


async def test_mail_requests_from_several_networks_of_a_48_share_the_hourly_limit(
    make_keys_app: MakeApp,
) -> None:
    se = await make_signup_app(make_keys_app, key_signup_per_ip_per_hour=2)
    set_every_clock(se.env)

    first = await se.request_key("a@example.org", ip6(NET_A, 1))
    second = await se.post(DELETION_REQUESTS, {"email": "b@example.org"}, ip6(NET_A, 2))
    third = await se.request_key("c@example.org", ip6(NET_A, 3))
    other_48 = await se.request_key("d@example.org", ip6(NET_B, 1))

    assert (first.status_code, second.status_code) == (202, 202)
    assert_error(third, 429, "rate_limited")
    assert other_48.status_code == 202
