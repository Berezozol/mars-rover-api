"""The autouse network guard (tests/conftest.py) blocks real HTTP in every test."""

import httpx
import pytest
import respx
from fastapi import FastAPI

pytestmark = pytest.mark.unit

UNMOCKED = "https://mars.nasa.gov/rss/api/?feed=raw_images"


async def test_unmocked_request_fails_without_requesting_the_router() -> None:
    """The guard is active even when the test does not ask for `upstream_router`."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError):
            await client.get(UNMOCKED)


def test_unmocked_sync_request_fails() -> None:
    with httpx.Client() as client, pytest.raises(AssertionError):
        client.get(UNMOCKED)


async def test_routes_added_to_the_guard_are_served(upstream_router: respx.MockRouter) -> None:
    route = upstream_router.get("https://feeds.example.test/ok").respond(200, json={"ok": 1})

    async with httpx.AsyncClient() as client:
        response = await client.get("https://feeds.example.test/ok")
        with pytest.raises(AssertionError):
            await client.get(UNMOCKED)

    assert response.json() == {"ok": 1}
    assert route.call_count == 1


async def test_in_process_asgi_client_is_not_blocked() -> None:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "yes"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"pong": "yes"}
