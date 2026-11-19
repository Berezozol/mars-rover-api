"""GET /health/sync (docs/API.md; stage 9).

- 200 ``{"status": "ok", "rovers": [...]}`` when no flag is raised, otherwise 503 with
  ``"status": "stale"`` and the same body; database down -> 503 ``{"status": "degraded",
  "db": "error"}``.
- Rover items: ``rover``, ``last_success_at`` (ISO UTC with ``Z`` or null),
  ``newest_photo_date`` (``YYYY-MM-DD`` or null), ``sync_stale``, ``photos_stale``; only
  curiosity and perseverance.
- ``Cache-Control: no-store``; no auth, no rate limit, no secrets or error texts.

The endpoint uses the real clock, so the data is placed relative to ``datetime.now(UTC)``
with wide margins (hours and days), never at a boundary.
"""

import re
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.main import create_app
from tests.integration._freshness import ROVER_KEYS, SYNCED_ROVERS, add_photos, add_run

pytestmark = pytest.mark.api

Factory = async_sessionmaker[AsyncSession]

URL = "/health/sync"
# As in v2 and the contract example: whole seconds, UTC, "Z".
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
ISO_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")


def now() -> datetime:
    return datetime.now(UTC)


async def make_fresh(factory: Factory, rover_id: str) -> None:
    current = now()
    await add_run(factory, rover_id, finished_at=current - timedelta(hours=2))
    await add_photos(factory, rover_id, [current.date() - timedelta(days=1)])


def assert_body_shape(body: dict[str, object]) -> list[dict[str, object]]:
    assert list(body) == ["status", "rovers"]
    rovers = body["rovers"]
    assert isinstance(rovers, list)
    assert [item["rover"] for item in rovers] == SYNCED_ROVERS
    for item in rovers:
        assert list(item) == ROVER_KEYS
        assert item["last_success_at"] is None or ISO_Z.match(item["last_success_at"]), item
        assert item["newest_photo_date"] is None or ISO_DATE.match(item["newest_photo_date"])
        assert isinstance(item["sync_stale"], bool)
        assert isinstance(item["photos_stale"], bool)
    return rovers


def assert_no_store(response: httpx.Response) -> None:
    assert response.headers.get("cache-control") == "no-store", dict(response.headers)
    assert "etag" not in response.headers


async def test_health_sync_all_fresh_returns_200_ok(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)

    response = await client.get(URL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    for item in assert_body_shape(body):
        assert item["sync_stale"] is False
        assert item["photos_stale"] is False
        assert item["newest_photo_date"] == (now().date() - timedelta(days=1)).isoformat()
    assert_no_store(response)


async def test_health_sync_last_success_at_is_utc_with_z_seconds(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    finished = (now() - timedelta(hours=3)).replace(microsecond=654321)
    for rover in SYNCED_ROVERS:
        await add_run(clean_db, rover, finished_at=finished)
        await add_photos(clean_db, rover, [now().date()])

    body = (await client.get(URL)).json()

    expected = finished.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert [item["last_success_at"] for item in body["rovers"]] == [expected, expected]


async def test_health_sync_last_success_ignores_failed_and_partial_runs(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    success = (now() - timedelta(hours=5)).replace(microsecond=0)
    for rover in SYNCED_ROVERS:
        await add_run(clean_db, rover, finished_at=success)
        await add_photos(clean_db, rover, [now().date()])
    await add_run(clean_db, "curiosity", status="failed", finished_at=now() - timedelta(hours=1))
    await add_run(clean_db, "curiosity", status="partial", finished_at=now() - timedelta(hours=1))

    response = await client.get(URL)

    assert response.status_code == 200, response.text
    curiosity = response.json()["rovers"][0]
    assert curiosity["last_success_at"] == success.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert curiosity["sync_stale"] is False


async def test_health_sync_empty_database_returns_503_stale_with_nulls(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    response = await client.get(URL)

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "stale"
    for item in assert_body_shape(body):
        assert item == {
            "rover": item["rover"],
            "last_success_at": None,
            "newest_photo_date": None,
            "sync_stale": True,
            "photos_stale": True,
        }
    assert_no_store(response)


@pytest.mark.parametrize(
    ("status", "age"),
    [
        pytest.param("success", timedelta(hours=30), id="success-30h-ago"),
        pytest.param("failed", timedelta(hours=1), id="only-failed"),
        pytest.param("partial", timedelta(hours=1), id="only-partial"),
    ],
)
async def test_health_sync_sync_stale_returns_503(
    client: httpx.AsyncClient, clean_db: Factory, status: str, age: timedelta
) -> None:
    await make_fresh(clean_db, "perseverance")
    await add_run(clean_db, "curiosity", status=status, finished_at=now() - age)
    await add_photos(clean_db, "curiosity", [now().date()])

    response = await client.get(URL)

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "stale"
    curiosity, perseverance = assert_body_shape(body)
    assert curiosity["sync_stale"] is True
    assert curiosity["photos_stale"] is False
    assert perseverance["sync_stale"] is False
    assert perseverance["photos_stale"] is False
    assert_no_store(response)


async def test_health_sync_photos_stale_returns_503(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    await make_fresh(clean_db, "curiosity")
    await add_run(clean_db, "perseverance", finished_at=now() - timedelta(hours=1))
    old = now().date() - timedelta(days=30)
    await add_photos(clean_db, "perseverance", [old - timedelta(days=3), old])

    response = await client.get(URL)

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "stale"
    _, perseverance = assert_body_shape(body)
    assert perseverance["newest_photo_date"] == old.isoformat()
    assert perseverance["photos_stale"] is True
    assert perseverance["sync_stale"] is False


async def test_health_sync_mer_rovers_are_not_listed_nor_counted(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)
    await add_photos(clean_db, "spirit", [date(2010, 3, 21)])
    await add_photos(clean_db, "opportunity", [date(2018, 6, 10)])

    response = await client.get(URL)

    assert response.status_code == 200, response.text
    assert [item["rover"] for item in response.json()["rovers"]] == SYNCED_ROVERS


async def test_health_sync_does_not_leak_secrets_or_errors(
    settings: Settings, clean_db: Factory
) -> None:
    admin_token = "health-admin-token-secret"
    bot_token = "987654:health-bot-token-secret"
    app = create_app(
        settings.model_copy(
            update={
                "admin_token": SecretStr(admin_token),
                "telegram_bot_token": SecretStr(bot_token),
                "telegram_chat_id": "777",
            }
        )
    )
    error_text = "upstream exploded: secret-error-detail"
    await add_run(clean_db, "curiosity", status="failed", finished_at=now(), error=error_text)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(URL)
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 503
    for secret in (admin_token, bot_token, "secret-error-detail", "777"):
        assert secret not in response.text


async def test_health_sync_without_database_returns_503_degraded(settings: Settings) -> None:
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
            response = await client.get(URL)
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error"}
    assert_no_store(response)


# --- HEAD ----------------------------------------------------------------------------------


async def test_health_sync_head_fresh_returns_200_no_store_without_body(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)

    response = await client.head(URL)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("application/json")
    assert_no_store(response)


async def test_health_sync_head_stale_returns_503_no_store_without_body(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    response = await client.head(URL)

    assert response.status_code == 503
    assert response.content == b""
    assert_no_store(response)


async def test_health_sync_head_without_database_returns_503_without_body(
    settings: Settings,
) -> None:
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
            response = await client.head(URL)
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 503
    assert response.content == b""
    assert_no_store(response)


# --- response cache (HEALTH_SYNC_CACHE_SECONDS) --------------------------------------------


def test_health_sync_cache_seconds_default_is_30() -> None:
    assert Settings.model_fields["health_sync_cache_seconds"].default == 30


async def test_health_sync_cache_returns_previous_result_within_window(
    settings: Settings, clean_db: Factory
) -> None:
    app = create_app(settings.model_copy(update={"health_sync_cache_seconds": 3600}))
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get(URL)
            for rover in SYNCED_ROVERS:
                await make_fresh(clean_db, rover)
            second = await client.get(URL)
    finally:
        await app.state.engine.dispose()

    assert first.status_code == 503
    assert second.status_code == 503, "the cached result is served within the window"
    assert second.json() == first.json()
    assert_no_store(second)


async def test_health_sync_cache_disabled_sees_data_changes(
    client: httpx.AsyncClient, clean_db: Factory
) -> None:
    """The suite runs with HEALTH_SYNC_CACHE_SECONDS=0 (tests/conftest.py)."""
    first = await client.get(URL)
    for rover in SYNCED_ROVERS:
        await make_fresh(clean_db, rover)

    second = await client.get(URL)

    assert (first.status_code, second.status_code) == (503, 200)
