"""Application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app import __version__
from app.api.caching import CachingMiddleware, VaryAcceptEncodingMiddleware
from app.api.health import router as health_router
from app.api.legacy import router as legacy_router
from app.api.v2 import router as v2_router
from app.api.v2.admin import router as admin_router
from app.core.config import Settings, check_admin_token, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.services.admin import AdapterFactory, cancel_background
from app.services.scheduler import SyncScheduler
from app.services.sync import SyncService
from app.sources.base import SourceAdapter
from app.sources.http import UpstreamClient
from app.sources.registry import build_adapter

# Bodies below this many bytes are sent as is.
GZIP_MINIMUM_SIZE = 1000
# zlib level 1: an 8 MB v1 list compresses ~85x in ~11 ms; level 6 gains ~25% more ratio at
# ~3x the time (it would push the largest v1 lists past the 200 ms target).
GZIP_COMPRESS_LEVEL = 1


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Test hooks (replace on ``app.state`` before requests):

    - ``adapter_factory``: ``Callable[[str], SourceAdapter]`` used by the admin sync;
      the default builds the real adapter with the shared upstream client (lifespan).
    - ``sync_tasks``: set of the running background sync tasks (await them in tests).
    """
    settings = settings or get_settings()
    # Fail fast on a too-short ADMIN_TOKEN (model_copy(update=...) skips the validator).
    check_admin_token(settings.admin_token)
    configure_logging(settings.log_level)

    engine = create_engine(
        settings.database_url,
        connect_timeout=settings.db_connect_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if settings.sync_enabled or settings.admin_enabled:
                app.state.upstream_client = UpstreamClient(settings)
            if settings.sync_enabled:
                _start_scheduler(app, settings)
            yield
        finally:
            try:
                await _stop_sync(app)
            finally:
                await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Unofficial replacement for the archived Mars Rover Photos API. "
            "Not affiliated with or endorsed by NASA or JPL. "
            "Images courtesy of NASA/JPL-Caltech and instrument partners."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.upstream_client = None
    app.state.scheduler = None
    # One service for the scheduler and the admin endpoints.
    app.state.sync_service = SyncService(app.state.session_factory, settings)
    app.state.adapter_factory = _default_adapter_factory(app, settings)
    app.state.sync_tasks = set()

    # Added first = inner: CORS headers also reach 304 responses.
    app.add_middleware(CachingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )
    # Outside the caching middleware: the ETag is computed on the uncompressed body, so it
    # does not depend on the encoding (see app.api.caching).
    app.add_middleware(
        GZipMiddleware, minimum_size=GZIP_MINIMUM_SIZE, compresslevel=GZIP_COMPRESS_LEVEL
    )
    app.add_middleware(VaryAcceptEncodingMiddleware)
    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(legacy_router, prefix="/api/v1")
    app.include_router(legacy_router, prefix="/mars-photos/api/v1", include_in_schema=False)
    app.include_router(v2_router, prefix="/api/v2")
    if settings.admin_enabled:
        app.include_router(admin_router, prefix="/api/v2")
    return app


def _default_adapter_factory(app: FastAPI, settings: Settings) -> AdapterFactory:
    """Real adapters over the shared upstream client created in the lifespan."""

    def factory(rover_id: str) -> SourceAdapter:
        client: UpstreamClient | None = app.state.upstream_client
        if client is None:
            raise RuntimeError("The upstream client is not running (application not started)")
        return build_adapter(rover_id, client, settings)

    return factory


def _start_scheduler(app: FastAPI, settings: Settings) -> None:
    """Start the periodic sync on the shared client and service."""
    app.state.scheduler = SyncScheduler(app.state.sync_service, app.state.upstream_client, settings)
    app.state.scheduler.start()


async def _stop_sync(app: FastAPI) -> None:
    """Stop the scheduler and background syncs, then close the client (always)."""
    try:
        try:
            if app.state.scheduler is not None:
                await app.state.scheduler.shutdown()
        finally:
            tasks: set[asyncio.Task[None]] = app.state.sync_tasks
            await cancel_background(tasks)
    finally:
        if app.state.upstream_client is not None:
            await app.state.upstream_client.aclose()
            app.state.upstream_client = None


app = create_app()
