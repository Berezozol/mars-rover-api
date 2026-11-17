"""Shared fixtures.

- `settings` points the app at the test database (TEST_DATABASE_URL), never the dev one.
- `alembic_cfg` is an Alembic config bound to the test database.
- `migrated_db` applies all Alembic migrations to the test database once per session.
  Use it in every test that needs the database (integration and most api tests).
- `db_session` is an AsyncSession inside an outer transaction that is rolled back after the
  test: repository code may flush (and commit to a savepoint), nothing survives the test.
- `make_record` builds `PhotoRecord` objects with sensible defaults.
- `app` / `client` give an application instance and an HTTP client without a real server.
"""

import itertools
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent

ROUNDTRIP_MARKER = "migrations_roundtrip"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{ROUNDTRIP_MARKER}: drops and recreates the test schema; runs last, not xdist-safe",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run schema round-trip tests after everything else (stable sort keeps the rest)."""
    items.sort(key=lambda item: item.get_closest_marker(ROUNDTRIP_MARKER) is not None)


def _guard_test_database(base: Settings) -> None:
    """Abort before any test can migrate, downgrade or write to a non-test database."""
    test_url = make_url(base.test_database_url)
    if test_url == make_url(base.database_url) or not (test_url.database or "").endswith("_test"):
        pytest.exit(
            "Refusing to run: TEST_DATABASE_URL must differ from DATABASE_URL and point to a "
            f"database whose name ends with '_test' (got {test_url.database!r}).",
            returncode=2,
        )


@pytest.fixture(scope="session")
def settings() -> Settings:
    base = get_settings()
    _guard_test_database(base)
    return base.model_copy(
        update={
            "database_url": base.test_database_url,
            "environment": "test",
            "sync_enabled": False,
        }
    )


@pytest.fixture(scope="session")
def alembic_cfg(settings: Settings) -> AlembicConfig:
    _guard_test_database(get_settings())
    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    # "%" must be escaped for configparser interpolation.
    cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def migrated_db(settings: Settings, alembic_cfg: AlembicConfig) -> Iterator[str]:
    command.upgrade(alembic_cfg, "head")
    yield settings.database_url


@pytest.fixture
async def db_session(migrated_db: str) -> AsyncIterator[AsyncSession]:
    """Session bound to a connection whose outer transaction is always rolled back."""
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            session = AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            try:
                yield session
            finally:
                await session.close()
                if outer.is_active:
                    await outer.rollback()
    finally:
        await engine.dispose()


_record_counter = itertools.count(1)


def build_record(**overrides: Any) -> Any:
    """Build a `PhotoRecord` with defaults; every call gets a unique `source_id`."""
    from app.sources.base import PhotoRecord  # lazy: conftest must load without stage-1 code

    n = next(_record_counter)
    source_id = overrides.pop("source_id", f"test-photo-{n}")
    values: dict[str, Any] = {
        "source_id": source_id,
        "sol": 100,
        "camera_code": "FHAZ",
        "taken_at": datetime(2020, 1, 1, 12, 0, tzinfo=UTC),
        "earth_date": date(2020, 1, 1),
        "sample_type": "full",
        "img_full": f"https://images.example.test/{source_id}.png",
        "credit": "NASA/JPL-Caltech",
    }
    values.update(overrides)
    return PhotoRecord(**values)


@pytest.fixture
def make_record() -> Callable[..., Any]:
    return build_record


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
