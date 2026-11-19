# ruff: noqa: F811
"""MER image endpoint, stage 9a review: DB connection, cache key, queue, SSRF, internal errors.

Contract (docs/API.md, "Spirit и Opportunity, size=full"):
- no DB connection is held while a photo is downloaded or converted;
- the cache key is ``{id}-{sha256(source_link)[:16]}``: another source link is a new entry;
- at most ``MER_CONVERT_CONCURRENCY`` conversions run and at most ``MER_CONVERT_QUEUE``
  (default 8) wait; one more distinct photo gives ``503 converter_busy`` with
  ``Retry-After: 10`` and ``no-store``; requests merged into an in-flight photo do not count;
- the converter has its own PDS client with ``MER_PDS_MAX_RPS`` (default 2), independent of
  ``UPSTREAM_MAX_RPS`` (sync);
- a Spirit/Opportunity photo without ``source_link`` shows the preview as ``images.full``;
- a ``source_link`` outside the PDS host and any internal conversion error give ``502``;
- the JPEG response is never gzipped.
Shared helpers and the ``api`` fixture live in ``test_mer_image.py``.
"""

import asyncio
import hashlib
import threading
import time
from typing import Any

import httpx
import pytest
import respx
from PIL import Image
from sqlalchemy import text

from app.core.config import Settings
from tests.api.v2.test_mer_image import (  # noqa: F401 (api is a fixture)
    EDN,
    ESF,
    V2,
    ApiFactory,
    Factory,
    _real,
    api,
    browse_url,
    expected_jpeg,
    mer_record,
    pds_url,
    seed,
)

pytestmark = pytest.mark.api


def _checked_out(client: httpx.AsyncClient) -> int:
    return client.mer_app.state.engine.sync_engine.pool.checkedout()  # type: ignore[attr-defined]


@pytest.mark.parametrize("stage", ["slow-download", "slow-conversion"])
async def test_image_does_not_hold_a_db_connection_while_converting(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    from app.services import mer_images

    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    data = _real(ESF)
    entered = asyncio.Event()
    gate = asyncio.Event()
    started = threading.Event()
    release = threading.Event()

    async def pds(request: httpx.Request) -> httpx.Response:
        entered.set()
        if stage == "slow-download":
            await gate.wait()
        return httpx.Response(200, content=data)

    upstream_router.get(pds_url("P1")).mock(side_effect=pds)
    real_encode = mer_images.encode_jpeg

    def slow_encode(*args: Any, **kwargs: Any) -> bytes:
        started.set()
        release.wait(10)
        return real_encode(*args, **kwargs)

    if stage == "slow-conversion":
        monkeypatch.setattr(mer_images, "encode_jpeg", slow_encode)

    async with api() as client:
        task = asyncio.create_task(client.get(f"{V2}/photos/{ids['P1']}/image"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if stage == "slow-conversion":
                assert await asyncio.wait_for(asyncio.to_thread(started.wait, 5), 6)
            assert not task.done()  # the image request is in flight right now
            assert _checked_out(client) == 0
            others = await asyncio.wait_for(client.get(f"{V2}/rovers"), 2)
            assert others.status_code == 200  # other endpoints answer promptly meanwhile
        finally:
            gate.set()
            release.set()
        response = await asyncio.wait_for(task, 10)

    assert response.status_code == 200


async def test_image_cache_key_changes_with_the_source_link(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, tmp_path: Any
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    old_data, new_data = _real(ESF), _real(EDN)
    new_url = pds_url("P1-REPLACED")
    old_route = upstream_router.get(pds_url("P1")).respond(200, content=old_data)
    new_route = upstream_router.get(new_url).respond(200, content=new_data)
    cache_dir = tmp_path / "keyed-cache"
    url = f"{V2}/photos/{ids['P1']}/image"

    async with api(mer_image_cache_dir=cache_dir) as client:
        first = await client.get(url)
        async with clean_db() as session:
            await session.execute(
                text("UPDATE photos SET source_link = :link WHERE id = :id"),
                {"link": new_url, "id": ids["P1"]},
            )
            await session.commit()
        second = await client.get(url)
        third = await client.get(url)

    assert first.content == expected_jpeg(old_data)
    assert second.content == expected_jpeg(new_data)  # same id, other source: not the old entry
    assert third.content == second.content
    assert (old_route.call_count, new_route.call_count) == (1, 1)
    key = f"{ids['P1']}-{hashlib.sha256(new_url.encode()).hexdigest()[:16]}"
    assert any(p.name.startswith(key) for p in cache_dir.rglob("*") if p.is_file())


async def test_image_queue_overflow_gives_503_converter_busy_but_merged_requests_wait(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import mer_images

    ids = await seed(clean_db, "spirit", [mer_record(f"P{n}") for n in range(4)])
    data = _real(ESF)
    for n in range(4):
        upstream_router.get(pds_url(f"P{n}")).respond(200, content=data)
    started = threading.Event()
    release = threading.Event()
    real_encode = mer_images.encode_jpeg

    def blocking_encode(*args: Any, **kwargs: Any) -> bytes:
        started.set()
        release.wait(15)
        return real_encode(*args, **kwargs)

    monkeypatch.setattr(mer_images, "encode_jpeg", blocking_encode)

    def get(client: httpx.AsyncClient, n: int) -> Any:
        return asyncio.ensure_future(client.get(f"{V2}/photos/{ids[f'P{n}']}/image"))

    async with api(mer_convert_concurrency=1, mer_convert_queue=2) as client:
        try:
            running = get(client, 0)  # 1 in work
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 5), 6)
            waiting = [get(client, 1), get(client, 2)]  # 2 waiting: the queue is full
            await asyncio.sleep(0.5)  # let them reach the queue
            merged = get(client, 0)  # the photo in work again: not counted against the queue
            busy = await asyncio.wait_for(client.get(f"{V2}/photos/{ids['P3']}/image"), 5)
        finally:
            release.set()
        done = await asyncio.wait_for(asyncio.gather(running, *waiting, merged), 20)
        after = await client.get(f"{V2}/photos/{ids['P3']}/image")

    assert busy.status_code == 503
    assert busy.headers["retry-after"] == "10"
    assert busy.headers["cache-control"] == "no-store"
    assert busy.json()["error"]["code"] == "converter_busy"
    assert [r.status_code for r in done] == [200, 200, 200, 200]
    assert after.status_code == 200  # the rejected request left no trace


def test_mer_converter_defaults_match_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MER_CONVERT_QUEUE", "MER_PDS_MAX_RPS", "MER_CONVERT_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)

    values = Settings(_env_file=None)

    assert values.mer_convert_queue == 8
    assert values.mer_pds_max_rps == 2
    assert values.mer_convert_concurrency == 2


async def test_image_converter_is_not_throttled_by_the_sync_rate_limit(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    """The converter has its own PDS client: UPSTREAM_MAX_RPS (shared with sync) is not applied."""
    ids = await seed(clean_db, "spirit", [mer_record(f"P{n}") for n in range(3)])
    for n in range(3):
        upstream_router.get(pds_url(f"P{n}")).respond(200, content=_real(ESF))

    # The shared client would wait 5 s between requests and blow the 3 s timeout.
    async with api(upstream_max_rps=0.2, mer_convert_timeout_seconds=3) as client:
        responses = [await client.get(f"{V2}/photos/{ids[f'P{n}']}/image") for n in range(3)]

    assert [r.status_code for r in responses] == [200, 200, 200]


async def test_image_converter_applies_its_own_pds_rate_limit(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record(f"P{n}") for n in range(3)])
    for n in range(3):
        upstream_router.get(pds_url(f"P{n}")).respond(200, content=_real(ESF))

    async with api(mer_pds_max_rps=2) as client:
        started = time.monotonic()
        responses = await asyncio.gather(
            *(client.get(f"{V2}/photos/{ids[f'P{n}']}/image") for n in range(3))
        )
        elapsed = time.monotonic() - started

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert elapsed >= 0.9  # 3 requests at 2 per second: two gaps of 0.5 s


async def test_photo_json_without_source_link_points_to_the_preview(
    api: ApiFactory, clean_db: Factory
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1", source_link=None)])

    async with api() as client:
        body = (await client.get(f"{V2}/photos/{ids['P1']}")).json()
        listing = (await client.get(f"{V2}/rovers/spirit/photos")).json()["items"][0]

    for item in (body, listing):
        assert item["img_src"] == browse_url("P1")
        assert item["images"]["full"] == browse_url("P1")
        assert item["images"]["small"] == browse_url("P1")
        assert item["source"]["link"] is None


FOREIGN_LINKS = {
    "other-host": "https://evil.example.test/img/data/mer/mer2no_0xxx/data/sol0100/edr/p1.img",
    "internal-ip": "http://169.254.169.254/latest/meta-data/",
    "localhost": "http://localhost:8080/admin",
    "suffix-trick": "https://planetarydata.jpl.nasa.gov.evil.example.test/img/data/mer/p1.img",
    "userinfo-trick": "https://planetarydata.jpl.nasa.gov@evil.example.test/img/data/mer/p1.img",
    "file-scheme": "file:///etc/passwd",
}


@pytest.mark.parametrize("link", FOREIGN_LINKS.values(), ids=FOREIGN_LINKS.keys())
async def test_image_source_link_on_a_foreign_host_gives_502_without_any_request(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, link: str
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1", source_link=link)])
    catch_all = upstream_router.route().mock(return_value=httpx.Response(200, content=b"x"))

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert response.headers["cache-control"] == "no-store"
    assert catch_all.call_count == 0
    assert "evil" not in response.text and "169.254" not in response.text


INTERNAL_ERRORS = {
    "encoder-oserror": ("encode_jpeg", OSError("encoder failed")),
    "encoder-runtime": ("encode_jpeg", RuntimeError("boom")),
    "stretch-valueerror": ("stretch_to_8bit", ValueError("bad array")),
    "pillow-bomb": ("encode_jpeg", Image.DecompressionBombError("too big")),
}


@pytest.mark.parametrize("target_exc", INTERNAL_ERRORS.values(), ids=INTERNAL_ERRORS.keys())
async def test_image_internal_conversion_error_gives_502_not_500(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    target_exc: tuple[str, Exception],
) -> None:
    from app.services import mer_images

    name, exc = target_exc
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=_real(ESF))

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr(mer_images, name, broken)

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert response.headers["cache-control"] == "no-store"
    assert "boom" not in response.text and "encoder failed" not in response.text


async def test_image_jpeg_is_not_gzipped(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    data = _real(EDN)
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=data)

    async with api() as client:
        response = await client.get(
            f"{V2}/photos/{ids['P1']}/image", headers={"Accept-Encoding": "gzip"}
        )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == expected_jpeg(data)
    assert response.headers["content-length"] == str(len(response.content))


async def test_image_conversion_slot_is_held_until_the_worker_thread_ends(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out request must not free its slot while its thread still runs."""
    from app.services import mer_images

    ids = await seed(clean_db, "spirit", [mer_record("P1"), mer_record("P2")])
    for source_id in ("P1", "P2"):
        upstream_router.get(pds_url(source_id)).respond(200, content=_real(ESF))
    lock = threading.Lock()
    release_first = threading.Event()
    first_finished = threading.Event()
    state = {"calls": 0, "now": 0, "peak": 0}
    real_encode = mer_images.encode_jpeg

    def encode(*args: Any, **kwargs: Any) -> bytes:
        with lock:
            state["calls"] += 1
            first = state["calls"] == 1
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            if first:
                release_first.wait(15)
            return real_encode(*args, **kwargs)
        finally:
            with lock:
                state["now"] -= 1
            if first:
                first_finished.set()

    monkeypatch.setattr(mer_images, "encode_jpeg", encode)

    async with api(
        mer_convert_concurrency=1, mer_convert_queue=5, mer_convert_timeout_seconds=0.5
    ) as client:
        try:
            first = await client.get(f"{V2}/photos/{ids['P1']}/image")
            second = await client.get(f"{V2}/photos/{ids['P2']}/image")
            calls_while_first_runs = state["calls"]
        finally:
            release_first.set()
        assert await asyncio.wait_for(asyncio.to_thread(first_finished.wait, 10), 11)
        third = await client.get(f"{V2}/photos/{ids['P2']}/image")

    assert first.status_code == 504
    assert second.status_code == 504  # the only slot is still busy with the first thread
    assert calls_while_first_runs == 1  # the second photo's conversion never started
    assert third.status_code == 200
    assert state["peak"] == 1


async def test_image_service_aclose_cancels_in_flight_work_and_does_not_hang_waiters(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1"), mer_record("P2")])
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200, content=b"never")

    upstream_router.get(pds_url("P1")).mock(side_effect=hang)
    upstream_router.get(pds_url("P2")).mock(side_effect=hang)

    async with api(
        mer_convert_concurrency=1, mer_convert_queue=3, mer_convert_timeout_seconds=30
    ) as client:
        service = client.mer_app.state.mer_image_service  # type: ignore[attr-defined]
        running = asyncio.ensure_future(client.get(f"{V2}/photos/{ids['P1']}/image"))
        await asyncio.wait_for(entered.wait(), 5)
        waiting = asyncio.ensure_future(client.get(f"{V2}/photos/{ids['P2']}/image"))
        await asyncio.sleep(0.3)  # the second one is queued behind the first

        await asyncio.wait_for(service.aclose(), 5)

        results = await asyncio.wait_for(
            asyncio.gather(running, waiting, return_exceptions=True), 5
        )

    assert cancelled.is_set()  # the in-flight download was cancelled
    for result in results:  # an error response or a cancellation, but never a hang or a 200
        if isinstance(result, httpx.Response):
            assert result.status_code >= 500
        else:
            assert isinstance(result, BaseException)
