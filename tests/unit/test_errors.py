"""The error format depends on the API version: v1 keeps the legacy shape, v2 has its own."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Query

from app.core.errors import NotFoundError, is_legacy_path, register_exception_handlers

pytestmark = pytest.mark.unit


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise NotFoundError("Rover 'spirit' not found", code="rover_not_found")

    @router.get("/items")
    async def items(sol: int = Query(ge=0)) -> dict[str, int]:
        return {"sol": sol}

    app.include_router(router, prefix="/api/v1")
    app.include_router(router, prefix="/mars-photos/api/v1")
    app.include_router(router, prefix="/api/v2")
    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1", True),
        ("/api/v1/rovers", True),
        ("/mars-photos/api/v1/rovers", True),
        ("/api/v2/rovers", False),
        ("/api/v10/rovers", False),
        ("/health", False),
    ],
    ids=["v1-root", "v1", "v1-nasa-prefix", "v2", "not-v1-prefix", "health"],
)
def test_is_legacy_path(path: str, expected: bool) -> None:
    assert is_legacy_path(path) is expected


@pytest.mark.parametrize("prefix", ["/api/v1", "/mars-photos/api/v1"], ids=["v1", "v1-nasa-prefix"])
async def test_app_error_on_v1_uses_legacy_format(client: httpx.AsyncClient, prefix: str) -> None:
    response = await client.get(f"{prefix}/boom")

    assert response.status_code == 404
    assert response.json() == {"errors": "Rover 'spirit' not found"}


async def test_app_error_on_v2_uses_structured_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v2/boom")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "rover_not_found", "message": "Rover 'spirit' not found", "details": None}
    }


async def test_unknown_route_on_v2_returns_not_found_error(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v2/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_unknown_route_on_v1_returns_legacy_error(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"errors": "Not found"}


async def test_invalid_query_on_v2_returns_422_with_field_details(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v2/items", params={"sol": "abc"})

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert [detail["field"] for detail in body["details"]] == ["sol"]


async def test_invalid_query_on_v1_returns_400_with_short_message(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/items", params={"sol": "abc"})

    assert response.status_code == 400
    assert response.json() == {"errors": "Invalid sol"}
