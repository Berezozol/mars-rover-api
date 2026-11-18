"""Database outage: 503 db_unavailable in v2, 503 "Database unavailable" in v1 (docs/API.md).

A real engine points at 127.0.0.1:1 (refuses connections; nothing leaves localhost). A fake
session factory covers the other failure shapes, and a programming error must stay a 500.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.api

V2_URLS = ["/api/v2/rovers", "/api/v2/rovers/curiosity/photos?sol=1"]
V1_URLS = ["/api/v1/rovers", "/api/v1/rovers/curiosity/photos?sol=1"]


def _client(app: FastAPI) -> httpx.AsyncClient:
    # Unhandled errors must come back as a 500 response, not be re-raised into the test.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def down_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """App whose database is a closed localhost port."""
    url = make_url(settings.database_url).set(host="127.0.0.1", port=1)
    broken = settings.model_copy(
        update={
            "database_url": url.render_as_string(hide_password=False),
            "db_connect_timeout_seconds": 2.0,
        }
    )
    app = create_app(broken)
    try:
        async with _client(app) as client:
            yield client
    finally:
        await app.state.engine.dispose()


class _FailingSession:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __getattr__(self, name: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise self._error

        return fail


class _FailingFactory:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __call__(self) -> "_FailingFactory":
        return self

    async def __aenter__(self) -> _FailingSession:
        return _FailingSession(self._error)

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
async def failing_app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    try:
        yield app
    finally:
        await app.state.engine.dispose()


def assert_db_unavailable_v2(response: httpx.Response) -> None:
    assert response.status_code == 503, response.text
    body = response.json()
    assert list(body) == ["error"]
    assert list(body["error"]) == ["code", "message", "details"]
    assert body["error"]["code"] == "db_unavailable"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["error"]["details"] is None


@pytest.mark.parametrize("url", V2_URLS, ids=["rovers", "photos"])
async def test_v2_database_down_returns_503_db_unavailable(
    down_client: httpx.AsyncClient, url: str
) -> None:
    assert_db_unavailable_v2(await down_client.get(url))


@pytest.mark.parametrize("url", V1_URLS, ids=["rovers", "photos"])
async def test_v1_database_down_returns_503_legacy_message(
    down_client: httpx.AsyncClient, url: str
) -> None:
    response = await down_client.get(url)

    assert response.status_code == 503, response.text
    assert response.json() == {"errors": "Database unavailable"}


CONNECTION_ERRORS = [
    OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused")),
    ConnectionRefusedError("connection refused"),
    OSError("network is unreachable"),
    TimeoutError("connect timed out"),
]
CONNECTION_ERROR_IDS = ["operational-error", "connection-refused", "oserror", "timeout"]


@pytest.mark.parametrize("error", CONNECTION_ERRORS, ids=CONNECTION_ERROR_IDS)
@pytest.mark.parametrize("url", V2_URLS, ids=["rovers", "photos"])
async def test_v2_connection_error_from_session_returns_503(
    failing_app: FastAPI, url: str, error: BaseException
) -> None:
    failing_app.state.session_factory = _FailingFactory(error)

    async with _client(failing_app) as client:
        assert_db_unavailable_v2(await client.get(url))


@pytest.mark.parametrize("error", CONNECTION_ERRORS, ids=CONNECTION_ERROR_IDS)
async def test_v1_connection_error_from_session_returns_503(
    failing_app: FastAPI, error: BaseException
) -> None:
    failing_app.state.session_factory = _FailingFactory(error)

    async with _client(failing_app) as client:
        response = await client.get(V1_URLS[0])

    assert response.status_code == 503, response.text
    assert response.json() == {"errors": "Database unavailable"}


@pytest.mark.parametrize(
    "error",
    [TypeError("bug"), KeyError("bug"), AttributeError("bug")],
    ids=["type-error", "key-error", "attribute-error"],
)
async def test_v2_programming_error_still_returns_500(
    failing_app: FastAPI, error: BaseException
) -> None:
    failing_app.state.session_factory = _FailingFactory(error)

    async with _client(failing_app) as client:
        response = await client.get(V2_URLS[0])

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"


# --- stage 6: 503 only for database errors (docs/PLAN.md, stage 6, last item) ------------

NON_DB_ERRORS = [
    OSError("disk is full"),
    ConnectionRefusedError("upstream refused"),
    TimeoutError("upstream timed out"),
]
NON_DB_ERROR_IDS = ["oserror", "connection-refused", "timeout"]


def _add_probe(app: FastAPI, path: str, error: BaseException) -> None:
    """A test-only route that fails outside any database session."""

    async def probe() -> None:
        raise error

    app.add_api_route(path, probe, methods=["GET"])


@pytest.mark.parametrize("error", NON_DB_ERRORS, ids=NON_DB_ERROR_IDS)
async def test_v2_non_db_oserror_returns_500_not_503(
    failing_app: FastAPI, error: BaseException
) -> None:
    _add_probe(failing_app, "/api/v2/__probe", error)

    async with _client(failing_app) as client:
        response = await client.get("/api/v2/__probe")

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"


@pytest.mark.parametrize("error", NON_DB_ERRORS, ids=NON_DB_ERROR_IDS)
@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"])
async def test_v1_non_db_oserror_returns_500_not_503(
    failing_app: FastAPI, error: BaseException, prefix: str
) -> None:
    _add_probe(failing_app, f"{prefix}/__probe", error)

    async with _client(failing_app) as client:
        response = await client.get(f"{prefix}/__probe")

    assert response.status_code == 500, response.text
    assert response.json() == {"errors": "Internal server error"}
