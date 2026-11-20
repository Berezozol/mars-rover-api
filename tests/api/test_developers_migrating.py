"""/developers "Migrating" section and Host handling of /developers and /llms.txt (stage 18A).

- The page has a "Migrating" section with the three steps: change the host; no key needed
  (`api_key` is ignored); both `/api/v1` and `/mars-photos/api/v1` work.
- A malformed or hostile `Host` never reaches /developers or /llms.txt; a sane address
  (the server's own) is used instead and the response is still 200.
"""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from tests.api._raw import raw_get

pytestmark = [pytest.mark.api, pytest.mark.usefixtures("migrated_db")]

HOST = "mars-api.example.test"


@pytest.fixture
async def site(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"https://{HOST}") as client:
        yield client


@pytest.fixture
async def page(site: httpx.AsyncClient) -> str:
    return (await site.get("/developers")).text


def _migrating_section(html: str) -> str:
    match = re.search(r"migrating", html, re.I)
    assert match, "no 'Migrating' section on /developers"
    return html[match.start() :]


def test_developers_has_a_migrating_heading(page: str) -> None:
    assert re.search(r"<h[1-6][^>]*>[^<]*migrating[^<]*</h[1-6]>", page, re.I)


def test_migrating_step_one_change_the_host(page: str) -> None:
    section = _migrating_section(page).lower()

    assert "host" in section
    assert f"{HOST}" in _migrating_section(page)


def test_migrating_step_two_no_key_needed(page: str) -> None:
    section = _migrating_section(page)

    assert "api_key" in section
    assert re.search(r"(not needed|no key|ignored|not required|without a key)", section, re.I)


def test_migrating_step_three_both_v1_paths_work(page: str) -> None:
    section = _migrating_section(page)

    assert "/api/v1" in section
    assert "/mars-photos/api/v1" in section


def test_migrating_has_three_steps(page: str) -> None:
    section = _migrating_section(page)

    assert len(re.findall(r"<li\b", section, re.I)) >= 3


def test_migrating_keeps_the_differences_table(page: str) -> None:
    assert "<table" in _migrating_section(page).lower()


# --- hostile / malformed Host --------------------------------------------------------------

# (raw Host header, regex that must not match the output)
HOSTILE = [
    pytest.param(b'evil"><x', r'evil|<x\b|"><', id="quote-and-tag"),
    pytest.param(b"a b", r"//a\b|a b/", id="space"),
    pytest.param(b"example.com:99999", r"example\.com|99999", id="port-out-of-range"),
    pytest.param(b"x@y", r"x@y|//y\b", id="userinfo"),
    pytest.param(b"evil.test/path?q=1", r"evil\.test", id="path-in-host"),
    pytest.param(b"evil.test\\x", r"evil\.test", id="backslash"),
    pytest.param(b"<script>alert(1)</script>", r"<script|alert\(1\)", id="script"),
    pytest.param(b"", r"//:", id="empty"),
]

ORIGIN = re.compile(r"https?://[A-Za-z0-9.\-]+(?::\d{1,5})?(?:\[[0-9a-f:]+\])?/api/v2/", re.I)


@pytest.mark.parametrize(("host", "forbidden"), HOSTILE)
@pytest.mark.parametrize("path", ["/developers", "/llms.txt"])
async def test_hostile_host_never_appears_and_a_sane_address_is_used(
    app: FastAPI, path: str, host: bytes, forbidden: str
) -> None:
    response = await raw_get(app, path, [(b"host", host)])

    assert response.status == 200, response.body
    assert not re.search(forbidden, response.body, re.I), f"{host!r} leaked into {path}"
    assert ORIGIN.search(response.body), f"{path}: no well-formed base URL for {host!r}"


@pytest.mark.parametrize("path", ["/developers", "/llms.txt"])
async def test_missing_host_header_falls_back_to_a_sane_address(app: FastAPI, path: str) -> None:
    response = await raw_get(app, path, [])

    assert response.status == 200
    assert ORIGIN.search(response.body)
    assert "http://localhost/api/v2/" in response.body
    assert "testserver" not in response.body


@pytest.mark.parametrize("path", ["/developers", "/llms.txt"])
async def test_valid_host_with_port_is_used(app: FastAPI, path: str) -> None:
    response = await raw_get(app, path, [(b"host", b"good.example.test:8443")])

    assert response.status == 200
    assert "good.example.test:8443/api/v2/" in response.body


async def test_hostile_host_does_not_change_cache_headers(app: FastAPI) -> None:
    response = await raw_get(app, "/llms.txt", [(b"host", b'evil"><x')])

    assert response.headers.get("cache-control") == "public, max-age=3600"
    assert response.headers["content-type"].lower().startswith("text/plain")
