"""Public landing page GET / (docs/API.md "GET /"; stage 9).

HTML in English: what the service is and that it is unofficial, how to migrate (change the
host), differences from the old API, v1 and v2 examples built from the request host, a link
to /docs, image credit. No NASA/JPL logos (no external images or scripts).
``Cache-Control: public, max-age=3600``; no rate limit (tests/api/test_rate_limit.py); not in
the OpenAPI schema. The page needs no database.
"""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.api

HOST = "mars-api.example.test"
BASE_URL = f"https://{HOST}"


@pytest.fixture
async def site(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
async def page(site: httpx.AsyncClient) -> httpx.Response:
    return await site.get("/")


def test_landing_returns_200_html(page: httpx.Response) -> None:
    assert page.status_code == 200, page.text
    assert page.headers["content-type"].startswith("text/html")
    assert "<html" in page.text.lower()


def test_landing_cache_control_is_public_one_hour(page: httpx.Response) -> None:
    assert page.headers.get("cache-control") == "public, max-age=3600"


def test_landing_says_unofficial(page: httpx.Response) -> None:
    assert "unofficial" in page.text.lower()


def test_landing_credits_nasa_jpl_caltech(page: httpx.Response) -> None:
    assert "NASA/JPL-Caltech" in page.text


def test_landing_does_not_claim_endorsement(page: httpx.Response) -> None:
    text = page.text.lower()
    for phrase in ("endorsed by nasa", "official nasa", "approved by nasa", "nasa-approved"):
        index = text.find(phrase)
        if index != -1:
            # Only allowed as a negation, e.g. "not endorsed by NASA".
            assert re.search(r"\bnot\b[^.]{0,40}$", text[max(0, index - 60) : index]), phrase


def test_landing_examples_use_request_host_for_v1_and_v2(page: httpx.Response) -> None:
    text = page.text
    assert f"{HOST}/api/v1/" in text
    assert f"{HOST}/api/v2/" in text
    assert "mars-photos/api/v1" in text, "the old path prefix is shown for migrating clients"


async def test_landing_examples_follow_another_host(app: FastAPI) -> None:
    other = "another-host.example.test"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{other}") as client:
        text = (await client.get("/")).text

    assert f"{other}/api/v1/" in text
    assert f"{other}/api/v2/" in text
    assert HOST not in text


def test_landing_links_to_docs(page: httpx.Response) -> None:
    assert re.search(r"""href=["'](https?://[^"']*)?/docs["'/]""", page.text), "link to /docs"


def test_landing_has_no_external_images_or_scripts(page: httpx.Response) -> None:
    html = page.text
    external = r"""(?:https?:)?//"""
    assert not re.search(rf"""<img\b[^>]*\bsrc\s*=\s*["']?{external}""", html, re.I)
    assert not re.search(rf"""<script\b[^>]*\bsrc\s*=\s*["']?{external}""", html, re.I)
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        if re.search(r"""\brel\s*=\s*["']?[^"'>]*(icon|stylesheet)""", tag, re.I):
            assert not re.search(rf"""\bhref\s*=\s*["']?{external}""", tag, re.I), tag
    assert "logo" not in " ".join(re.findall(r"<img\b[^>]*>", html, re.I)).lower()


async def test_landing_head_returns_headers_without_body(site: httpx.AsyncClient) -> None:
    response = await site.head("/")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers.get("cache-control") == "public, max-age=3600"


async def test_landing_is_not_in_openapi(site: httpx.AsyncClient) -> None:
    paths = (await site.get("/openapi.json")).json()["paths"]

    assert "/" not in paths


async def raw_get(app: FastAPI, path: str, headers: list[tuple[bytes, bytes]]) -> tuple[int, str]:
    """Call the ASGI app directly with raw headers (httpx would not pass a hostile Host)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("203.0.113.5", 50000),
        "server": ("testserver", 80),
    }
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = next(int(m["status"]) for m in messages if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"")
        for m in messages
        if m["type"] == "http.response.body"  # type: ignore[misc]
    )
    return status, body.decode("utf-8")


@pytest.mark.parametrize(
    "host",
    [b'evil.test"><script>alert(1)</script>', b"evil.test'><script>alert(1)</script>"],
    ids=["double-quote", "single-quote"],
)
async def test_landing_hostile_host_header_is_not_reflected_raw(app: FastAPI, host: bytes) -> None:
    """A hostile Host is either escaped or not used at all (falls back to a safe origin)."""
    status, text = await raw_get(app, "/", [(b"host", host)])

    assert status == 200, text
    assert "<script>alert(1)" not in text
    assert "<script" not in text.lower()
    if "evil.test" in text:
        assert "&lt;script&gt;" in text


async def test_landing_raw_host_header_is_used_in_examples(app: FastAPI) -> None:
    status, text = await raw_get(app, "/", [(b"host", b"good.example.test")])

    assert status == 200
    assert "good.example.test/api/v2/" in text


def test_render_home_escapes_origin() -> None:
    from app.api.home import render_home

    page = render_home('https://evil.test"><script>alert(1)</script>')

    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&quot;" in page
