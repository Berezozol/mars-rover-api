"""Shared fixtures.

Parallel run (pytest-xdist): `uv run pytest -q -p no:cacheprovider -n 12`. Every run gets
its own id (`MARS_TEST_RUN_ID`, set by the controller in `pytest_configure`, inherited by
the workers) and every worker its own database `<TEST_DATABASE_URL name>_<run id>_<worker>`
(e.g. mars_test_65a1b2c3d4_gw0), cloned with CREATE DATABASE ... TEMPLATE from
`<name>_<run id>_template`, which is migrated once per run under a Postgres advisory lock
(no races between workers). Two concurrent `-n` runs therefore never touch each other's
databases. The controller drops the run's template at the end; databases of crashed runs
(run id older than 6 hours, plus legacy `<name>_gwN` / `<name>_template` without any
connection) are dropped when the next run builds its template. Without xdist the plain
TEST_DATABASE_URL database is used (do not run two plain runs at the same time). All
temporary directories come from `tmp_path` / `tmp_path_factory` (unique per worker).
Timings: tests/README.md.

- `settings` points the app at the test database (TEST_DATABASE_URL), never the dev one,
  with the per-IP rate limit switched off (RATE_LIMIT_ENABLED=false) and no /health/sync
  response cache (HEALTH_SYNC_CACHE_SECONDS=0); the MER image cache lives in a temp dir
  (the `app` fixture uses a per-test one).
- `alembic_cfg` is an Alembic config bound to the test database.
- `migrated_db` applies all Alembic migrations to the test database once per session.
  Use it in every test that needs the database (integration and most api tests).
- `db_session` is an AsyncSession inside an outer transaction that is rolled back after the
  test: repository code may flush (and commit to a savepoint), nothing survives the test.
- `session_factory` is an `async_sessionmaker` bound to the test database for code that
  COMMITS (the sync service). Such tests must also use `clean_db`.
- `clean_db` empties photos, sol_summaries, sync_runs, camera_sol_counts and
  sol_source_counts, removes non-seed cameras and resets the rovers cache fields before and
  after the test; seed rows survive.
- `make_record` builds `PhotoRecord` objects with sensible defaults.
- `app` / `client` give an application instance and an HTTP client without a real server.
- `upstream_router` (autouse) blocks real outgoing HTTP for every test: it is a respx router
  with `assert_all_mocked=True`; tests add routes to it to mock upstream feeds. In-process
  `httpx.ASGITransport` clients are not affected.
"""

import asyncio
import itertools
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.main import create_app
from tests._rad import events, fake_sleep, rad_env  # noqa: F401  (fixtures of the stage-21 tests)
from tests._weather import weather_env  # noqa: F401  (fixture of the stage-17 tests)

ROOT = Path(__file__).resolve().parent.parent

ROUNDTRIP_MARKER = "migrations_roundtrip"


RUN_ID_ENV = "MARS_TEST_RUN_ID"
# A run id is `<unix seconds, 8 hex digits><4 random hex digits>`; databases of a run older than
# this are considered left over by a crashed run.
_STALE_AFTER_SECONDS = 6 * 3600


def _new_run_id() -> str:
    return f"{int(time.time()):08x}{secrets.token_hex(2)}"


def _is_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{ROUNDTRIP_MARKER}: drops and recreates the schema; runs last (own DB per xdist worker)",
    )
    # The controller picks the run id before any worker is spawned; workers inherit the
    # environment, so `setdefault` keeps one id per run (and for a plain run it is unused).
    if not _is_worker(config):
        os.environ[RUN_ID_ENV] = _new_run_id()
    else:
        os.environ.setdefault(RUN_ID_ENV, _new_run_id())


def pytest_unconfigure(config: pytest.Config) -> None:
    """Controller only: drop this run's template (and any worker database left behind)."""
    if _is_worker(config) or getattr(config.option, "dist", "no") == "no":
        return
    run_id = os.environ.get(RUN_ID_ENV)
    if not run_id:
        return
    try:
        base = get_settings()
        name = make_url(base.test_database_url).database or "mars_test"
        asyncio.run(_drop_run_databases(base.test_database_url, name, run_id))
    except Exception:  # noqa: BLE001  (cleanup must never fail the run; stale ones are swept later)
        pass


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


# Arbitrary constant: key of the Postgres advisory lock that serializes database provisioning.
_PROVISION_LOCK_KEY = 7_345_001
_TEMPLATE_READY_MARKER = "mars-test-template.ready"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _alembic_config_for(url: str) -> AlembicConfig:
    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    # "%" must be escaped for configparser interpolation.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _run_database_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(name)}_([0-9a-f]{{8}})[0-9a-f]{{4}}_(?:gw[0-9]+|template)$")


def _legacy_database_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(name)}_(?:gw[0-9]+|template)$")


async def _sweep_stale_databases(conn: Any, name: str, now: float | None = None) -> None:
    """Drop databases of crashed runs: old run ids, and legacy names nobody is connected to."""
    now = time.time() if now is None else now
    rows = await conn.execute(
        text(
            "SELECT d.datname, (SELECT count(*) FROM pg_stat_activity a "
            "WHERE a.datname = d.datname) AS sessions FROM pg_database d "
            "WHERE left(d.datname, length(:prefix)) = :prefix"
        ),
        {"prefix": f"{name}_"},
    )
    run_pattern, legacy_pattern = _run_database_pattern(name), _legacy_database_pattern(name)
    for datname, sessions in rows.all():
        match = run_pattern.match(datname)
        if match:
            stale = now - int(match.group(1), 16) > _STALE_AFTER_SECONDS
        else:
            stale = bool(legacy_pattern.match(datname)) and sessions == 0
        if stale:
            await conn.execute(
                text(f"DROP DATABASE IF EXISTS {_quote_ident(datname)} WITH (FORCE)")
            )


async def _drop_run_databases(base_url: str, name: str, run_id: str) -> None:
    admin_url = make_url(base_url).set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    prefix = f"{name}_{run_id}_"
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT datname FROM pg_database WHERE left(datname, length(:p)) = :p"),
                {"p": prefix},
            )
            for (datname,) in rows.all():
                await conn.execute(
                    text(f"DROP DATABASE IF EXISTS {_quote_ident(datname)} WITH (FORCE)")
                )
    finally:
        await engine.dispose()


async def _provision_worker_database(
    base_url: str, template: str, worker_db: str, marker: Path
) -> None:
    """Migrate the template once per run, then clone it as the worker's database.

    Everything runs under one advisory lock: workers queue up, the first one builds the
    template (the marker file in the shared base temp dir says "already built in this run"),
    the others only clone. Cloning needs no other session on the template, hence the lock
    also serializes the clones.
    """
    admin_url = make_url(base_url).set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _PROVISION_LOCK_KEY})
            try:
                if not marker.exists():  # noqa: ASYNC240 (tiny local file)
                    await _sweep_stale_databases(conn, make_url(base_url).database or "")
                    await conn.execute(
                        text(f"DROP DATABASE IF EXISTS {_quote_ident(template)} WITH (FORCE)")
                    )
                    await conn.execute(text(f"CREATE DATABASE {_quote_ident(template)}"))
                    template_url = make_url(base_url).set(database=template)
                    url = template_url.render_as_string(hide_password=False)
                    # migrations/env.py calls asyncio.run() itself: run it off this loop.
                    await asyncio.to_thread(command.upgrade, _alembic_config_for(url), "head")
                    marker.write_text("ready", encoding="utf-8")  # noqa: ASYNC240
                await conn.execute(
                    text(f"DROP DATABASE IF EXISTS {_quote_ident(worker_db)} WITH (FORCE)")
                )
                await conn.execute(
                    text(
                        f"CREATE DATABASE {_quote_ident(worker_db)} "
                        f"TEMPLATE {_quote_ident(template)}"
                    )
                )
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _PROVISION_LOCK_KEY}
                )
    finally:
        await engine.dispose()


async def _drop_database(base_url: str, name: str) -> None:
    admin_url = make_url(base_url).set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """URL of this process's test database.

    Plain run: TEST_DATABASE_URL itself. Under xdist: a private `<name>_<run id>_<worker>` clone.
    """
    base = get_settings()
    _guard_test_database(base)
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        yield base.test_database_url
        return
    base_url = make_url(base.test_database_url)
    name = base_url.database or "mars_test"
    run_id = os.environ[RUN_ID_ENV]
    worker_db = f"{name}_{run_id}_{worker}"
    marker = tmp_path_factory.getbasetemp().parent / _TEMPLATE_READY_MARKER
    asyncio.run(
        _provision_worker_database(
            base.test_database_url, f"{name}_{run_id}_template", worker_db, marker
        )
    )
    try:
        yield base_url.set(database=worker_db).render_as_string(hide_password=False)
    finally:
        asyncio.run(_drop_database(base.test_database_url, worker_db))


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory, test_database_url: str) -> Settings:
    base = get_settings()
    _guard_test_database(base)
    return base.model_copy(
        update={
            "database_url": test_database_url,
            # Stage 10: no built web interface in the suite by default (the developer's own
            # frontend/dist must not change what `/` returns); tests of the interface build
            # a fake dist in tmp_path and pass `frontend_dist_dir`.
            "frontend_dist_dir": tmp_path_factory.mktemp("no-frontend") / "dist",
            "environment": "test",
            "sync_enabled": False,
            # Stage 9: the per-IP limit is off for the suite; rate-limit tests build their own
            # app with it on and small limits (tests/api/test_rate_limit.py).
            "rate_limit_enabled": False,
            # No /health/sync response cache: freshness tests change data between requests.
            "health_sync_cache_seconds": 0,
            # Stage 9a: converted MER images are cached in a temporary directory, never in
            # data/. The `app` fixture gives every test its own (empty) cache directory.
            "mer_image_cache_dir": tmp_path_factory.mktemp("mer-images-session"),
        }
    )


@pytest.fixture(scope="session")
def alembic_cfg(settings: Settings) -> AlembicConfig:
    _guard_test_database(get_settings())
    return _alembic_config_for(settings.database_url)


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


@pytest.fixture
async def session_factory(migrated_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory on the test database; whatever it commits stays (use `clean_db`)."""
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


# Number of seed cameras per rover (migrations 0002, 0008); sort_order starts at 1.
SEED_CAMERA_COUNTS = {
    "perseverance": 17,
    "curiosity": 7,
    "opportunity": 6,
    "spirit": 6,
    "ingenuity": 2,  # stage 12 (migration 0008)
}

_CLEAN_STATEMENTS = [
    "TRUNCATE photos, sol_summaries, sync_runs RESTART IDENTITY",
    "DELETE FROM cameras WHERE sort_order > CASE rover_id "
    + " ".join(f"WHEN '{rover}' THEN {count}" for rover, count in SEED_CAMERA_COUNTS.items())
    + " ELSE 0 END",
    "UPDATE rovers SET max_sol = NULL, max_date = NULL, total_photos = 0, "
    "last_synced_at = NULL, full_max_sol = NULL, full_max_date = NULL, full_total_photos = 0",
]


# Stage-7 tables (migration 0004); truncated only if present so that the suite still runs
# against a schema without them.
_OPTIONAL_TABLES = (
    "camera_sol_counts",
    "sol_source_counts",
    "rover_waypoints",  # stage 11 (migration 0006)
    "rover_traverse_segments",
    "location_datasets",
    "rover_samples",  # stage 13 (migration 0010)
    "sample_datasets",
    "rad_files",  # stage 21 (migration 0017)
    "rad_daily",
    "rad_dataset",
    "weather_daily",  # stage 17 (migration 0018); hourly rows go with it (CASCADE below)
    "weather_files",
    "weather_dataset",
)


async def _clean_database(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        for table in _OPTIONAL_TABLES:
            if await session.scalar(
                text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
            ):
                await session.execute(text(f"TRUNCATE {table} CASCADE"))
        for statement in _CLEAN_STATEMENTS:
            await session.execute(text(statement))
        await session.commit()


@pytest.fixture
async def clean_db(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Committed-data isolation: wipe sync data before and after the test, keep the seed.

    It TRUNCATEs tables of this process's test database: safe under xdist (one database per
    worker), but not alongside another process using the same database.
    """
    await _clean_database(session_factory)
    try:
        yield session_factory
    finally:
        await _clean_database(session_factory)


_API_KEY_TABLES = "api_key_usage_daily, api_key_requests, api_keys, api_accounts"


async def _clean_api_keys(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        if await session.scalar(text("SELECT to_regclass('public.api_accounts') IS NOT NULL")):
            await session.execute(text(f"TRUNCATE {_API_KEY_TABLES} RESTART IDENTITY CASCADE"))
            await session.commit()


@pytest.fixture
async def clean_api_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Stage 20: empty the account / key tables before and after the test (if they exist)."""
    await _clean_api_keys(session_factory)
    try:
        yield
    finally:
        await _clean_api_keys(session_factory)


@pytest.fixture
async def make_keys_app(settings: Settings) -> AsyncIterator[Callable[..., Any]]:
    """Factory of apps with API keys on (see tests/_apikeys.py); closed after the test.

    `await make_keys_app(**settings_overrides)` returns a `KeysApp` (app, fake clock, clients).
    """
    from contextlib import AsyncExitStack

    from tests._apikeys import keys_app

    async with AsyncExitStack() as stack:

        async def make(**extra: Any) -> Any:
            return await stack.enter_async_context(keys_app(settings, **extra))

        yield make


@pytest.fixture(autouse=True)
def upstream_router() -> Iterator[respx.MockRouter]:
    """Network guard: any httpx request that is not explicitly mocked fails the test."""
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


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
async def app(settings: Settings, tmp_path: Path) -> AsyncIterator[FastAPI]:
    # A fresh image cache per test: photo ids restart from 1 after every `clean_db`.
    application = create_app(
        settings.model_copy(update={"mer_image_cache_dir": tmp_path / "mer-images"})
    )
    try:
        yield application
    finally:
        # Close the lazily created HTTP clients (shared upstream client, MER converter).
        shared = getattr(application.state, "upstream_client", None)
        if shared is not None:
            await shared.aclose()
        closer = getattr(getattr(application.state, "mer_image_service", None), "aclose", None)
        if closer is not None:
            await closer()
        await application.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
