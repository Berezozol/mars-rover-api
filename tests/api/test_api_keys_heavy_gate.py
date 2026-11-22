"""Heavy-search gate keyed by account (stage 20A, AC-15; API.md "Ограничение частоты").

At most one heavy search at a time per IP without a key and per ACCOUNT with a key (two keys
and any IP of one account share the place); the server-wide `HEAVY_QUERY_CONCURRENCY` is shared
by all tiers. The count of a heavy shape is replaced by a stub that waits for an event, so a
request holds its place until the test lets it go (no sleeping, no database work).
"""

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.repositories import photos as photos_repo
from app.repositories.photos import PhotoFilter
from tests._apikeys import (
    IP_A,
    IP_B,
    IP_C,
    KeysApp,
    account_with_key,
    create_account,
    create_key,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

MakeApp = Callable[..., Any]
HEAVY = "/api/v2/rovers/curiosity/photos?date_min=2012-08-06&sample_type=full"
LIGHT = "/api/v2/rovers/curiosity/photos?sol=1000"
BUSY = {
    "error": {"code": "service_busy", "message": "Server is busy, retry later", "details": None}
}


class CountHold:
    """Stand-in for `count_photos`: heavy shapes (a `date_min`) wait for `release`."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = 0

    async def __call__(self, session: Any, flt: PhotoFilter) -> int:
        if flt.date_min is not None:
            self.entered += 1
            await self.release.wait()
        return 0


@pytest.fixture
def hold(monkeypatch: pytest.MonkeyPatch) -> CountHold:
    stub = CountHold()
    monkeypatch.setattr(photos_repo, "count_photos", stub)
    return stub


async def wait_for(condition: Callable[[], bool], what: str) -> None:
    for _ in range(200_000):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"timed out waiting for {what}")


def hdr(key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


def assert_busy(response: httpx.Response) -> None:
    assert response.status_code == 503, response.text
    assert response.json() == BUSY
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"


async def finish(hold: CountHold, *tasks: "asyncio.Future[httpx.Response]") -> list[int]:
    hold.release.set()
    return [r.status_code for r in await asyncio.gather(*tasks)]


async def test_ac15_second_heavy_search_of_the_same_account_from_another_ip_is_503(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    env: KeysApp = await make_keys_app(heavy_query_concurrency=3)
    key = await account_with_key(env.factory)
    first = asyncio.ensure_future(env.client(IP_A, hdr(key.value)).get(HEAVY))
    await wait_for(lambda: hold.entered == 1, "the first heavy search")

    second = await env.client(IP_B, hdr(key.value)).get(HEAVY)
    assert env.app.state.heavy_query_gate.in_use == 1  # the free server slot was not taken

    assert_busy(second)
    assert await finish(hold, first) == [200]


async def test_ac15_the_second_key_of_the_same_account_shares_the_place(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    env: KeysApp = await make_keys_app(heavy_query_concurrency=3)
    account_id = await create_account(env.factory, "pair@example.org")
    first_key = await create_key(env.factory, account_id)
    second_key = await create_key(env.factory, account_id)
    first = asyncio.ensure_future(env.client(IP_A, hdr(first_key.value)).get(HEAVY))
    await wait_for(lambda: hold.entered == 1, "the first heavy search")

    second = await env.client(IP_A, hdr(second_key.value)).get(HEAVY)

    assert_busy(second)
    await finish(hold, first)


async def test_ac15_the_gate_is_not_per_ip_for_a_keyed_client(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    """Two different accounts behind one IP (a NAT) may each run a heavy search."""
    env: KeysApp = await make_keys_app(heavy_query_concurrency=3)
    one = await account_with_key(env.factory, "one@example.org")
    two = await account_with_key(env.factory, "two@example.org")
    first = asyncio.ensure_future(env.client(IP_A, hdr(one.value)).get(HEAVY))
    second = asyncio.ensure_future(env.client(IP_A, hdr(two.value)).get(HEAVY))
    await wait_for(lambda: hold.entered == 2, "two heavy searches")

    assert await finish(hold, first, second) == [200, 200]


async def test_ac15_a_keyed_account_and_an_anonymous_ip_are_separate_clients(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    env: KeysApp = await make_keys_app(heavy_query_concurrency=3)
    key = await account_with_key(env.factory)
    keyed = asyncio.ensure_future(env.client(IP_A, hdr(key.value)).get(HEAVY))
    anonymous = asyncio.ensure_future(env.client(IP_A).get(HEAVY))
    await wait_for(lambda: hold.entered == 2, "a keyed and an anonymous heavy search")

    assert await finish(hold, keyed, anonymous) == [200, 200]


async def test_ac15_anonymous_client_still_has_one_heavy_search_per_ip(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    env: KeysApp = await make_keys_app(heavy_query_concurrency=3)
    first = asyncio.ensure_future(env.client(IP_A).get(HEAVY))
    await wait_for(lambda: hold.entered == 1, "the first heavy search")

    second = await env.client(IP_A).get(HEAVY)

    assert_busy(second)
    await finish(hold, first)


async def test_ac15_the_server_wide_limit_is_shared_by_keyed_and_anonymous_clients(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    env: KeysApp = await make_keys_app(heavy_query_concurrency=2)
    one = await account_with_key(env.factory, "one@example.org")
    two = await account_with_key(env.factory, "two@example.org")
    keyed = asyncio.ensure_future(env.client(IP_A, hdr(one.value)).get(HEAVY))
    anonymous = asyncio.ensure_future(env.client(IP_B).get(HEAVY))
    await wait_for(lambda: hold.entered == 2, "both places taken")

    third = await env.client(IP_C, hdr(two.value)).get(HEAVY)
    fourth = await env.client(IP_C).get(HEAVY)

    assert_busy(third)
    assert_busy(fourth)
    await finish(hold, keyed, anonymous)


async def test_ac15_light_searches_of_the_account_are_served_while_its_heavy_one_runs(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    env: KeysApp = await make_keys_app(heavy_query_concurrency=3)
    key = await account_with_key(env.factory)
    heavy = asyncio.ensure_future(env.client(IP_A, hdr(key.value)).get(HEAVY))
    await wait_for(lambda: hold.entered == 1, "the heavy search")

    light = await env.client(IP_B, hdr(key.value)).get(LIGHT)

    assert light.status_code == 200, light.text
    await finish(hold, heavy)


async def test_ac15_the_place_is_free_again_after_the_heavy_search_ends(
    make_keys_app: MakeApp, hold: CountHold
) -> None:
    env: KeysApp = await make_keys_app(heavy_query_concurrency=3)
    key = await account_with_key(env.factory)
    first = asyncio.ensure_future(env.client(IP_A, hdr(key.value)).get(HEAVY))
    await wait_for(lambda: hold.entered == 1, "the first heavy search")
    await finish(hold, first)
    await wait_for(lambda: env.app.state.heavy_query_gate.in_use == 0, "the place to be freed")

    again = await env.client(IP_B, hdr(key.value)).get(HEAVY)

    assert again.status_code == 200, again.text
