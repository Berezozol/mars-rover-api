"""The request session from `get_session` (stage 6 contract, section 4; review MINOR-1).

The dependency yields a guarding proxy: raw OSError / TimeoutError raised by session operations
become `DatabaseUnavailable` (503 db_unavailable), the same errors raised by other code in the
request pass through unchanged. The proxy must still behave as an `AsyncSession`, including
`async with session.begin()` and `begin_nested()`.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import DatabaseUnavailable
from app.db.session import get_session

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]


def fake_request(factory: Factory) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=factory)))


@pytest.fixture
async def request_session(session_factory: Factory) -> AsyncIterator[Any]:
    """The session exactly as an endpoint receives it; the dependency is closed afterwards."""
    dependency = get_session(fake_request(session_factory))
    session = await anext(dependency)
    try:
        yield session
    finally:
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)


async def test_session_begin_context_manager_works_through_proxy(request_session: Any) -> None:
    async with request_session.begin():
        assert request_session.in_transaction()
        assert await request_session.scalar(text("SELECT 1")) == 1

    assert not request_session.in_transaction()


async def test_session_begin_nested_context_manager_works_through_proxy(
    request_session: Any,
) -> None:
    async with request_session.begin():
        await request_session.execute(text("CREATE TEMP TABLE guard_probe (n int) ON COMMIT DROP"))
        await request_session.execute(text("INSERT INTO guard_probe VALUES (1)"))
        with pytest.raises(RuntimeError, match="undo"):
            async with request_session.begin_nested():
                assert request_session.in_nested_transaction()
                await request_session.execute(text("INSERT INTO guard_probe VALUES (2)"))
                raise RuntimeError("undo the savepoint")
        # Only the savepoint was rolled back.
        assert await request_session.scalar(text("SELECT count(*) FROM guard_probe")) == 1


async def test_session_awaited_begin_and_commit_work_through_proxy(request_session: Any) -> None:
    await request_session.begin()
    assert await request_session.scalar(text("SELECT 2")) == 2
    await request_session.commit()

    assert not request_session.in_transaction()


@pytest.mark.parametrize(
    "error",
    [OSError("connection reset"), ConnectionResetError("reset"), TimeoutError("timed out")],
    ids=["oserror", "connection-reset", "timeout"],
)
@pytest.mark.parametrize("method", ["execute", "scalar", "commit"])
async def test_session_operation_io_error_becomes_db_unavailable(
    request_session: Any, monkeypatch: pytest.MonkeyPatch, error: BaseException, method: str
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(AsyncSession, method, fail)

    call = getattr(request_session, method)
    with pytest.raises(DatabaseUnavailable):
        await (call() if method == "commit" else call(text("SELECT 1")))


async def test_session_begin_io_error_becomes_db_unavailable(
    request_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("connection reset")

    monkeypatch.setattr(AsyncSession, "execute", fail)

    with pytest.raises(DatabaseUnavailable):
        async with request_session.begin():
            await request_session.execute(text("SELECT 1"))


@pytest.mark.parametrize(
    "error", [OSError("disk is full"), TimeoutError("upstream")], ids=["oserror", "timeout"]
)
async def test_endpoint_io_error_passes_through_dependency_unchanged(
    session_factory: Factory, error: BaseException
) -> None:
    """An error raised by the endpoint itself is thrown in at yield and must stay as it is."""
    dependency = get_session(fake_request(session_factory))
    await anext(dependency)

    with pytest.raises(type(error)) as exc_info:
        await dependency.athrow(error)

    assert exc_info.value is error
