"""Concurrency limit outside the API (docs/API.md "Ограничение частоты (v1 и v2)",
bullet "Одновременные запросы вне API (2026-11-27)" and the changed first bullet).

On non-API paths (site pages and files, /health, /health/sync, /docs, /redoc, /openapi.json,
/developers, /llms.txt) at most `RATE_LIMIT_PAGE_CONCURRENT` (default 16) requests of one client
address are served at once; an extra one gets the v2-style 429 with `Retry-After: 1` and
`Cache-Control: no-store`. There are no windows and no `X-RateLimit-*` headers there. The counter
is separate from the API one, keyed by the address only (an API key changes nothing), IPv6 by
/64 and by /48 (N x `RATE_LIMIT_IPV6_SITE_FACTOR`). A slot is held until the response is fully
sent. `RATE_LIMIT_ENABLED=false` switches it off.

Requests are held in flight by synthetic non-API routes (`/__page_hold/{name}` and friends)
that are put in front of the frontend catch-all. The real pages are probed by filling the
counter of the address with those routes and then asking the real path: it must be refused.
Nothing sleeps; time is never needed because there are no windows.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Response
from fastapi.responses import StreamingResponse

from app.core.config import Settings
from tests._apikeys import (
    HOLD_V2,
    IP_A,
    IP_B,
    SMALL_V1,
    SMALL_V1_ALT,
    SMALL_V2,
    V2_BODY_429,
    KeysApp,
    account_with_key,
)

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db", "clean_api_keys")]

N = 3  # page concurrency used by most tests
PAGE_HOLD = "/__page_hold"  # /{name}
PAGE_HOLD_304 = "/__page_hold304"  # /{name}
PAGE_STREAM = "/__page_stream"  # /{name}
PAGE_SMALL = "/__page_small"
PAGE_BOOM = "/__page_boom"
INDEX_MARKER = "PAGE-CONCURRENCY-FAKE-BUILD"

NET_A = 0xA
NET_B = 0xB

MakeApp = Callable[..., Any]


def ip6(site: int, net: int, host: int = 1) -> str:
    """An address of the /64 number `net` of the /48 number `site`."""
    return f"2001:db8:{site:x}:{net:x}::{host:x}"


def assert_page_429(response: httpx.Response) -> None:
    assert response.status_code == 429
    assert response.json() == V2_BODY_429
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert_no_limit_headers(response)


def assert_no_limit_headers(response: httpx.Response) -> None:
    leaked = [name for name in response.headers if name.lower().startswith("x-ratelimit")]
    assert leaked == []


@dataclass
class PageEnv:
    env: KeysApp
    gates: dict[str, asyncio.Event] = field(default_factory=dict)
    serial: int = 0

    def gate(self, name: str) -> asyncio.Event:
        return self.gates.setdefault(name, asyncio.Event())

    def open_all(self) -> None:
        for gate in self.gates.values():
            gate.set()
        self.env.release.set()

    def name(self) -> str:
        self.serial += 1
        return f"h{self.serial}"

    def start(
        self,
        ip: str = IP_A,
        *,
        name: str | None = None,
        prefix: str = PAGE_HOLD,
        method: str = "get",
        headers: dict[str, str] | None = None,
    ) -> "asyncio.Future[httpx.Response]":
        """Start a request that stays in flight until `gate(name)` is opened."""
        client = self.env.client(ip, headers)
        url = f"{prefix}/{name or self.name()}"
        return asyncio.ensure_future(getattr(client, method)(url))

    async def fill(
        self,
        count: int,
        ip: str = IP_A,
        *,
        prefix: str = PAGE_HOLD,
        headers: dict[str, str] | None = None,
    ) -> dict[str, "asyncio.Future[httpx.Response]"]:
        """Start `count` held requests and wait until all of them are inside the handler."""
        target = len(self.env.entered) + count
        tasks: dict[str, asyncio.Future[httpx.Response]] = {}
        for _ in range(count):
            name = self.name()
            tasks[name] = self.start(ip, name=name, prefix=prefix, headers=headers)
        await self.env.wait_entered(target)
        return tasks

    async def probe(self, ip: str = IP_A, path: str | None = None) -> httpx.Response:
        """A request that is refused at once or answered at once (it is never held)."""
        return await self.env.client(ip).get(path or f"{PAGE_HOLD}/probe-{self.name()}")

    async def finish(self, tasks: dict[str, "asyncio.Future[httpx.Response]"]) -> list[int]:
        for name in tasks:
            self.gate(name).set()
        return [r.status_code for r in await asyncio.gather(*tasks.values())]


def install_page_routes(page: PageEnv) -> None:
    env = page.env
    app = env.app

    async def hold(name: str):  # type: ignore[no-untyped-def]
        env.entered.append(1)
        await page.gate(name).wait()
        return {"ok": True}

    async def hold304(name: str):  # type: ignore[no-untyped-def]
        env.entered.append(1)
        await page.gate(name).wait()
        return Response(status_code=304)

    async def stream(name: str):  # type: ignore[no-untyped-def]
        async def body():  # type: ignore[no-untyped-def]
            yield b"first"
            env.entered.append(1)
            await page.gate(name).wait()
            yield b"last"

        return StreamingResponse(body(), media_type="text/plain")

    def small():  # type: ignore[no-untyped-def]
        return Response(content=b"ok", media_type="text/plain")

    async def boom():  # type: ignore[no-untyped-def]
        raise RuntimeError("kaboom")

    before = len(app.router.routes)
    for path, handler in (
        (f"{PAGE_HOLD}/{{name}}", hold),
        (f"{PAGE_HOLD_304}/{{name}}", hold304),
        (f"{PAGE_STREAM}/{{name}}", stream),
        (PAGE_SMALL, small),
        (PAGE_BOOM, boom),
    ):
        app.add_api_route(path, handler, methods=["GET", "HEAD"], include_in_schema=False)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = added  # in front of any frontend catch-all


@pytest.fixture
async def make_page(make_keys_app: MakeApp, tmp_path: Path) -> Callable[..., Awaitable[PageEnv]]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        f"<!doctype html><html><body><!-- {INDEX_MARKER} --></body></html>", encoding="utf-8"
    )
    (dist / "assets" / "app-abc123.js").write_text("console.log(1);\n", encoding="utf-8")
    (dist / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    made: list[PageEnv] = []

    async def make(**extra: Any) -> PageEnv:
        values: dict[str, Any] = {
            "rate_limit_page_concurrent": N,
            "rate_limit_per_minute": 1000,
            "rate_limit_per_hour": 100_000,
            "frontend_dist_dir": dist,
            "mer_image_cache_dir": tmp_path / "mer-images",
            **extra,
        }
        env: KeysApp = await make_keys_app(**values)
        page = PageEnv(env)
        install_page_routes(page)
        made.append(page)
        return page

    yield make
    for page in made:  # before the apps are closed: nothing may stay held
        page.open_all()


MakePage = Callable[..., Awaitable[PageEnv]]


# --- 1: the limit itself ---


async def test_page_n_requests_in_flight_are_served_and_the_next_one_is_refused(
    make_page: MakePage,
) -> None:
    page = await make_page()
    tasks = await page.fill(N)

    refused = await page.probe()
    served = await page.finish(tasks)

    assert_page_429(refused)
    assert served == [200] * N


async def test_page_served_responses_carry_no_rate_limit_headers(make_page: MakePage) -> None:
    page = await make_page()
    tasks = await page.fill(N)
    page.open_all()

    responses = await asyncio.gather(*tasks.values())

    assert [r.json() for r in responses] == [{"ok": True}] * N
    for response in responses:
        assert_no_limit_headers(response)


def test_page_concurrent_setting_defaults_to_16_and_must_be_positive() -> None:
    field_info = Settings.model_fields["rate_limit_page_concurrent"]

    assert field_info.default == 16
    assert any(getattr(item, "ge", None) == 1 for item in field_info.metadata)


def test_page_concurrent_env_name_sets_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PAGE_CONCURRENT", "7")

    assert Settings(_env_file=None).rate_limit_page_concurrent == 7  # type: ignore[call-arg]


async def test_page_app_built_without_the_override_uses_the_default_of_16(
    make_keys_app: MakeApp,
) -> None:
    """The app, not just the field, applies 16 when nothing is configured."""
    env: KeysApp = await make_keys_app()
    page = PageEnv(env)
    install_page_routes(page)
    tasks = await page.fill(16)

    refused = await page.probe()
    served = await page.finish(tasks)

    assert_page_429(refused)
    assert served == [200] * 16


# --- 2: every kind of non-API path is subject to it ---

PAGE_PATHS = [
    pytest.param("/", id="root"),
    pytest.param("/assets/app-abc123.js", id="frontend-asset"),
    pytest.param("/robots.txt", id="frontend-file"),
    pytest.param("/rovers/curiosity", id="frontend-page"),
    pytest.param("/health", id="health"),
    pytest.param("/health/sync", id="health-sync"),
    pytest.param("/docs", id="docs"),
    pytest.param("/redoc", id="redoc"),
    pytest.param("/openapi.json", id="openapi"),
    pytest.param("/developers", id="developers"),
    pytest.param("/llms.txt", id="llms"),
]


@pytest.mark.parametrize("path", PAGE_PATHS)
async def test_page_path_is_refused_when_the_address_has_n_requests_in_flight(
    make_page: MakePage, path: str
) -> None:
    page = await make_page()
    tasks = await page.fill(N)

    refused = await page.probe(path=path)
    served = await page.finish(tasks)

    assert_page_429(refused)
    assert served == [200] * N


@pytest.mark.parametrize("path", PAGE_PATHS)
async def test_page_path_is_served_when_the_address_has_a_free_slot(
    make_page: MakePage, path: str
) -> None:
    page = await make_page()
    tasks = await page.fill(N - 1)

    response = await page.probe(path=path)
    await page.finish(tasks)

    assert response.status_code != 429  # /health/sync may answer 503 when the data is stale
    assert_no_limit_headers(response)


# --- 3: independence from the API counter ---

API_PATHS = [
    pytest.param(SMALL_V2, id="v2"),
    pytest.param(SMALL_V1, id="v1"),
    pytest.param(SMALL_V1_ALT, id="v1-legacy-prefix"),
]


@pytest.mark.parametrize("path", API_PATHS)
async def test_full_page_counter_does_not_refuse_api_requests(
    make_page: MakePage, path: str
) -> None:
    page = await make_page()
    tasks = await page.fill(N)

    api = await page.probe(path=path)
    page_refused = await page.probe()
    await page.finish(tasks)

    assert api.status_code == 200
    assert {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"} <= set(api.headers)
    assert_page_429(page_refused)


@pytest.mark.parametrize("path", PAGE_PATHS[:1] + PAGE_PATHS[4:5] + PAGE_PATHS[-1:])
async def test_full_api_counter_does_not_refuse_page_requests(
    make_page: MakePage, path: str
) -> None:
    page = await make_page(rate_limit_concurrent=2, rate_limit_page_concurrent=N)
    target = len(page.env.entered) + 2
    held = [asyncio.ensure_future(page.env.client(IP_A).get(HOLD_V2)) for _ in range(2)]
    await page.env.wait_entered(target)

    api_refused = await page.env.client(IP_A).get(SMALL_V2)
    response = await page.probe(path=path)
    page.open_all()
    done = await asyncio.gather(*held)

    assert api_refused.status_code == 429  # the API counter really is full
    assert "x-ratelimit-limit" in api_refused.headers
    assert response.status_code != 429
    assert_no_limit_headers(response)
    assert [r.status_code for r in done] == [200, 200]


async def test_held_api_requests_do_not_take_page_slots_and_the_reverse(
    make_page: MakePage,
) -> None:
    page = await make_page(rate_limit_concurrent=2, rate_limit_page_concurrent=2)
    page_tasks = await page.fill(2)  # page counter full
    target = len(page.env.entered) + 2
    api_tasks = [asyncio.ensure_future(page.env.client(IP_A).get(HOLD_V2)) for _ in range(2)]
    await page.env.wait_entered(target)  # both API requests are admitted: own counter

    page_refused = await page.probe()
    api_refused = await page.env.client(IP_A).get(SMALL_V2)
    page.open_all()
    results = await asyncio.gather(*page_tasks.values(), *api_tasks)

    assert_page_429(page_refused)
    assert api_refused.status_code == 429
    assert [r.status_code for r in results] == [200] * 4


# --- 4: addresses ---


async def test_page_other_address_is_not_affected(make_page: MakePage) -> None:
    page = await make_page()
    tasks = await page.fill(N, IP_A)

    other = await page.probe(IP_B, PAGE_SMALL)
    same = await page.probe(IP_A)
    page.open_all()
    await asyncio.gather(*tasks.values())

    assert other.status_code == 200
    assert_page_429(same)


async def test_page_two_addresses_of_one_64_share_the_counter(make_page: MakePage) -> None:
    page = await make_page(rate_limit_page_concurrent=2)
    first = await page.fill(1, ip6(NET_A, 1, 1))
    second = await page.fill(1, ip6(NET_A, 1, 2))

    third_address = await page.probe(ip6(NET_A, 1, 3))
    first_address_again = await page.probe(ip6(NET_A, 1, 1))
    await page.finish({**first, **second})

    assert_page_429(third_address)
    assert_page_429(first_address_again)


async def test_page_different_64_of_one_48_are_separate_until_the_48_budget_is_full(
    make_page: MakePage,
) -> None:
    """N = 2, factor 2: every /64 holds 2, the /48 holds 4."""
    page = await make_page(rate_limit_page_concurrent=2, rate_limit_ipv6_site_factor=2)
    net1 = await page.fill(2, ip6(NET_A, 1))  # the /64 is full, the /48 has 2 of 4
    net2_first = await page.fill(1, ip6(NET_A, 2))  # a free /64 is served: 3 of 4
    net2_second = await page.fill(1, ip6(NET_A, 2, 2))  # 4 of 4

    net3 = await page.probe(ip6(NET_A, 3))  # its own /64 is empty, the /48 is full
    other_48 = await page.probe(ip6(NET_B, 1), PAGE_SMALL)
    await page.finish({**net1, **net2_first, **net2_second})

    assert_page_429(net3)
    assert other_48.status_code == 200


async def test_page_48_budget_frees_up_when_a_request_finishes(make_page: MakePage) -> None:
    page = await make_page(rate_limit_page_concurrent=2, rate_limit_ipv6_site_factor=2)
    net1 = await page.fill(2, ip6(NET_A, 1))
    net2 = await page.fill(2, ip6(NET_A, 2))
    assert_page_429(await page.probe(ip6(NET_A, 3)))

    name = next(iter(net1))
    page.gate(name).set()
    await net1[name]
    admitted = await page.fill(1, ip6(NET_A, 3))  # exactly one place is free again
    refused = await page.probe(ip6(NET_A, 4))
    await page.finish({**{k: v for k, v in net1.items() if k != name}, **net2, **admitted})

    assert_page_429(refused)


async def test_page_ipv6_site_factor_is_applied_to_the_page_budget(make_page: MakePage) -> None:
    """With factor 1 a /48 is no larger than one /64: a second /64 of it has no room."""
    page = await make_page(rate_limit_page_concurrent=2, rate_limit_ipv6_site_factor=1)
    net1 = await page.fill(2, ip6(NET_A, 1))

    net2 = await page.probe(ip6(NET_A, 2))
    await page.finish(net1)

    assert_page_429(net2)


async def test_page_ipv4_addresses_have_no_shared_budget(make_page: MakePage) -> None:
    page = await make_page(rate_limit_page_concurrent=1, rate_limit_ipv6_site_factor=1)
    tasks: dict[str, asyncio.Future[httpx.Response]] = {}
    for ip in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        tasks.update(await page.fill(1, ip))  # one each: no shared /24 budget

    assert await page.finish(tasks) == [200, 200, 200]


async def test_api_site_budget_does_not_refuse_page_requests_of_the_48(
    make_page: MakePage,
) -> None:
    """The API's /48 concurrency (1 x 2) is full; the page counters of the /48 are untouched."""
    page = await make_page(
        rate_limit_concurrent=1, rate_limit_ipv6_site_factor=2, rate_limit_page_concurrent=2
    )
    target = len(page.env.entered) + 2
    held = [asyncio.ensure_future(page.env.client(ip6(NET_A, net)).get(HOLD_V2)) for net in (1, 2)]
    await page.env.wait_entered(target)

    api_refused = await page.env.client(ip6(NET_A, 3)).get(SMALL_V2)
    response = await page.probe(ip6(NET_A, 3), PAGE_SMALL)
    page.open_all()
    await asyncio.gather(*held)

    assert api_refused.status_code == 429
    assert response.status_code == 200


# --- 5: an API key does not change the counter ---


async def test_page_valid_api_key_does_not_change_the_key_of_the_counter(
    make_page: MakePage,
) -> None:
    page = await make_page()
    key = await account_with_key(page.env.factory)
    keyed = {"X-Api-Key": key.value}
    anonymous = await page.fill(N - 1, IP_A)
    with_key = await page.fill(1, IP_A, headers=keyed)  # same address: same counter

    keyed_refused = await page.env.client(IP_A, keyed).get(f"{PAGE_HOLD}/probe")
    anonymous_refused = await page.probe(IP_A)
    other_address_with_key = await page.env.client(IP_B, keyed).get(PAGE_SMALL)
    await page.finish({**anonymous, **with_key})

    assert_page_429(keyed_refused)
    assert_page_429(anonymous_refused)
    assert other_address_with_key.status_code == 200  # not an account-wide counter


async def test_page_requests_with_a_key_do_not_get_the_larger_keyed_limit(
    make_page: MakePage,
) -> None:
    page = await make_page(rate_limit_key_concurrent=50)
    key = await account_with_key(page.env.factory)
    tasks = await page.fill(N, IP_A, headers={"X-Api-Key": key.value})

    refused = await page.env.client(IP_A, {"X-Api-Key": key.value}).get(f"{PAGE_HOLD}/probe")
    await page.finish(tasks)

    assert_page_429(refused)


async def test_page_invalid_api_key_header_is_ignored_on_pages(make_page: MakePage) -> None:
    page = await make_page()

    response = await page.env.client(IP_A, {"X-Api-Key": "not-a-key"}).get(PAGE_SMALL)

    assert response.status_code == 200
    assert_no_limit_headers(response)


# --- 6: the slot is released ---


async def assert_n_fit_again(page: PageEnv, ip: str = IP_A) -> None:
    tasks = await page.fill(N, ip)
    refused = await page.probe(ip)
    served = await page.finish(tasks)
    assert_page_429(refused)
    assert served == [200] * N


async def test_page_slots_are_released_after_the_responses_are_complete(
    make_page: MakePage,
) -> None:
    page = await make_page()
    first = await page.fill(N)
    assert await page.finish(first) == [200] * N

    await assert_n_fit_again(page)


async def test_page_slot_is_held_until_a_streamed_response_is_complete(
    make_page: MakePage,
) -> None:
    """The handler has returned and the headers are out, but the body is still being sent."""
    page = await make_page()
    tasks = await page.fill(N, prefix=PAGE_STREAM)

    refused = await page.probe()
    served = await page.finish(tasks)
    after = await page.probe(path=PAGE_SMALL)

    assert_page_429(refused)
    assert served == [200] * N
    assert after.status_code == 200


async def test_page_slots_are_released_after_a_failing_handler(make_page: MakePage) -> None:
    page = await make_page()
    crashed = [await page.env.client(IP_A).get(PAGE_BOOM) for _ in range(N + 2)]

    assert [r.status_code for r in crashed] == [500] * (N + 2)
    await assert_n_fit_again(page)


async def test_page_slots_are_released_after_the_client_cancels(make_page: MakePage) -> None:
    page = await make_page()
    tasks = await page.fill(N)
    for task in tasks.values():
        task.cancel()
    await asyncio.gather(*tasks.values(), return_exceptions=True)

    await assert_n_fit_again(page)


async def test_page_slots_are_released_after_an_aborted_streamed_response(
    make_page: MakePage,
) -> None:
    page = await make_page()
    tasks = await page.fill(N, prefix=PAGE_STREAM)
    for task in tasks.values():
        task.cancel()
    await asyncio.gather(*tasks.values(), return_exceptions=True)

    await assert_n_fit_again(page)


async def test_page_refused_request_takes_no_slot_and_one_finish_frees_exactly_one(
    make_page: MakePage,
) -> None:
    page = await make_page()
    tasks = await page.fill(N)
    refused = [await page.probe() for _ in range(5)]  # many refusals must not leak or consume
    for response in refused:
        assert_page_429(response)

    first = next(iter(tasks))
    page.gate(first).set()
    await tasks[first]
    admitted = await page.fill(1)  # exactly one more fits
    still_full = await page.probe()
    rest = {k: v for k, v in tasks.items() if k != first}
    served = await page.finish({**rest, **admitted})

    assert_page_429(still_full)
    assert served == [200] * N


# --- 7: HEAD and 304 ---


async def test_page_head_request_in_flight_takes_a_slot(make_page: MakePage) -> None:
    page = await make_page()
    target = len(page.env.entered) + 1
    head_name = page.name()
    head = page.start(name=head_name, method="head")
    await page.env.wait_entered(target)
    rest = await page.fill(N - 1)

    refused = await page.probe()
    served = await page.finish({head_name: head, **rest})

    assert_page_429(refused)
    assert served == [200] * N


async def test_page_head_request_is_refused_when_the_counter_is_full(make_page: MakePage) -> None:
    page = await make_page()
    tasks = await page.fill(N)

    refused = await page.env.client(IP_A).head(f"{PAGE_HOLD}/probe")
    await page.finish(tasks)

    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "1"
    assert refused.headers["cache-control"] == "no-store"
    assert_no_limit_headers(refused)


async def test_page_not_modified_response_in_flight_takes_a_slot(make_page: MakePage) -> None:
    page = await make_page()
    revalidating = await page.fill(N - 1, prefix=PAGE_HOLD_304)
    target = len(page.env.entered) + 1
    last_name = page.name()
    last = page.start(name=last_name, prefix=PAGE_HOLD_304)
    await page.env.wait_entered(target)

    refused = await page.probe()
    served = await page.finish({**revalidating, last_name: last})

    assert_page_429(refused)
    assert served == [304] * N


# --- 8: switched off ---


async def test_page_limit_is_off_when_rate_limit_is_disabled(make_page: MakePage) -> None:
    page = await make_page(rate_limit_enabled=False, rate_limit_page_concurrent=2)
    tasks = await page.fill(2 * N + 4)

    extra = await page.fill(1)
    served = await page.finish({**tasks, **extra})

    assert served == [200] * (2 * N + 4) + [200]


# --- 9: no windows ---


@pytest.mark.parametrize(
    "path",
    ["/", "/assets/app-abc123.js", "/llms.txt", "/developers", PAGE_SMALL],
    ids=["root", "frontend-asset", "llms", "developers", "plain"],
)
async def test_page_many_sequential_requests_within_a_minute_all_succeed(
    make_page: MakePage, path: str
) -> None:
    page = await make_page(rate_limit_per_minute=3, rate_limit_per_hour=3)
    client = page.env.client(IP_A)

    codes = [(await client.get(path)).status_code for _ in range(150)]

    assert codes == [200] * 150
