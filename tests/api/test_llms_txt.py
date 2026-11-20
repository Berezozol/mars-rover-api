"""GET /llms.txt (docs/API.md "GET /llms.txt", stage 18A).

Markdown (llmstxt.org) as `text/plain; charset=utf-8`: what the service is, unofficial, base
URL from the request host, main v1/v2 endpoints, links to /openapi.json, /docs, /developers,
image credit. `Cache-Control: public, max-age=3600`, ETag (304), HEAD, no rate limit, not
intercepted by the web interface even when a build exists.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

HOST = "mars-api.example.test"
BASE_URL = f"https://{HOST}"
INDEX_MARKER = "SOL-INDEX-FAKE-BUILD-MARKER"


@pytest.fixture
async def site(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
async def llms(site: httpx.AsyncClient) -> httpx.Response:
    return await site.get("/llms.txt")


def test_llms_returns_200_plain_text_utf8(llms: httpx.Response) -> None:
    assert llms.status_code == 200, llms.text
    assert llms.headers["content-type"].replace(" ", "").lower() == "text/plain;charset=utf-8"


def test_llms_is_markdown_with_a_title_and_says_unofficial(llms: httpx.Response) -> None:
    assert llms.text.lstrip().startswith("# "), "llmstxt.org: the file starts with an H1"
    assert "unofficial" in llms.text.lower()


def test_llms_uses_the_request_host_as_base_url(llms: httpx.Response) -> None:
    assert f"{BASE_URL}/api/v2/" in llms.text
    assert f"{BASE_URL}/api/v1/" in llms.text


async def test_llms_follows_another_host(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://other.example.test") as c:
        text = (await c.get("/llms.txt")).text

    assert "http://other.example.test/api/v2/" in text
    assert HOST not in text


def test_llms_links_to_openapi_docs_and_developers(llms: httpx.Response) -> None:
    for path in ("/openapi.json", "/docs", "/developers"):
        assert f"{BASE_URL}{path}" in llms.text, path


def test_llms_mentions_both_v1_prefixes(llms: httpx.Response) -> None:
    assert "/api/v1" in llms.text
    assert "/mars-photos/api/v1" in llms.text


def test_llms_lists_main_endpoints_of_v1_and_v2(llms: httpx.Response) -> None:
    for fragment in ("/api/v1/rovers", "/api/v2/rovers", "photos", "manifest"):
        assert fragment in llms.text, fragment


def test_llms_keeps_image_credit_and_no_endorsement(llms: httpx.Response) -> None:
    assert "NASA/JPL-Caltech" in llms.text
    lowered = llms.text.lower()
    for phrase in ("endorsed by nasa", "official nasa", "approved by nasa"):
        assert phrase not in lowered.replace("not " + phrase, "")


def test_llms_mentions_that_api_key_is_not_needed(llms: httpx.Response) -> None:
    assert "api_key" in llms.text


def test_llms_cache_control_is_public_one_hour(llms: httpx.Response) -> None:
    assert llms.headers.get("cache-control") == "public, max-age=3600"


def test_llms_has_an_etag(llms: httpx.Response) -> None:
    assert llms.headers.get("etag")


async def test_llms_if_none_match_returns_304_without_body(site: httpx.AsyncClient) -> None:
    first = await site.get("/llms.txt")
    etag = first.headers["etag"]

    again = await site.get("/llms.txt", headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers.get("etag") == etag


async def test_llms_stale_etag_returns_full_body(site: httpx.AsyncClient) -> None:
    response = await site.get("/llms.txt", headers={"If-None-Match": '"stale-etag"'})

    assert response.status_code == 200
    assert response.text.startswith("#") or response.text.lstrip().startswith("# ")


async def test_llms_etag_is_stable_between_requests(site: httpx.AsyncClient) -> None:
    first = await site.get("/llms.txt")
    second = await site.get("/llms.txt")

    assert first.headers["etag"] == second.headers["etag"]
    assert first.text == second.text


async def test_llms_head_returns_headers_without_body(site: httpx.AsyncClient) -> None:
    get = await site.get("/llms.txt")
    head = await site.head("/llms.txt")

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"] == get.headers["content-type"]
    assert head.headers.get("cache-control") == "public, max-age=3600"
    assert head.headers.get("etag") == get.headers["etag"]


async def test_llms_is_not_in_openapi(site: httpx.AsyncClient) -> None:
    paths = (await site.get("/openapi.json")).json()["paths"]

    assert "/llms.txt" not in paths


async def test_llms_is_not_rate_limited(settings: Settings, tmp_path: Path) -> None:
    limited = settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_per_minute": 3,
            "rate_limit_per_hour": 1000,
            "mer_image_cache_dir": tmp_path / "mer-images",
        }
    )
    application = create_app(limited)
    try:
        transport = httpx.ASGITransport(app=application, client=("203.0.113.88", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(10):
                assert (await client.get("/llms.txt")).status_code == 200
                assert (await client.head("/llms.txt")).status_code == 200
            # The API budget is untouched.
            api = [(await client.get("/api/v2/rovers")).status_code for _ in range(4)]
            assert api == [200, 200, 200, 429]
            # And llms.txt still works while the API is limited.
            assert (await client.get("/llms.txt")).status_code == 200
    finally:
        await application.state.engine.dispose()


# --- not served by the web interface --------------------------------------------------------


@pytest.fixture
async def ui(settings: Settings, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """An app with a (fake) built web interface, as in tests/api/test_frontend.py."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        f"<!doctype html><html><body><!-- {INDEX_MARKER} --></body></html>", encoding="utf-8"
    )
    application = create_app(
        settings.model_copy(
            update={"frontend_dist_dir": dist, "mer_image_cache_dir": tmp_path / "mer-images"}
        )
    )
    try:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            yield client
    finally:
        await application.state.engine.dispose()


async def test_llms_is_not_intercepted_by_the_web_interface(ui: httpx.AsyncClient) -> None:
    sanity = await ui.get("/some/ui/page")
    assert INDEX_MARKER in sanity.text, "the fake build must be active"

    response = await ui.get("/llms.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert INDEX_MARKER not in response.text
    assert "<html" not in response.text.lower()
    assert f"{BASE_URL}/api/v2/" in response.text
    assert response.headers.get("cache-control") == "public, max-age=3600"


async def test_llms_head_with_the_web_interface_is_not_the_index(ui: httpx.AsyncClient) -> None:
    response = await ui.head("/llms.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers.get("cache-control") == "public, max-age=3600"


async def test_developers_is_still_not_intercepted_with_the_web_interface(
    ui: httpx.AsyncClient,
) -> None:
    response = await ui.get("/developers")

    assert response.status_code == 200
    assert INDEX_MARKER not in response.text
