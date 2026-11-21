"""Database engine, session factory and the FastAPI session dependency."""

import functools
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, NoReturn, cast

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.errors import DB_IO_ERRORS, DatabaseUnavailable

logger = logging.getLogger(__name__)


def create_engine(
    database_url: str,
    *,
    connect_timeout: float = 5.0,
    statement_timeout: float | None = None,
    pool_timeout: float | None = None,
) -> AsyncEngine:
    """Create the engine. Creating it does not open a connection.

    ``statement_timeout`` (seconds; None or 0 = unlimited) is set on every connection of the
    engine: only the engine serving HTTP requests gets one, the sync, backfill and loaders
    run on engines without it. ``pool_timeout`` is how long a request waits for a free
    connection (None = the SQLAlchemy default of 30 s).
    """
    connect_args: dict[str, Any] = {"timeout": connect_timeout}
    if statement_timeout:
        # No parallel workers for request queries: one slow request must not take several cores.
        connect_args["server_settings"] = {
            "statement_timeout": str(int(statement_timeout * 1000)),
            "max_parallel_workers_per_gather": "0",
        }
    pool_args: dict[str, Any] = {} if pool_timeout is None else {"pool_timeout": pool_timeout}
    return create_async_engine(
        database_url, pool_pre_ping=True, connect_args=connect_args, **pool_args
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def _raise_unavailable(exc: BaseException) -> NoReturn:
    logger.warning("Database unavailable error=%s", type(exc).__name__)
    raise DatabaseUnavailable() from exc


async def guard_db_io[T](awaitable: Awaitable[T]) -> T:
    """Await database work; a raw OSError / TimeoutError becomes ``DatabaseUnavailable``."""
    try:
        return await awaitable
    except DB_IO_ERRORS as exc:
        _raise_unavailable(exc)


@asynccontextmanager
async def db_io_errors() -> AsyncIterator[None]:
    """Block form of ``guard_db_io`` for code that opens its own sessions or connections.

    Only for blocks that do database work alone: any raw I/O error inside becomes a 503.
    """
    try:
        yield
    except DB_IO_ERRORS as exc:
        _raise_unavailable(exc)


class _GuardedSession:
    """Session proxy: raw I/O errors raised by ITS operations become ``DatabaseUnavailable``;
    the same errors raised by other code in the request stay what they are (a 500).

    Guarded: method calls (and the coroutines they return) and ``async with session``.
    Objects that are not coroutines (``session.begin()`` contexts, results) are returned
    as they are, so I/O inside them is not converted: ``async with session.begin()``,
    ``session.connection()`` once its connection is used, ``stream()`` iteration. Such
    errors fail safe as a 500.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Any) -> None:
        object.__setattr__(self, "_session", session)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._session, name)
        if not callable(attr):
            return attr
        return _guarded_call(attr)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._session, name, value)

    async def __aenter__(self) -> "_GuardedSession":
        await guard_db_io(self._session.__aenter__())
        return self

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await guard_db_io(self._session.__aexit__(*exc_info))


def _guarded_call(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            result = method(*args, **kwargs)
        except DB_IO_ERRORS as exc:
            _raise_unavailable(exc)
        # Only coroutines: awaitable context managers such as session.begin() pass through.
        if inspect.iscoroutine(result):
            return guard_db_io(result)
        return result

    return call


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Session for one request; its connection errors map to 503 ``db_unavailable``.

    Exceptions raised by the endpoint are thrown in at ``yield`` and pass through unchanged,
    so an OSError from non-database code stays a 500.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    endpoint_error: BaseException | None = None
    try:
        async with session_factory() as session:
            try:
                yield cast(AsyncSession, _GuardedSession(session))
            except BaseException as exc:
                endpoint_error = exc
                raise
    except DB_IO_ERRORS as exc:
        if exc is endpoint_error:
            raise
        _raise_unavailable(exc)
