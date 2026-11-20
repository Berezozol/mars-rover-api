"""Origin used in links and examples (stage 18A).

- PUBLIC_BASE_URL set and valid: it is always the origin; the Host header is ignored, even a
  valid foreign host (/developers, /llms.txt, v2 JSON, RSS).
- Unset (or invalid) and the Host is invalid: the fallback is exactly `http://localhost`,
  never the socket or container address.
- /llms.txt and /developers send `X-Content-Type-Options: nosniff`.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from tests.api._raw import raw_get

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

PUBLIC = "https://public.example.test"
FOREIGN = "evil.example"
PAGES = ["/developers", "/llms.txt"]


@pytest.fixture
async def configured_app(settings: Settings, tmp_path: Path) -> AsyncIterator[FastAPI]:
    application = create_app(
        settings.model_copy(
            update={"public_base_url": PUBLIC, "mer_image_cache_dir": tmp_path / "mer-images"}
        )
    )
    try:
        yield application
    finally:
        await application.state.engine.dispose()


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("host", [FOREIGN, "evil.example:8443", "good.example.test"])
async def test_public_base_url_wins_over_a_valid_foreign_host(
    configured_app: FastAPI, path: str, host: str
) -> None:
    response = await raw_get(configured_app, path, [(b"host", host.encode())])

    assert response.status == 200
    assert f"{PUBLIC}/api/v2/" in response.body
    assert host.split(":")[0] not in response.body


@pytest.mark.parametrize("path", PAGES)
async def test_public_base_url_is_used_when_host_is_hostile_or_missing(
    configured_app: FastAPI, path: str
) -> None:
    for headers in ([(b"host", b'evil"><x')], []):
        response = await raw_get(configured_app, path, headers)

        assert f"{PUBLIC}/api/v2/" in response.body
        assert "localhost" not in response.body


async def test_public_base_url_wins_in_v2_json_and_rss(configured_app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=configured_app)
    async with httpx.AsyncClient(transport=transport, base_url=f"https://{FOREIGN}") as client:
        for path in (
            "/api/v2/rovers",
            "/api/v2/rovers/curiosity/photos?sol=1",
            "/api/v2/rovers/curiosity/feed.xml",
            "/api/v2/rovers/perseverance/feed.xml?camera=NAVCAM_LEFT",
        ):
            response = await client.get(path)

            assert response.status_code == 200, path
            assert FOREIGN not in response.text, path
        feed = await client.get("/api/v2/rovers/curiosity/feed.xml")
        assert f"{PUBLIC}/api/v2/rovers/curiosity/feed.xml" in feed.text


async def test_public_base_url_with_a_path_is_reduced_to_the_origin(
    settings: Settings, tmp_path: Path
) -> None:
    application = create_app(
        settings.model_copy(
            update={
                "public_base_url": PUBLIC + "/some/path/",
                "mer_image_cache_dir": tmp_path / "mer-images",
            }
        )
    )
    try:
        body = (await raw_get(application, "/llms.txt", [(b"host", b"x.test")])).body
    finally:
        await application.state.engine.dispose()

    assert f"{PUBLIC}/api/v2/" in body
    assert "/some/path" not in body


@pytest.mark.parametrize("bad", ["not a url", "ftp://x.test", "https://", "javascript:alert(1)"])
async def test_invalid_public_base_url_is_ignored(
    settings: Settings, tmp_path: Path, bad: str
) -> None:
    application = create_app(
        settings.model_copy(
            update={"public_base_url": bad, "mer_image_cache_dir": tmp_path / "mer-images"}
        )
    )
    try:
        body = (await raw_get(application, "/llms.txt", [(b"host", b"good.example.test")])).body
    finally:
        await application.state.engine.dispose()

    assert "http://good.example.test/api/v2/" in body
    assert "javascript" not in body


# --- no PUBLIC_BASE_URL: localhost fallback --------------------------------------------------

BAD_HOSTS = [
    pytest.param(b'evil"><x', id="quote-and-tag"),
    pytest.param(b"a b", id="space"),
    pytest.param(b"example.com:99999", id="port-out-of-range"),
    pytest.param(b"x@y", id="userinfo"),
    pytest.param(b"", id="empty"),
]


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("host", BAD_HOSTS)
async def test_invalid_host_falls_back_to_http_localhost(
    app: FastAPI, path: str, host: bytes
) -> None:
    response = await raw_get(app, path, [(b"host", host)])

    assert response.status == 200
    assert "http://localhost/api/v2/" in response.body
    assert "testserver" not in response.body, "the socket address must not be revealed"


@pytest.mark.parametrize("path", PAGES)
async def test_missing_host_falls_back_to_http_localhost(app: FastAPI, path: str) -> None:
    response = await raw_get(app, path, [])

    assert "http://localhost/api/v2/" in response.body
    assert "testserver" not in response.body


async def test_invalid_host_in_rss_falls_back_to_localhost(app: FastAPI) -> None:
    response = await raw_get(app, "/api/v2/rovers/curiosity/feed.xml", [(b"host", b'evil"><x')])

    assert response.status == 200
    assert "http://localhost" in response.body
    assert "evil" not in response.body
    assert "testserver" not in response.body


# --- nosniff ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
async def test_pages_send_nosniff(app: FastAPI, path: str) -> None:
    get = await raw_get(app, path, [(b"host", b"good.example.test")])
    head = await raw_get(app, path, [(b"host", b"good.example.test")], method="HEAD")

    assert get.headers.get("x-content-type-options") == "nosniff"
    assert head.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.parametrize("path", PAGES)
async def test_nosniff_is_kept_with_a_hostile_host(app: FastAPI, path: str) -> None:
    response = await raw_get(app, path, [(b"host", b'evil"><x')])

    assert response.headers.get("x-content-type-options") == "nosniff"
