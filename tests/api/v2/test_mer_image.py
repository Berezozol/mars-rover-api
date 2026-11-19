"""GET /api/v2/photos/{id}/image for Spirit and Opportunity (stage 9a).

Contract (docs/API.md, "Spirit и Opportunity, size=full"): the full frame is converted from
the PDS3 ``.img`` of ``source.link`` to a JPEG (percentile stretch 0.5/99.5, quality 90) and
returned as ``200 image/jpeg`` with ``Cache-Control: public, max-age=86400``, a weak ETag
(``304`` on ``If-None-Match``) and a body-less HEAD; results are cached on disk (one PDS
call per photo); equal concurrent requests share one conversion; at most
``MER_CONVERT_CONCURRENCY`` conversions run at once; ``size=small`` redirects to the PDS
preview, ``medium`` / ``large`` act as ``full``; PDS errors or an unreadable file give
``502 upstream_error``, a timeout ``504 upstream_timeout``, both ``no-store`` and never
cached. Curiosity and Perseverance keep their 302. PDS is mocked with respx.
"""

import asyncio
import io
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
import respx
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from app.services.mer_images import encode_jpeg, stretch_to_8bit
from app.sources.pds3 import parse_pds3_image
from tests.conftest import build_record
from tests.unit._pds3 import SAMPLE_FORMATS, build_pds3, make_pixels, real_samples

pytestmark = pytest.mark.api

V2 = "/api/v2"
PDS = "https://planetarydata.jpl.nasa.gov/img/data/mer/"
Factory = async_sessionmaker[AsyncSession]
ApiFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


def _real(name: str) -> bytes:
    return next(p for p in real_samples() if p.name == name).read_bytes()


ESF = "2p135232363esf2700p2682l8m1.img"  # 63 x 63, 16 bit MSB
EDN = "2n160633049edna2k1f0006r0m1.img"  # 256 x 256, 16 bit MSB


def expected_jpeg(data: bytes) -> bytes:
    """What the contract says the endpoint returns for a PDS3 file."""
    return encode_jpeg(stretch_to_8bit(parse_pds3_image(data).pixels), quality=90)


def pds_url(source_id: str) -> str:
    return f"{PDS}mer2no_0xxx/data/sol0100/edr/{source_id.lower()}.img"


def browse_url(source_id: str) -> str:
    return f"{PDS}mer2no_0xxx/browse/sol0100/edr/{source_id.lower()}.img.jpg"


def mer_record(source_id: str, **overrides: Any) -> Any:
    return build_record(
        source_id=source_id,
        sol=100,
        camera_code="NAVCAM",
        sample_type="full",
        img_full=browse_url(source_id),
        **({"source_link": pds_url(source_id)} | overrides),
    )


async def seed(factory: Factory, rover: str, records: list[Any]) -> dict[str, int]:
    async with factory() as session:
        await upsert_photos(session, rover, records)
        await recompute_sol_summaries(session, rover, {r.sol for r in records})
        await refresh_rover_stats(session, rover)
        await session.commit()
        rows = await session.execute(text("SELECT source_id, id FROM photos"))
        return {source_id: photo_id for source_id, photo_id in rows}


@pytest.fixture
def api(settings: Settings, tmp_path: Any, clean_db: Factory) -> ApiFactory:
    """Client factory: app with a private image cache and fast, unthrottled upstream settings."""
    created: list[Any] = []

    @asynccontextmanager
    async def factory(**overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
        values = {
            "mer_image_cache_dir": tmp_path / f"cache-{len(created)}",
            "upstream_max_rps": 0,
            "mer_pds_max_rps": 1000,
            "upstream_max_retries": 0,
            "upstream_retry_base_seconds": 0,
        } | overrides
        app = create_app(settings.model_copy(update=values))
        created.append(app)
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                client.mer_app = app  # type: ignore[attr-defined]  # for pool inspection
                yield client
        finally:
            await close_app(app)

    return factory


async def close_app(app: Any) -> None:
    """Close every lazily created HTTP client of the app, then the DB engine."""
    shared = getattr(app.state, "upstream_client", None)
    if shared is not None:
        await shared.aclose()
    service = getattr(app.state, "mer_image_service", None)
    closer = getattr(service, "aclose", None)
    if closer is not None:
        await closer()
    await app.state.engine.dispose()


def assert_jpeg(content: bytes, size: tuple[int, int]) -> None:
    image = Image.open(io.BytesIO(content))
    image.load()
    assert image.format == "JPEG" and image.mode == "L" and image.size == size


def assert_conversion_headers(response: httpx.Response) -> None:
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.headers["etag"]


# --- the converted image -------------------------------------------------------------------


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
@pytest.mark.parametrize(("name", "size"), [(ESF, (63, 63)), (EDN, (256, 256))], ids=["esf", "edn"])
async def test_image_full_converts_the_pds_file_to_jpeg(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    rover: str,
    name: str,
    size: tuple[int, int],
) -> None:
    data = _real(name)
    ids = await seed(clean_db, rover, [mer_record("P1")])
    route = upstream_router.get(pds_url("P1")).respond(200, content=data)

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image", params={"size": "full"})

    assert response.status_code == 200
    assert_conversion_headers(response)
    assert_jpeg(response.content, size)
    assert response.content == expected_jpeg(data)
    assert route.call_count == 1
    assert "location" not in response.headers


async def test_image_without_size_is_full(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    data = _real(ESF)
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=data)

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 200
    assert response.content == expected_jpeg(data)


@pytest.mark.parametrize("fmt", list(SAMPLE_FORMATS))
async def test_image_converts_every_sample_format_with_prefix_bytes(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, fmt: str
) -> None:
    pixels = make_pixels(12, 20, fmt)
    data = build_pds3(pixels, fmt=fmt, prefix=4, suffix=2, realistic=True).data
    ids = await seed(clean_db, "opportunity", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=data)

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 200
    assert_jpeg(response.content, (20, 12))
    assert response.content == expected_jpeg(data)


@pytest.mark.parametrize("size", ["medium", "large", "FULL", "Large"])
async def test_image_other_sizes_fall_back_to_full_conversion(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, size: str
) -> None:
    data = _real(ESF)
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=data)

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image", params={"size": size})

    assert response.status_code == 200
    assert response.content == expected_jpeg(data)


@pytest.mark.parametrize("size", ["small", "SMALL"])
async def test_image_small_redirects_to_the_pds_preview_without_touching_pds(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, size: str
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image", params={"size": size})

    assert response.status_code == 302
    assert response.headers["location"] == browse_url("P1")
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert upstream_router.calls.call_count == 0


async def test_image_without_source_link_redirects_to_the_preview(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    record = mer_record("P1", source_link=None)
    ids = await seed(clean_db, "spirit", [record])

    async with api() as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 302
    assert response.headers["location"] == browse_url("P1")


# --- ETag, HEAD ----------------------------------------------------------------------------


async def test_image_if_none_match_gives_304_without_body(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    route = upstream_router.get(pds_url("P1")).respond(200, content=_real(ESF))
    url = f"{V2}/photos/{ids['P1']}/image"

    async with api() as client:
        first = await client.get(url)
        etag = first.headers["etag"]
        again = await client.get(url, headers={"If-None-Match": etag})
        other = await client.get(url, headers={"If-None-Match": 'W/"something-else"'})

    assert etag.startswith("W/")
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == etag
    assert again.headers["cache-control"] == "public, max-age=86400"
    assert other.status_code == 200 and other.content == first.content
    assert route.call_count == 1


async def test_image_etag_is_stable_between_requests(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=_real(ESF))

    async with api() as client:
        first = await client.get(f"{V2}/photos/{ids['P1']}/image")
        second = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert first.headers["etag"] == second.headers["etag"]


async def test_image_head_has_headers_and_no_body(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=_real(ESF))

    async with api() as client:
        got = await client.get(f"{V2}/photos/{ids['P1']}/image")
        response = await client.head(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 200
    assert_conversion_headers(response)
    assert response.content == b""
    assert response.headers["content-length"] == str(len(got.content))
    assert response.headers["etag"] == got.headers["etag"]


# --- cache and concurrency -----------------------------------------------------------------


async def test_image_second_request_is_served_from_the_cache(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    route = upstream_router.get(pds_url("P1")).respond(200, content=_real(EDN))

    async with api() as client:
        first = await client.get(f"{V2}/photos/{ids['P1']}/image")
        second = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert route.call_count == 1


async def test_image_cache_survives_an_application_restart(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, tmp_path: Any
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    route = upstream_router.get(pds_url("P1")).respond(200, content=_real(ESF))
    shared = {"mer_image_cache_dir": tmp_path / "shared-cache"}

    async with api(**shared) as client:
        first = await client.get(f"{V2}/photos/{ids['P1']}/image")
    async with api(**shared) as client:
        second = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert first.content == second.content
    assert route.call_count == 1


async def test_image_cache_is_per_photo(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1"), mer_record("P2")])
    upstream_router.get(pds_url("P1")).respond(200, content=_real(ESF))
    upstream_router.get(pds_url("P2")).respond(200, content=_real(EDN))

    async with api() as client:
        one = await client.get(f"{V2}/photos/{ids['P1']}/image")
        two = await client.get(f"{V2}/photos/{ids['P2']}/image")

    assert_jpeg(one.content, (63, 63))
    assert_jpeg(two.content, (256, 256))


async def test_image_concurrent_requests_of_one_photo_make_one_pds_call(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    data = _real(ESF)
    ids = await seed(clean_db, "spirit", [mer_record("P1")])

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)  # let every request arrive while the download is in flight
        return httpx.Response(200, content=data)

    route = upstream_router.get(pds_url("P1")).mock(side_effect=slow)

    async with api() as client:
        responses = await asyncio.gather(
            *(client.get(f"{V2}/photos/{ids['P1']}/image") for _ in range(6))
        )

    assert [r.status_code for r in responses] == [200] * 6
    assert {r.content for r in responses} == {expected_jpeg(data)}
    assert route.call_count == 1


@pytest.mark.parametrize("limit", [1, 2], ids=lambda n: f"limit{n}")
async def test_image_conversions_run_at_most_concurrency_limit_at_once(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    from app.services import mer_images

    ids = await seed(clean_db, "spirit", [mer_record(f"P{n}") for n in range(6)])
    data = _real(ESF)
    for n in range(6):
        upstream_router.get(pds_url(f"P{n}")).respond(200, content=data)
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()
    hold = threading.Event()  # never set: wait(timeout) keeps a conversion "busy" briefly
    real_encode = mer_images.encode_jpeg

    def counting_encode(*args: Any, **kwargs: Any) -> bytes:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        hold.wait(0.15)
        try:
            return real_encode(*args, **kwargs)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(mer_images, "encode_jpeg", counting_encode)

    async with api(mer_convert_concurrency=limit) as client:
        responses = await asyncio.gather(
            *(client.get(f"{V2}/photos/{ids[f'P{n}']}/image") for n in range(6))
        )

    assert [r.status_code for r in responses] == [200] * 6
    assert state["peak"] == limit


# --- errors --------------------------------------------------------------------------------


def _truncated() -> bytes:
    return _real(ESF)[:-100]


PDS_FAILURES = {
    "http-404": lambda request: httpx.Response(404, text="Not Found"),
    "http-500": lambda request: httpx.Response(500, text="boom"),
    "http-503": lambda request: httpx.Response(503),
    "connect-error": httpx.ConnectError("connection refused"),
    "not-pds3": lambda request: httpx.Response(200, content=b"<html>Not an image</html>"),
    "empty-body": lambda request: httpx.Response(200, content=b""),
    "truncated-file": lambda request: httpx.Response(200, content=_truncated()),
    "jpeg-instead-of-img": lambda request: httpx.Response(200, content=b"\xff\xd8\xff\xe0" * 100),
}


@pytest.mark.parametrize("failure", PDS_FAILURES.values(), ids=PDS_FAILURES.keys())
async def test_image_pds_failure_gives_502_and_is_not_cached(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, failure: Any
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    data = _real(ESF)
    state = {"broken": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["broken"]:
            if isinstance(failure, Exception):
                raise failure
            return failure(request)
        return httpx.Response(200, content=data)

    route = upstream_router.get(pds_url("P1")).mock(side_effect=handler)
    url = f"{V2}/photos/{ids['P1']}/image"

    async with api() as client:
        bad = await client.get(url)
        state["broken"] = False
        good = await client.get(url)

    assert bad.status_code == 502
    assert bad.headers["cache-control"] == "no-store"
    assert bad.headers["content-type"].startswith("application/json")
    body = bad.json()
    assert body["error"]["code"] == "upstream_error"
    assert set(body["error"]) == {"code", "message", "details"}
    for secret in ("planetarydata", "nasa.gov", "/img/data/mer", ".img", "mer2no"):
        assert secret not in bad.text
    assert good.status_code == 200 and good.content == expected_jpeg(data)
    assert route.call_count >= 2


async def test_image_slow_pds_gives_504_and_is_not_cached(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    data = _real(ESF)
    state = {"slow": True}

    async def handler(request: httpx.Request) -> httpx.Response:
        if state["slow"]:
            await asyncio.sleep(10)  # cancelled by the timeout long before
        return httpx.Response(200, content=data)

    upstream_router.get(pds_url("P1")).mock(side_effect=handler)
    url = f"{V2}/photos/{ids['P1']}/image"

    async with api(mer_convert_timeout_seconds=0.3) as client:
        bad = await client.get(url)
        state["slow"] = False
        await asyncio.sleep(0.3)  # let the abandoned conversion finish dying
        good = await client.get(url)

    assert bad.status_code == 504
    assert bad.headers["cache-control"] == "no-store"
    body = bad.json()
    assert body["error"]["code"] == "upstream_timeout"
    for secret in ("planetarydata", "nasa.gov", "/img/data/mer", ".img"):
        assert secret not in bad.text
    assert good.status_code == 200 and good.content == expected_jpeg(data)


async def test_image_slow_conversion_gives_504(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import mer_images

    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=_real(ESF))
    hold = threading.Event()
    real_encode = mer_images.encode_jpeg

    def slow_encode(*args: Any, **kwargs: Any) -> bytes:
        hold.wait(1.5)
        return real_encode(*args, **kwargs)

    monkeypatch.setattr(mer_images, "encode_jpeg", slow_encode)

    async with api(mer_convert_timeout_seconds=0.3) as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"
    assert response.headers["cache-control"] == "no-store"


async def test_image_errors_do_not_leave_a_cached_file(
    api: ApiFactory, clean_db: Factory, upstream_router: respx.MockRouter, tmp_path: Any
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])
    upstream_router.get(pds_url("P1")).respond(200, content=b"garbage")
    cache_dir = tmp_path / "errors-cache"

    async with api(mer_image_cache_dir=cache_dir) as client:
        response = await client.get(f"{V2}/photos/{ids['P1']}/image")

    assert response.status_code == 502
    assert [p for p in cache_dir.rglob("*") if p.is_file()] == []


async def test_image_unknown_photo_is_404(api: ApiFactory) -> None:
    async with api() as client:
        response = await client.get(f"{V2}/photos/999999/image")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "photo_not_found"


# --- other rovers are unchanged ------------------------------------------------------------


@pytest.mark.parametrize("rover", ["curiosity", "perseverance"])
@pytest.mark.parametrize("size", [None, "full", "large", "medium", "small"])
async def test_image_other_rovers_still_redirect(
    api: ApiFactory,
    clean_db: Factory,
    upstream_router: respx.MockRouter,
    rover: str,
    size: str | None,
) -> None:
    record = build_record(
        source_id="X1",
        sol=10,
        camera_code="FHAZ" if rover == "curiosity" else "NAVCAM_LEFT",
        img_full="https://img.example.test/x1-full.png",
        img_small="https://img.example.test/x1-small.jpg",
        img_medium="https://img.example.test/x1-medium.jpg",
        img_large="https://img.example.test/x1-large.jpg",
        source_link="https://img.example.test/x1.img",
    )
    ids = await seed(clean_db, rover, [record])
    expected = {
        "small": record.img_small,
        "medium": record.img_medium,
        "large": record.img_large,
    }.get(size or "", record.img_full)

    async with api() as client:
        response = await client.get(
            f"{V2}/photos/{ids['X1']}/image", params={"size": size} if size else {}
        )

    assert response.status_code == 302
    assert response.headers["location"] == expected
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert upstream_router.calls.call_count == 0


# --- JSON links point at the conversion ------------------------------------------------------


@pytest.mark.parametrize("rover", ["spirit", "opportunity"])
async def test_photo_json_links_full_to_the_conversion_and_small_to_the_preview(
    api: ApiFactory, clean_db: Factory, rover: str
) -> None:
    ids = await seed(clean_db, rover, [mer_record("P1")])
    photo_id = ids["P1"]
    link = f"http://test{V2}/photos/{photo_id}/image?size=full"

    async with api() as client:
        detail = (await client.get(f"{V2}/photos/{photo_id}")).json()
        listing = (await client.get(f"{V2}/rovers/{rover}/photos")).json()["items"][0]
        latest = (await client.get(f"{V2}/rovers/{rover}/photos/latest")).json()["items"][0]

    for item in (detail, listing, latest):
        assert item["img_src"] == link
        assert item["images"] == {
            "small": browse_url("P1"),
            "medium": None,
            "large": None,
            "full": link,
        }
        assert item["title"] is None
        assert item["source"]["link"] == pds_url("P1")


async def test_photo_json_origin_follows_the_request_host(
    api: ApiFactory, clean_db: Factory
) -> None:
    ids = await seed(clean_db, "spirit", [mer_record("P1")])

    async with api() as client:
        body = (
            await client.get(f"{V2}/photos/{ids['P1']}", headers={"Host": "mars.example.org"})
        ).json()

    assert (
        body["images"]["full"] == f"http://mars.example.org{V2}/photos/{ids['P1']}/image?size=full"
    )
    assert body["img_src"] == body["images"]["full"]


@pytest.mark.parametrize("rover", ["curiosity", "perseverance"])
async def test_photo_json_of_other_rovers_keeps_stored_urls(
    api: ApiFactory, clean_db: Factory, rover: str
) -> None:
    record = build_record(
        source_id="X1",
        sol=10,
        camera_code="FHAZ" if rover == "curiosity" else "NAVCAM_LEFT",
        img_full="https://img.example.test/x1-full.png",
        img_small="https://img.example.test/x1-small.jpg",
    )
    ids = await seed(clean_db, rover, [record])

    async with api() as client:
        body = (await client.get(f"{V2}/photos/{ids['X1']}")).json()

    assert body["img_src"] == "https://img.example.test/x1-full.png"
    assert body["images"] == {
        "small": "https://img.example.test/x1-small.jpg",
        "medium": None,
        "large": None,
        "full": "https://img.example.test/x1-full.png",
    }
