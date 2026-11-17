"""Database engine, session factory and the FastAPI session dependency."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, connect_timeout: float = 5.0) -> AsyncEngine:
    """Create the engine. Creating it does not open a connection."""
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"timeout": connect_timeout},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session
