"""SyncService.start() / SyncHandle.wait() (stage 6 contract, section 2).

`start()` validates the arguments, takes the rover lock on its own connection (SyncInProgress
if busy) and commits the run row before returning a handle with `run_id`; `wait()` does the
rest and ALWAYS releases the lock, also on exceptions and cancellation. `run()` = start + wait
and keeps its behaviour (covered by test_sync_service.py).
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import SyncInProgress
from app.services.sync import SYNC_LOCK_NAMESPACE, SyncResult, SyncService
from app.sources import http
from tests.integration._fakes import FakeAdapter, legacy_id, photo_count, sol_records, sync_runs

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]

SessionFactory = async_sessionmaker[AsyncSession]
OVERLAP = 7
CHUNK = 10
WAIT = 10


@pytest.fixture
def service(session_factory: SessionFactory, settings: Settings) -> SyncService:
    return SyncService(
        session_factory,
        settings.model_copy(update={"sync_overlap_sols": OVERLAP, "sync_chunk_sols": CHUNK}),
    )


async def lock_is_free(factory: SessionFactory, rover_id: str) -> bool:
    params = {"ns": SYNC_LOCK_NAMESPACE, "key": await legacy_id(factory, rover_id)}
    async with factory.kw["bind"].connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:ns, :key)"), params)
        if acquired:
            await connection.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), params)
        await connection.commit()
        return bool(acquired)


class HeldLock:
    def __init__(self, factory: SessionFactory, rover_id: str) -> None:
        self.factory = factory
        self.rover_id = rover_id

    async def __aenter__(self) -> "HeldLock":
        self.params = {
            "ns": SYNC_LOCK_NAMESPACE,
            "key": await legacy_id(self.factory, self.rover_id),
        }
        self.connection = await self.factory.kw["bind"].connect()
        await self.connection.execute(text("SELECT pg_advisory_lock(:ns, :key)"), self.params)
        await self.connection.commit()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.connection.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), self.params)
        await self.connection.close()


async def test_start_commits_running_row_holds_lock_and_wait_finishes(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity").add(sol_records("curiosity", 2, 2))

    handle = await service.start(adapter, from_sol=0, to_sol=3)
    try:
        # Visible from another session: the row is committed before wait().
        [row] = await sync_runs(session_factory)
        assert (row["id"], row["status"], row["mode"]) == (handle.run_id, "running", "backfill")
        assert (row["from_sol"], row["to_sol"]) == (0, 3)
        assert adapter.calls == []  # nothing fetched before wait()
        assert not await lock_is_free(session_factory, "curiosity")
    finally:
        result = await asyncio.wait_for(handle.wait(), timeout=WAIT)

    assert isinstance(result, SyncResult)
    assert (result.run_id, result.status, result.photos_upserted) == (handle.run_id, "success", 2)
    [row] = await sync_runs(session_factory)
    assert row["status"] == "success"
    assert await photo_count(session_factory, "curiosity") == 2
    assert await lock_is_free(session_factory, "curiosity")


async def test_start_lock_held_raises_sync_in_progress_without_row(
    service: SyncService, session_factory: SessionFactory
) -> None:
    adapter = FakeAdapter("curiosity", latest=10)

    async with HeldLock(session_factory, "curiosity"):
        with pytest.raises(SyncInProgress):
            await service.start(adapter)

    assert adapter.calls == []
    assert await sync_runs(session_factory) == []


async def test_start_second_start_before_wait_is_rejected(
    service: SyncService, session_factory: SessionFactory
) -> None:
    first = await service.start(FakeAdapter("curiosity"), from_sol=0, to_sol=1)
    try:
        with pytest.raises(SyncInProgress):
            await service.start(FakeAdapter("curiosity"), from_sol=0, to_sol=1)
    finally:
        await asyncio.wait_for(first.wait(), timeout=WAIT)

    assert len(await sync_runs(session_factory)) == 1
    assert await lock_is_free(session_factory, "curiosity")


@pytest.mark.parametrize(
    ("from_sol", "to_sol"),
    [(1, None), (None, 1), (5, 1), (-1, 1)],
    ids=["from-only", "to-only", "from-gt-to", "negative"],
)
async def test_start_invalid_range_raises_value_error_without_row_or_lock(
    service: SyncService, session_factory: SessionFactory, from_sol: int | None, to_sol: int | None
) -> None:
    with pytest.raises(ValueError):
        await service.start(FakeAdapter("curiosity"), from_sol=from_sol, to_sol=to_sol)

    assert await sync_runs(session_factory) == []
    assert await lock_is_free(session_factory, "curiosity")


async def test_wait_exception_marks_failed_releases_lock_and_reraises(
    service: SyncService, session_factory: SessionFactory
) -> None:
    handle = await service.start(
        FakeAdapter("curiosity", fail_at={1: RuntimeError("adapter bug")}), from_sol=0, to_sol=1
    )

    with pytest.raises(RuntimeError, match="adapter bug"):
        await asyncio.wait_for(handle.wait(), timeout=WAIT)

    [row] = await sync_runs(session_factory)
    assert row["status"] == "failed"
    assert "adapter bug" in row["error"]
    assert await lock_is_free(session_factory, "curiosity")


async def test_wait_upstream_failure_is_result_not_exception(
    service: SyncService, session_factory: SessionFactory
) -> None:
    error = http.UpstreamError("https://feed.example.test/", 500, "boom")
    handle = await service.start(FakeAdapter("curiosity", latest_error=error))

    result = await asyncio.wait_for(handle.wait(), timeout=WAIT)

    assert result.status == "failed"
    assert await lock_is_free(session_factory, "curiosity")


async def test_wait_cancelled_incremental_marks_whole_range_failed_and_releases_lock(
    service: SyncService, session_factory: SessionFactory
) -> None:
    entered = asyncio.Event()

    async def hang(from_sol: int, to_sol: int) -> None:
        entered.set()
        await asyncio.Event().wait()

    handle = await service.start(FakeAdapter("curiosity", latest=10, on_fetch=hang))
    task = asyncio.create_task(handle.wait())
    await asyncio.wait_for(entered.wait(), timeout=WAIT)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    [row] = await sync_runs(session_factory)
    assert (row["status"], row["mode"]) == ("failed", "incremental")
    assert (row["from_sol"], row["to_sol"]) == (10 - OVERLAP, 10)
    assert row["failed_sols"] == list(range(10 - OVERLAP, 11))
    assert row["finished_at"] is not None
    assert await lock_is_free(session_factory, "curiosity")


async def test_run_still_returns_result_after_split(
    service: SyncService, session_factory: SessionFactory
) -> None:
    result = await service.run(
        FakeAdapter("curiosity").add(sol_records("curiosity", 1, 1)), from_sol=0, to_sol=1
    )

    assert (result.status, result.photos_upserted) == ("success", 1)
    assert await lock_is_free(session_factory, "curiosity")


async def test_start_cancelled_after_run_row_committed_marks_failed_and_releases_lock(
    service: SyncService, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review MINOR-4: a cancellation that lands inside start() right after the run row is
    committed (e.g. client disconnect, shutdown) must not leave a 'running' row behind.

    Provoked deterministically by wrapping the row creation step of start(): the original
    commits the row, then the cancellation is delivered before start() returns.
    """
    original = SyncService._create_run

    async def create_then_cancel(self: SyncService, *args: object, **kwargs: object) -> int:
        await original(self, *args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(SyncService, "_create_run", create_then_cancel)

    with pytest.raises(asyncio.CancelledError):
        await service.start(FakeAdapter("curiosity"), from_sol=0, to_sol=1)

    monkeypatch.undo()
    [row] = await sync_runs(session_factory)
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert await lock_is_free(session_factory, "curiosity")
