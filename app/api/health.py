"""Liveness, database health and data freshness checks (not rate limited).

Both answer HEAD with the GET status and headers and no body (uptime monitors send HEAD).
"""

import logging
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import NO_STORE
from app.services.health import (
    FreshnessCache,
    RoverFreshness,
    SyncFreshness,
    get_sync_freshness,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_DEGRADED = {"status": "degraded", "db": "error"}


@router.get("/health")
# HEAD: the same handler, kept out of the schema (one operationId per path).
@router.head("/health", include_in_schema=False)
async def health(request: Request) -> JSONResponse:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Database health check failed", exc_info=True)
        return _json(request, _DEGRADED, 503)
    return _json(request, {"status": "ok", "db": "ok"}, 200)


@router.get("/health/sync")
# HEAD: the same handler, kept out of the schema (one operationId per path).
@router.head("/health/sync", include_in_schema=False)
async def health_sync(request: Request) -> JSONResponse:
    """Freshness of the synced rovers: 200 if fresh, 503 if stale or the database is down."""
    no_store = {"Cache-Control": NO_STORE}
    try:
        freshness = await _freshness_cache(request).get(lambda: _load_freshness(request))
    except Exception:
        logger.warning("Sync freshness check failed", exc_info=True)
        return _json(request, _DEGRADED, 503, no_store)
    status_code = 503 if freshness.stale else 200
    return _json(request, _freshness_body(freshness), status_code, no_store)


def _freshness_cache(request: Request) -> FreshnessCache:
    """The app's cache (``app.state.health_sync_cache``), created on first use."""
    state = request.app.state
    cache: FreshnessCache | None = getattr(state, "health_sync_cache", None)
    if cache is None:
        settings: Settings = state.settings
        cache = FreshnessCache(settings.health_sync_cache_seconds)
        state.health_sync_cache = cache
    return cache


async def _load_freshness(request: Request) -> SyncFreshness:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        return await get_sync_freshness(session, request.app.state.settings, datetime.now(UTC))


def _json(
    request: Request,
    content: dict[str, Any],
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response = JSONResponse(content, status_code=status_code, headers=headers)
    if request.method == "HEAD":
        # Same headers as GET (Content-Length included), no body.
        response.body = b""
    return response


def _freshness_body(freshness: SyncFreshness) -> dict[str, Any]:
    return {"status": freshness.status, "rovers": [_rover_body(r) for r in freshness.rovers]}


def _rover_body(rover: RoverFreshness) -> dict[str, Any]:
    return {
        "rover": rover.rover,
        "last_success_at": _utc_z(rover.last_success_at),
        "newest_photo_date": _iso_date(rover.newest_photo_date),
        "sync_stale": rover.sync_stale,
        "photos_stale": rover.photos_stale,
    }


def _utc_z(value: datetime | None) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else None


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None
