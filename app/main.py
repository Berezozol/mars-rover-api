"""Application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.health import router as health_router
from app.api.legacy import router as legacy_router
from app.api.v2 import router as v2_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.services.scheduler import SyncScheduler
from app.services.sync import SyncService
from app.sources.http import UpstreamClient


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = create_engine(
        settings.database_url,
        connect_timeout=settings.db_connect_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if settings.sync_enabled:
                _start_sync(app, settings)
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(legacy_router, prefix="/api/v1")
    app.include_router(legacy_router, prefix="/mars-photos/api/v1", include_in_schema=False)
    app.include_router(v2_router, prefix="/api/v2")
    return app


def _start_sync(app: FastAPI, settings: Settings) -> None:
    """Create the shared upstream client and start the periodic sync."""
    client = UpstreamClient(settings)
    app.state.upstream_client = client
    service = SyncService(app.state.session_factory, settings)
    app.state.scheduler = SyncScheduler(service, client, settings)
    app.state.scheduler.start()


async def _stop_sync(app: FastAPI) -> None:
    """Stop the scheduler and close the client; the client is closed even if stopping fails."""
    try:
        if app.state.scheduler is not None:
            await app.state.scheduler.shutdown()
    finally:
        if app.state.upstream_client is not None:
            await app.state.upstream_client.aclose()


app = create_app()
