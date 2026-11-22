"""Application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES

from app import __version__
from app.api.apikeys import API_KEY_HEADER, ApiKeyResolver
from app.api.caching import CachingMiddleware, VaryAcceptEncodingMiddleware
from app.api.frontend import mount_frontend
from app.api.health import router as health_router
from app.api.home import root_router as root_home_router
from app.api.home import router as home_router
from app.api.legacy import router as legacy_router
from app.api.llms import router as llms_router
from app.api.ratelimit import RateLimitMiddleware, ResponseSizeMiddleware
from app.api.v2 import router as v2_router
from app.api.v2.admin import accounts_router, admin_auth_limiter
from app.api.v2.admin import router as admin_router
from app.core.config import Settings, check_admin_token, check_pool_settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.overload import HeavyQueryGate
from app.core.ratelimit import RateLimiter
from app.db.session import create_engine, create_session_factory
from app.services.admin import AdapterFactory, cancel_background
from app.services.alerts import AlertMonitor, TelegramNotifier
from app.services.api_keys import ApiKeyService, ApiKeyUsage
from app.services.health import FreshnessCache
from app.services.location_sync import LocationSyncService
from app.services.mer_images import MerImageService
from app.services.sample_sync import SampleSyncService
from app.services.scheduler import AlertScheduler, ApiKeyScheduler, SyncScheduler
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
    - ``rate_limiter`` (only with RATE_LIMIT_ENABLED) and ``admin_auth_limiter`` (only with
      admin enabled): ``RateLimiter`` instances; tests may replace their ``clock``.
    - ``v1_bytes_per_request`` (only with RATE_LIMIT_ENABLED): bytes of a v1 response per charged
      request; tests may change it. ``v1_raw_bytes_per_request``: same for the uncompressed body.
    - ``key_rate_limiter``, ``api_key_auth_limiter``, ``api_key_resolver``, ``api_key_usage``,
      ``api_key_service`` (only with API_KEYS_ENABLED; the limiters also need RATE_LIMIT_ENABLED):
      limiter of requests with a key (per account), of failed key attempts (per IP), key lookup
      cache (``clock``, ``clear()``), usage counters (``await flush()``), account service.
    - ``alert_monitor``: ``AlertMonitor`` when Telegram alerts are configured, else None.
    - ``mer_image_service``: ``MerImageService`` of the Spirit/Opportunity full frames (a stub
      with ``async get_full_jpeg(photo) -> bytes`` also works; ``aclose()`` is optional).
    - ``health_sync_cache``: ``FreshnessCache`` of /health/sync (``ttl``, ``clock``, ``clear()``).
    """
    settings = settings or get_settings()
    # model_copy(update=...) skips the validators: repeat the token and pool/gate checks.
    check_admin_token(settings.admin_token)
    check_pool_settings(settings)
    configure_logging(settings.log_level)

    # Requests get a statement timeout and a short pool wait; the sync service, alerts and
    # loaders use their own engine without a statement limit (long upserts and backfills).
    engine = create_engine(
        settings.database_url,
        connect_timeout=settings.db_connect_timeout_seconds,
        statement_timeout=settings.db_statement_timeout_seconds,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        disable_jit=True,
        no_parallel_workers=True,
        application_name="mars-api",
    )
    worker_engine = create_engine(
        settings.database_url,
        connect_timeout=settings.db_connect_timeout_seconds,
        application_name="mars-worker",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if settings.sync_enabled or settings.admin_enabled:
                app.state.upstream_client = UpstreamClient(settings)
            if settings.sync_enabled:
                _start_scheduler(app, settings)
            _start_alerts(app, settings)
            _start_api_key_jobs(app, settings)
            yield
        finally:
            try:
                try:
                    _stop_alerts(app)
                finally:
                    try:
                        await _stop_converter(app)
                    finally:
                        try:
                            await _stop_sync(app)
                        finally:
                            await _stop_api_key_jobs(app)
            finally:
                try:
                    await engine.dispose()
                finally:
                    await worker_engine.dispose()

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
    app.state.worker_engine = worker_engine
    app.state.session_factory = create_session_factory(engine)
    app.state.worker_session_factory = create_session_factory(worker_engine)
    # Heavy v2 searches in flight (tests may read ``in_use`` or replace the gate).
    app.state.heavy_query_gate = HeavyQueryGate(settings.heavy_query_concurrency)
    app.state.upstream_client = None
    app.state.scheduler = None
    # Set when the scheduler starts (SYNC_ENABLED): the rover position loader.
    app.state.location_sync_service = None
    # Set at the same time: the samples page loader.
    app.state.sample_sync_service = None
    # One service for the scheduler and the admin endpoints.
    app.state.sync_service = SyncService(app.state.worker_session_factory, settings)
    app.state.adapter_factory = _default_adapter_factory(app, settings)
    app.state.sync_tasks = set()
    app.state.alert_monitor = _alert_monitor(app, settings)
    app.state.alert_scheduler = None
    app.state.mer_image_service = MerImageService(settings)
    # /health/sync result cache (HEALTH_SYNC_CACHE_SECONDS; tests may clear() it or set ttl).
    app.state.health_sync_cache = FreshnessCache(settings.health_sync_cache_seconds)
    if settings.admin_enabled:
        app.state.admin_auth_limiter = admin_auth_limiter(settings)
    # Stage 20 (API_KEYS_ENABLED): key lookup, usage counters and the account service.
    app.state.api_key_resolver = None
    app.state.api_key_usage = None
    app.state.api_key_scheduler = None
    _document_api_key(app)
    if settings.api_keys_enabled:
        _setup_api_keys(app, settings)

    # Added first = inner: CORS headers also reach 304 responses.
    app.add_middleware(CachingMiddleware)
    if settings.rate_limit_enabled:
        app.state.rate_limiter = RateLimiter(
            [(settings.rate_limit_per_minute, 60), (settings.rate_limit_per_hour, 3600)],
            max_concurrent=settings.rate_limit_concurrent,
        )
        if settings.api_keys_enabled:
            _setup_key_limiters(app, settings)
        # Inside CORS (browsers can read the 429), outside caching (304s count too).
        app.add_middleware(RateLimitMiddleware)
        # Test hook: bytes of a v1 response that cost one request.
        app.state.v1_bytes_per_request = settings.rate_limit_v1_bytes_per_request
        # Test hook: bytes of the uncompressed v1 body that cost one request.
        app.state.v1_raw_bytes_per_request = settings.rate_limit_v1_raw_bytes_per_request
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
        expose_headers=[
            "ETag",
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )
    # Outside the caching middleware: the ETag is computed on the uncompressed body, so it
    # does not depend on the encoding (see app.api.caching).
    app.add_middleware(
        GZipMiddleware,
        minimum_size=GZIP_MINIMUM_SIZE,
        compresslevel=GZIP_COMPRESS_LEVEL,
        exclude_content_types=(*DEFAULT_EXCLUDED_CONTENT_TYPES, "image/*"),
    )
    if settings.rate_limit_enabled:
        # Outside GZip: v1 responses are charged by the bytes on the wire (after compression).
        app.add_middleware(ResponseSizeMiddleware)
    app.add_middleware(VaryAcceptEncodingMiddleware)
    register_exception_handlers(app)

    app.include_router(home_router)
    app.include_router(llms_router)
    if not mount_frontend(app, settings.frontend_dist_dir):
        app.include_router(root_home_router)
    app.include_router(health_router)
    app.include_router(legacy_router, prefix="/api/v1")
    app.include_router(legacy_router, prefix="/mars-photos/api/v1", include_in_schema=False)
    app.include_router(v2_router, prefix="/api/v2")
    if settings.admin_enabled:
        app.include_router(admin_router, prefix="/api/v2")
        if settings.api_keys_enabled:
            app.include_router(accounts_router, prefix="/api/v2")
    return app


def _setup_api_keys(app: FastAPI, settings: Settings) -> None:
    """Key lookup with its cache, usage counters and the account service.

    Lookups use the API engine (statement timeout); counters are written on the worker one.
    """
    resolver = ApiKeyResolver(app.state.session_factory, settings.api_key_cache_seconds)
    app.state.api_key_resolver = resolver
    app.state.api_key_usage = ApiKeyUsage(app.state.worker_session_factory)
    app.state.api_key_service = ApiKeyService(settings, resolver)


def _document_api_key(app: FastAPI) -> None:
    """Describe the optional ``X-Api-Key`` header in the OpenAPI schema (every operation
    accepts it; ``security: [{}, ...]`` marks it optional)."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = original()
            schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
            schemes["ApiKeyHeader"] = {
                "type": "apiKey",
                "in": "header",
                "name": API_KEY_HEADER,
                "description": (
                    "Optional API key (si_ and 32 letters or digits), used when the server has API "
                    "keys enabled (otherwise ignored): the account limits apply instead of the "
                    "per-IP ones. Also accepted as the api_key query parameter; "
                    "the header wins. Wrong keys answer 401 invalid_api_key, revoked ones "
                    "401 api_key_revoked, blocked accounts 403 account_blocked."
                ),
            }
            schema["security"] = [{}, {"ApiKeyHeader": []}]
        return app.openapi_schema or schema

    app.openapi = openapi  # type: ignore[method-assign]


def _setup_key_limiters(app: FastAPI, settings: Settings) -> None:
    """Limiters of requests with a key (per account) and of failed key attempts (per IP)."""
    app.state.key_rate_limiter = RateLimiter(
        [(settings.rate_limit_key_per_minute, 60), (settings.rate_limit_key_per_hour, 3600)],
        max_concurrent=settings.rate_limit_key_concurrent,
    )
    app.state.api_key_auth_limiter = RateLimiter(
        [(settings.api_key_auth_max_failures, settings.api_key_auth_window_minutes * 60)]
    )


def _start_api_key_jobs(app: FastAPI, settings: Settings) -> None:
    """Periodic flush of the usage counters and the daily cleanup."""
    if app.state.api_key_usage is None:
        return
    scheduler = ApiKeyScheduler(app.state.api_key_usage, app.state.worker_session_factory, settings)
    app.state.api_key_scheduler = scheduler
    scheduler.start()


async def _stop_api_key_jobs(app: FastAPI) -> None:
    """Stop the jobs and write the counters that are still in memory."""
    scheduler = app.state.api_key_scheduler
    if scheduler is not None:
        app.state.api_key_scheduler = None
        await scheduler.shutdown()


def _default_adapter_factory(app: FastAPI, settings: Settings) -> AdapterFactory:
    """Real adapters over the shared upstream client created in the lifespan."""

    def factory(rover_id: str) -> SourceAdapter:
        client: UpstreamClient | None = app.state.upstream_client
        if client is None:
            raise RuntimeError("The upstream client is not running (application not started)")
        return build_adapter(rover_id, client, settings)

    return factory


def _alert_monitor(app: FastAPI, settings: Settings) -> AlertMonitor | None:
    """Telegram alerts, if configured; sync results reach it through the sync service."""
    notifier = TelegramNotifier.from_settings(settings)
    if notifier is None:
        return None
    monitor = AlertMonitor(app.state.worker_session_factory, settings, notifier)
    app.state.sync_service.add_listener(monitor.on_sync_finished)
    return monitor


def _start_alerts(app: FastAPI, settings: Settings) -> None:
    """Periodic freshness check (independent of SYNC_ENABLED)."""
    if app.state.alert_monitor is None:
        return
    app.state.alert_scheduler = AlertScheduler(app.state.alert_monitor, settings)
    app.state.alert_scheduler.start()


def _stop_alerts(app: FastAPI) -> None:
    """Stop scheduling checks; ``shutdown(wait=False)`` does not wait for a running check
    (it may still be querying while the engine is disposed; its errors are only logged).
    """
    if app.state.alert_scheduler is not None:
        app.state.alert_scheduler.shutdown()
        app.state.alert_scheduler = None


def _start_scheduler(app: FastAPI, settings: Settings) -> None:
    """Start the periodic sync on the shared client and service."""
    locations = LocationSyncService(app.state.worker_engine, app.state.upstream_client, settings)
    app.state.location_sync_service = locations
    samples = SampleSyncService(app.state.worker_engine, app.state.upstream_client, settings)
    app.state.sample_sync_service = samples
    app.state.scheduler = SyncScheduler(
        app.state.sync_service, app.state.upstream_client, settings, locations, samples
    )
    app.state.scheduler.start()


async def _stop_converter(app: FastAPI) -> None:
    """Cancel running conversions and close the converter's own upstream client."""
    aclose = getattr(app.state.mer_image_service, "aclose", None)
    if aclose is not None:
        await aclose()


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
