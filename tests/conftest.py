"""Shared fixtures.

- `settings` points the app at the test database (TEST_DATABASE_URL), never the dev one.
- `migrated_db` applies all Alembic migrations to the test database once per session.
  Use it in every test that needs the database (integration and most api tests).
- `app` / `client` give an application instance and an HTTP client without a real server.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def settings() -> Settings:
    base = get_settings()
    return base.model_copy(
        update={
            "database_url": base.test_database_url,
            "environment": "test",
            "sync_enabled": False,
        }
    )


@pytest.fixture(scope="session")
def migrated_db(settings: Settings) -> Iterator[str]:
    alembic_cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")
    yield settings.database_url


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    yield application
    await application.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
