import httpx
import pytest

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.api


@pytest.mark.usefixtures("migrated_db")
async def test_health_with_database_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_health_without_database_returns_503(settings: Settings) -> None:
    # Port 1 on localhost refuses connections immediately: the database is "down".
    broken = settings.model_copy(
        update={
            "database_url": "postgresql+asyncpg://mars:mars@127.0.0.1:1/mars",
            "db_connect_timeout_seconds": 2.0,
        }
    )
    app = create_app(broken)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error"}


async def test_openapi_schema_is_available(client: httpx.AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Mars Rover API"
