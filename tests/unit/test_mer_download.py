"""``UpstreamClient.download`` and ``python -m app.cli mer-download`` (stage 8), via respx.

Contract (docs/proposals/stage-8-mer-import.md, section 4, corrected by DATA-SOURCES.md):
- files ``<MER_PDS_BASE_URL><volume>/index/edrindex.{lbl,tab}``, 5 volumes per rover
  (``no ho po mo do``; ``mw``/``om`` are not EDR), both rovers by default;
- body streamed to ``<file>.part``, renamed when complete; a body that does not match its
  Content-Length is an error and leaves no file behind;
- a complete file is skipped (no request); completeness = row count == the label's ROWS
  (``ROWS x ROW_BYTES`` is wrong for these files); ``--force`` downloads again;
- all requests through the shared client: User-Agent and UPSTREAM_MAX_RPS across files;
- exit 0 everything present and valid, 1 a download or check failed.
"""

from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import httpx
import pytest
import respx

from app import cli
from app.core.config import Settings, get_settings
from app.sources import http
from app.sources.http import IncompleteResponse, UpstreamClient, UpstreamError
from tests.unit._mer import MER_FIXTURES, VOLUMES, copy_cache
from tests.unit._upstream import FakeClock, make_settings

pytestmark = pytest.mark.unit

BASE = "https://pds.example.test/img/data/mer/"
UA = "mars-rover-api-tests/mer"
FILES = ("edrindex.lbl", "edrindex.tab")


def url(volume: str, name: str) -> str:
    return f"{BASE}{volume}/index/{name}"


def listing(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def fixture_bytes(volume: str, name: str) -> bytes:
    return (MER_FIXTURES / volume / "index" / name).read_bytes()


# --- UpstreamClient.download ---------------------------------------------------------------


async def test_download_writes_body_and_returns_size(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    body = fixture_bytes("mer2no_0xxx", "edrindex.tab")
    upstream_router.get(url("mer2no_0xxx", "edrindex.tab")).respond(200, content=body)
    dest = tmp_path / "sub" / "edrindex.tab"

    async with UpstreamClient(make_settings(upstream_user_agent=UA)) as client:
        size = await client.download(url("mer2no_0xxx", "edrindex.tab"), dest)

    assert size == len(body)
    assert dest.read_bytes() == body  # CRLF and tabs byte for byte
    assert not (tmp_path / "sub" / "edrindex.tab.part").exists()
    assert upstream_router.calls.last.request.headers["user-agent"] == UA


async def test_download_streams_into_part_file_then_renames(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    dest = tmp_path / "edrindex.tab"
    seen: list[tuple[bool, bool]] = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[override]
            yield b"first\r\n"
            # Mid-stream: data goes to the .part file, the final name does not exist yet.
            seen.append((dest.exists(), dest.with_name("edrindex.tab.part").exists()))
            yield b"second\r\n"

    upstream_router.get(url("mer2no_0xxx", "edrindex.tab")).respond(200, stream=Body())

    async with UpstreamClient(make_settings()) as client:
        await client.download(url("mer2no_0xxx", "edrindex.tab"), dest)

    assert seen == [(False, True)]
    assert dest.read_bytes() == b"first\r\nsecond\r\n"


async def test_download_shorter_than_content_length_fails_without_file(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    upstream_router.get(url("mer2no_0xxx", "edrindex.tab")).respond(
        200, content=b"truncated\r\n", headers={"Content-Length": "4096"}
    )
    dest = tmp_path / "edrindex.tab"

    async with UpstreamClient(make_settings(upstream_max_retries=0)) as client:
        with pytest.raises(UpstreamError) as info:
            await client.download(url("mer2no_0xxx", "edrindex.tab"), dest)

    assert (
        isinstance(info.value, IncompleteResponse)
        or "Content-Length" in str(info.value)
        or ("4096" in str(info.value))
    )
    assert not dest.exists()
    assert listing(tmp_path) == []


async def test_download_retries_incomplete_response_once_then_succeeds(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    body = fixture_bytes("mer2no_0xxx", "edrindex.tab")
    route = upstream_router.get(url("mer2no_0xxx", "edrindex.tab"))
    route.side_effect = [
        httpx.Response(200, content=body[:100], headers={"Content-Length": str(len(body))}),
        httpx.Response(200, content=body),
    ]
    dest = tmp_path / "edrindex.tab"
    clock = FakeClock()

    async with UpstreamClient(make_settings(), sleep=clock.sleep, clock=clock) as client:
        size = await client.download(url("mer2no_0xxx", "edrindex.tab"), dest)

    assert route.call_count == 2
    assert size == len(body)
    assert dest.read_bytes() == body
    assert listing(tmp_path) == ["edrindex.tab"]  # no .part left behind


async def test_download_incomplete_twice_fails_without_file(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    route = upstream_router.get(url("mer2no_0xxx", "edrindex.tab"))
    route.respond(200, content=b"truncated\r\n", headers={"Content-Length": "4096"})
    clock = FakeClock()

    async with UpstreamClient(make_settings(), sleep=clock.sleep, clock=clock) as client:
        with pytest.raises(UpstreamError):
            await client.download(url("mer2no_0xxx", "edrindex.tab"), tmp_path / "edrindex.tab")

    assert route.call_count >= 2
    assert listing(tmp_path) == []


async def test_download_http_error_leaves_no_file(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    upstream_router.get(url("mer2no_0xxx", "edrindex.tab")).respond(404)
    dest = tmp_path / "edrindex.tab"

    async with UpstreamClient(make_settings(upstream_max_retries=0)) as client:
        with pytest.raises(UpstreamError):
            await client.download(url("mer2no_0xxx", "edrindex.tab"), dest)

    assert listing(tmp_path) == []


async def test_download_retries_5xx_then_succeeds(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    route = upstream_router.get(url("mer2no_0xxx", "edrindex.lbl"))
    route.side_effect = [httpx.Response(503), httpx.Response(200, content=b"PDS_VERSION_ID\n")]
    clock = FakeClock()

    async with UpstreamClient(make_settings(), sleep=clock.sleep, clock=clock) as client:
        await client.download(url("mer2no_0xxx", "edrindex.lbl"), tmp_path / "edrindex.lbl")

    assert route.call_count == 2
    assert (tmp_path / "edrindex.lbl").read_bytes() == b"PDS_VERSION_ID\n"


async def test_download_respects_rate_limit_between_files(
    tmp_path: Path, upstream_router: respx.MockRouter
) -> None:
    upstream_router.get(url("mer2no_0xxx", "edrindex.lbl")).respond(200, content=b"a")
    upstream_router.get(url("mer2no_0xxx", "edrindex.tab")).respond(200, content=b"b")
    clock = FakeClock()

    settings = make_settings(upstream_max_rps=1)
    async with UpstreamClient(settings, sleep=clock.sleep, clock=clock) as client:
        await client.download(url("mer2no_0xxx", "edrindex.lbl"), tmp_path / "a")
        await client.download(url("mer2no_0xxx", "edrindex.tab"), tmp_path / "b")

    assert clock.sleeps == [pytest.approx(1.0)]


# --- mer-download CLI ----------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "mer-cache"


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, settings: Settings, cache: Path) -> Iterator[None]:
    for key, value in {
        "DATABASE_URL": settings.database_url,
        "MER_PDS_BASE_URL": BASE,
        "MER_CACHE_DIR": str(cache),
        "UPSTREAM_MAX_RPS": "0",
        "UPSTREAM_MAX_RETRIES": "0",
        "UPSTREAM_RETRY_BASE_SECONDS": "0",
        "UPSTREAM_USER_AGENT": UA,
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def serve_fixtures(router: respx.MockRouter, rovers: tuple[str, ...]) -> dict[str, respx.Route]:
    routes = {}
    for rover in rovers:
        for volume in VOLUMES[rover]:
            for name in FILES:
                body = fixture_bytes(volume, name)
                routes[f"{volume}/{name}"] = router.get(url(volume, name)).respond(
                    200, content=body
                )
    return routes


def assert_cache_equals_fixtures(cache: Path, rovers: tuple[str, ...]) -> None:
    for rover in rovers:
        for volume in VOLUMES[rover]:
            for name in FILES:
                path = cache / volume / "index" / name
                assert path.read_bytes() == fixture_bytes(volume, name), path
    assert not list(cache.rglob("*.part"))


def run(argv: list[str]) -> int:
    try:
        return cli.main(argv)
    except SystemExit as exc:  # pragma: no cover - main returns codes
        return exc.code if isinstance(exc.code, int) else 1


def test_mer_download_both_rovers_by_default(
    cache: Path, upstream_router: respx.MockRouter
) -> None:
    routes = serve_fixtures(upstream_router, ("spirit", "opportunity"))

    assert run(["mer-download"]) == 0

    assert {key: route.call_count for key, route in routes.items()} == dict.fromkeys(routes, 1)
    assert_cache_equals_fixtures(cache, ("spirit", "opportunity"))
    for call in upstream_router.calls:
        assert call.request.headers["user-agent"] == UA


def test_mer_download_one_rover_only_its_volumes(
    cache: Path, upstream_router: respx.MockRouter
) -> None:
    routes = serve_fixtures(upstream_router, ("spirit",))

    assert run(["mer-download", "--rover", "SPIRIT"]) == 0

    assert all(route.call_count == 1 for route in routes.values())
    assert len(upstream_router.calls) == 10
    assert sorted(p.name for p in cache.iterdir()) == sorted(VOLUMES["spirit"])


def test_mer_download_skips_complete_files(cache: Path, upstream_router: respx.MockRouter) -> None:
    copy_cache(cache, ("spirit",))
    serve_fixtures(upstream_router, ("spirit",))

    assert run(["mer-download", "--rover", "spirit"]) == 0

    assert len(upstream_router.calls) == 0
    assert_cache_equals_fixtures(cache, ("spirit",))


def test_mer_download_force_downloads_again(cache: Path, upstream_router: respx.MockRouter) -> None:
    copy_cache(cache, ("spirit",))
    routes = serve_fixtures(upstream_router, ("spirit",))

    assert run(["mer-download", "--rover", "spirit", "--force"]) == 0

    assert all(route.call_count == 1 for route in routes.values())


def test_mer_download_redownloads_table_with_wrong_row_count(
    cache: Path, upstream_router: respx.MockRouter
) -> None:
    """An interrupted earlier run left a short table: it is fetched again, the rest skipped."""
    copy_cache(cache, ("spirit",))
    short = cache / "mer2po_0xxx" / "index" / "edrindex.tab"
    short.write_bytes(b"\r\n".join(short.read_bytes().split(b"\r\n")[:5]) + b"\r\n")
    routes = serve_fixtures(upstream_router, ("spirit",))

    assert run(["mer-download", "--rover", "spirit"]) == 0

    called = {key for key, route in routes.items() if route.called}
    assert called == {"mer2po_0xxx/edrindex.tab"}
    assert_cache_equals_fixtures(cache, ("spirit",))


def test_mer_download_row_count_differs_from_label_fails(
    cache: Path, upstream_router: respx.MockRouter
) -> None:
    serve_fixtures(upstream_router, ("spirit",))
    body = fixture_bytes("mer2no_0xxx", "edrindex.tab")
    one_less = b"\r\n".join(body.split(b"\r\n")[1:])
    upstream_router.get(url("mer2no_0xxx", "edrindex.tab")).respond(200, content=one_less)

    assert run(["mer-download", "--rover", "spirit"]) == 1

    # The bad table is not accepted as complete: the next run asks for it again.
    upstream_router.get(url("mer2no_0xxx", "edrindex.tab")).respond(200, content=body)
    assert run(["mer-download", "--rover", "spirit"]) == 0
    assert_cache_equals_fixtures(cache, ("spirit",))


def test_mer_download_content_length_mismatch_fails(
    cache: Path, upstream_router: respx.MockRouter
) -> None:
    serve_fixtures(upstream_router, ("spirit",))
    body = fixture_bytes("mer2ho_0xxx", "edrindex.tab")
    upstream_router.get(url("mer2ho_0xxx", "edrindex.tab")).respond(
        200, content=body[:-100], headers={"Content-Length": str(len(body))}
    )

    assert run(["mer-download", "--rover", "spirit"]) == 1

    assert not (cache / "mer2ho_0xxx" / "index" / "edrindex.tab").exists()
    assert not list(cache.rglob("*.part"))


def test_mer_download_http_404_fails(cache: Path, upstream_router: respx.MockRouter) -> None:
    serve_fixtures(upstream_router, ("spirit",))
    upstream_router.get(url("mer2mo_0xxx", "edrindex.lbl")).respond(404)

    assert run(["mer-download", "--rover", "spirit"]) == 1
    assert not (cache / "mer2mo_0xxx" / "index" / "edrindex.lbl").exists()


@pytest.mark.parametrize("rover", ["curiosity", "perseverance", "mars"])
def test_mer_download_non_mer_rover_is_usage_error(
    rover: str, upstream_router: respx.MockRouter
) -> None:
    assert run(["mer-download", "--rover", rover]) == 2
    assert len(upstream_router.calls) == 0


def test_mer_download_requests_are_rate_limited_across_files(
    monkeypatch: pytest.MonkeyPatch, upstream_router: respx.MockRouter
) -> None:
    """One shared client for the whole command: UPSTREAM_MAX_RPS spaces all 10 requests.

    Deterministic: every RateLimiter gets a fake clock/sleep, and the test checks the
    times the limiter granted (``_last`` after ``acquire``) and that each request was sent
    only after its own grant. Wall-clock gaps between requests are not a valid measure:
    a grant is scheduled from the previous grant, so a late wake-up of request i (Windows
    timers overshoot by up to ~20 ms) shortens the measured gap i -> i+1 while the
    schedule is still exact.
    """
    monkeypatch.setenv("UPSTREAM_MAX_RPS", "20")
    get_settings.cache_clear()
    clock = FakeClock()
    limiters: list[http.RateLimiter] = []
    grants: list[float] = []
    original_init = http.RateLimiter.__init__
    original_acquire = http.RateLimiter.acquire

    def init(self: http.RateLimiter, max_rps: float, **_: object) -> None:
        original_init(self, max_rps, clock=clock, sleep=clock.sleep)
        limiters.append(self)

    async def acquire(self: http.RateLimiter) -> None:
        await original_acquire(self)
        grants.append(clock.now)

    monkeypatch.setattr(http.RateLimiter, "__init__", init)
    monkeypatch.setattr(http.RateLimiter, "acquire", acquire)
    sent: list[tuple[int, float]] = []  # (grants so far, fake time) at each request
    for volume in VOLUMES["spirit"]:
        for name in FILES:
            body = fixture_bytes(volume, name)

            def respond(request: httpx.Request, body: bytes = body) -> httpx.Response:
                sent.append((len(grants), clock.now))
                return httpx.Response(200, content=body)

            upstream_router.get(url(volume, name)).mock(side_effect=respond)

    assert run(["mer-download", "--rover", "spirit"]) == 0

    assert len(limiters) == 1  # one client, one limiter for all files
    assert len(grants) == len(sent) == 10
    assert [b - a for a, b in pairwise(grants)] == [pytest.approx(0.05)] * 9
    # Request n goes out after grant n, at the granted time (no request skips the limiter).
    assert sent == [(n + 1, pytest.approx(grants[n])) for n in range(10)]
