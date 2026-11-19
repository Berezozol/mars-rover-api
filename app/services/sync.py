"""Rover synchronization: fetch a sol range from the source and store it.

See docs/ARCHITECTURE.md, "Синхронизация".
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import SyncInProgress
from app.db.models import Rover
from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories import source_counts as counts_repo
from app.repositories import sync_runs as runs_repo
from app.repositories.source_counts import SourceCountRow
from app.sources.base import FetchStats, PhotoRecord, SolStats, SourceAdapter
from app.sources.http import SolsFetchFailed, UpstreamError

logger = logging.getLogger(__name__)

# First key of the two-int advisory lock; the second one is rovers.legacy_id.
SYNC_LOCK_NAMESPACE = 7_250_001
MAX_ERROR_LENGTH = 2000

Mode = Literal["incremental", "backfill"]
Status = Literal["success", "partial", "failed"]


@dataclass(frozen=True)
class SyncResult:
    run_id: int
    rover_id: str
    mode: str
    # None only when an incremental run failed before its range was known.
    from_sol: int | None
    to_sol: int | None
    status: str
    photos_upserted: int
    failed_sols: list[int]
    error: str | None
    # Total the source declared for the range, when known.
    source_total: int | None = None


SyncListener = Callable[[SyncResult], Awaitable[object]]


@dataclass
class _Progress:
    photos_upserted: int = 0
    stored_sols: int = 0
    failed_sols: set[int] = field(default_factory=set)
    last_error: BaseException | None = None
    # Sum of the totals the source declared per chunk; None once a chunk's is unknown.
    source_total: int | None = 0

    def add_source_total(self, total: int | None) -> None:
        if self.source_total is not None and total is not None:
            self.source_total += total
        else:
            self.source_total = None

    def fail(self, sols: Iterable[int], exc: BaseException) -> None:
        self.failed_sols.update(sols)
        self.last_error = exc

    @property
    def status(self) -> Status:
        if not self.failed_sols:
            return "success"
        if self.photos_upserted > 0 or self.stored_sols > 0:
            return "partial"
        return "failed"

    def error_text(self) -> str | None:
        if not self.failed_sols:
            return None
        message = (
            f"failed sols: {compact_ranges(self.failed_sols)} ; "
            f"last error: {describe_error(self.last_error)}"
        )
        return message[:MAX_ERROR_LENGTH]


def describe_error(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown"
    return f"{type(exc).__name__}: {exc}"


def compact_ranges(sols: Iterable[int]) -> str:
    """Format sols as ascending ranges: {1, 2, 3, 7} -> "1-3, 7"."""
    ranges: list[list[int]] = []
    for sol in sorted(set(sols)):
        if ranges and sol == ranges[-1][1] + 1:
            ranges[-1][1] = sol
        else:
            ranges.append([sol, sol])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


def resolve_mode(from_sol: int | None, to_sol: int | None) -> Mode:
    """Both None -> incremental; both given and valid -> backfill; otherwise ValueError."""
    if from_sol is None and to_sol is None:
        return "incremental"
    if from_sol is None or to_sol is None:
        raise ValueError("from_sol and to_sol must be given together")
    if from_sol < 0 or to_sol < 0:
        raise ValueError(f"Sols must be non-negative, got {from_sol}..{to_sol}")
    if from_sol > to_sol:
        raise ValueError(f"from_sol {from_sol} is greater than to_sol {to_sol}")
    return "backfill"


def chunk_ranges(from_sol: int, to_sol: int, size: int) -> list[tuple[int, int]]:
    """Split [from_sol, to_sol] into descending (chunk_from, chunk_to) pairs of ``size`` sols."""
    return [
        (max(from_sol, chunk_to - size + 1), chunk_to)
        for chunk_to in range(to_sol, from_sol - 1, -size)
    ]


class SyncHandle:
    """A started sync: the lock is held and the run row exists (status ``running``).

    ``wait()`` performs the sync and ALWAYS releases the lock and its connection. Call it
    exactly once; if it is never going to be called, call ``close()`` instead, which marks
    the run failed (as a cancelled run) and releases the lock. ``close()`` after ``wait()``
    has started is a no-op.
    """

    def __init__(
        self,
        service: "SyncService",
        adapter: SourceAdapter,
        lock: "_LockLease",
        plan: "_RunPlan",
    ) -> None:
        self._service = service
        self._adapter = adapter
        self._lock = lock
        self._plan = plan
        self._claimed = False

    @property
    def run_id(self) -> int:
        return self._plan.run_id

    @property
    def rover_id(self) -> str:
        return self._plan.rover_id

    @property
    def mode(self) -> str:
        return self._plan.mode

    @property
    def started(self) -> bool:
        """True once ``wait()`` or ``close()`` has taken over the lock."""
        return self._claimed

    async def wait(self) -> SyncResult:
        """Run the sync to the end; same result and errors as ``SyncService.run()``.

        The service's listeners hear about the outcome after the lock is released, also
        when the sync crashed (as a ``failed`` result); a cancelled sync is not reported.
        """
        self._claim()
        try:
            try:
                result = await self._service._execute(self._adapter, self._plan)
            finally:
                await self._lock.release()
        except Exception as exc:
            await self._service.notify_finished(self._crashed_result(exc))
            raise
        await self._service.notify_finished(result)
        return result

    def _crashed_result(self, exc: Exception) -> SyncResult:
        plan = self._plan
        return SyncResult(
            run_id=plan.run_id,
            rover_id=plan.rover_id,
            mode=plan.mode,
            from_sol=plan.from_sol,
            to_sol=plan.to_sol,
            status="failed",
            photos_upserted=0,
            failed_sols=[],
            error=repr(exc)[:MAX_ERROR_LENGTH],
        )

    async def close(self) -> None:
        """Give up a handle whose ``wait()`` never ran: run failed, lock released."""
        if self._claimed:
            return
        self._claim()
        try:
            await self._service._mark_crashed(
                self._plan.run_id,
                self._plan.rover_id,
                _Progress(),
                [],
                asyncio.CancelledError("sync handle closed before wait()"),
            )
        finally:
            await self._lock.release()

    def _claim(self) -> None:
        if self._claimed:
            raise RuntimeError(f"Sync run {self._plan.run_id} was already waited for or closed")
        self._claimed = True


@dataclass(frozen=True)
class _RunPlan:
    run_id: int
    rover_id: str
    mode: Mode
    from_sol: int | None
    to_sol: int | None


@dataclass
class _LockLease:
    """A session-level advisory lock held on a dedicated connection."""

    conn: AsyncConnection
    params: dict[str, int]
    released: bool = False

    async def release(self) -> None:
        """Unlock and close the connection; idempotent, never leaves the lock in the pool."""
        if self.released:
            return
        self.released = True
        try:
            await _unlock(self.conn, self.params)
        finally:
            try:
                await self.conn.close()
            except Exception as exc:
                logger.warning("Failed to close the sync lock connection: %s", describe_error(exc))


class SyncService:
    """Syncs a rover; one sync per rover at a time (Postgres advisory lock)."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._listeners: list[SyncListener] = []

    def add_listener(self, listener: "SyncListener") -> None:
        """Call ``listener(result)`` after every sync started by this service ends."""
        self._listeners.append(listener)

    async def notify_finished(self, result: SyncResult) -> None:
        """Run the listeners one by one; their errors are logged, never raised."""
        for listener in self._listeners:
            try:
                await listener(result)
            except Exception:
                logger.exception("Sync listener failed run_id=%d", result.run_id)

    @property
    def _engine(self) -> AsyncEngine:
        return self._session_factory.kw["bind"]

    async def run(
        self,
        adapter: SourceAdapter,
        *,
        from_sol: int | None = None,
        to_sol: int | None = None,
    ) -> SyncResult:
        """Incremental sync without a range, backfill of [from_sol, to_sol] with one.

        Raises ``ValueError`` for an invalid range or an unknown rover and
        ``SyncInProgress`` if a sync of this rover is already running. Upstream
        failures do not raise: they end up in the result status.
        """
        handle = await self.start(adapter, from_sol=from_sol, to_sol=to_sol)
        return await handle.wait()

    async def start(
        self,
        adapter: SourceAdapter,
        *,
        from_sol: int | None = None,
        to_sol: int | None = None,
    ) -> SyncHandle:
        """Validate, take the rover lock and create the run row (committed); sync nothing yet.

        Raises like ``run()``. On success the caller owns the returned handle and must
        ``await handle.wait()`` (or ``handle.close()``) to release the lock.
        """
        mode = resolve_mode(from_sol, to_sol)
        rover = await self._load_rover(adapter.rover_id)
        lock = await self._acquire_lock(rover)
        created: list[int] = []
        try:
            run_id = await self._create_run(rover.id, mode, from_sol, to_sol, created)
        except BaseException as exc:
            try:
                if created:
                    # The row is committed: it must not stay "running" without a sync.
                    await self._mark_crashed(
                        created[0], rover.id, _Progress(), [], _start_error(exc)
                    )
            finally:
                await lock.release()
            raise
        plan = _RunPlan(run_id, rover.id, mode, from_sol, to_sol)
        return SyncHandle(self, adapter, lock, plan)

    async def _load_rover(self, rover_id: str) -> Rover:
        async with self._session_factory() as session:
            rover = await rovers_repo.get_rover(session, rover_id)
        if rover is None:
            raise ValueError(f"Unknown rover: {rover_id}")
        return rover

    async def _acquire_lock(self, rover: Rover) -> _LockLease:
        """Take the session-level advisory lock on a dedicated connection."""
        params = {"ns": SYNC_LOCK_NAMESPACE, "key": rover.legacy_id}
        conn = await self._engine.connect()
        try:
            acquired = await conn.scalar(text("SELECT pg_try_advisory_lock(:ns, :key)"), params)
            # The lock is session-level; do not keep a transaction open meanwhile.
            await conn.commit()
        except BaseException:
            # The lock may be held already: the connection must not return to the pool.
            with suppress(Exception):
                await conn.invalidate()
            with suppress(Exception):
                await conn.close()
            raise
        if not acquired:
            await conn.close()
            raise SyncInProgress(rover.id)
        return _LockLease(conn, params)

    async def _create_run(
        self,
        rover_id: str,
        mode: Mode,
        from_sol: int | None,
        to_sol: int | None,
        created: list[int],
    ) -> int:
        """Insert and commit the run row; its id goes to ``created`` as soon as it is committed."""
        async with self._session_factory() as session:
            await _abandon_stale_runs(session, rover_id)
            run = await runs_repo.create_run(session, rover_id, mode, from_sol, to_sol)
            run_id = run.id
            await session.commit()
            created.append(run_id)
        logger.info(
            "Sync started run_id=%d rover=%s mode=%s range=%s..%s",
            run_id,
            rover_id,
            mode,
            from_sol,
            to_sol,
        )
        return run_id

    async def _execute(self, adapter: SourceAdapter, plan: _RunPlan) -> SyncResult:
        """The sync itself, under the lock taken by ``start()``."""
        rover_id, run_id, mode = plan.rover_id, plan.run_id, plan.mode
        from_sol, to_sol = plan.from_sol, plan.to_sol
        progress = _Progress()
        # Incremental range once it is stored in the run row (known after latest_sol()).
        recorded: tuple[int, int] | None = None
        async with self._session_factory() as session:
            try:
                if mode == "incremental":
                    try:
                        from_sol, to_sol = await self._incremental_range(session, adapter, rover_id)
                    except UpstreamError as exc:
                        return await _fail_without_range(session, rover_id, run_id, exc)
                    await runs_repo.update_run_range(session, run_id, from_sol, to_sol)
                    await session.commit()
                    recorded = (from_sol, to_sol)
                if from_sol is None or to_sol is None:
                    raise RuntimeError("Sync range is unknown after range resolution")
                await self._sync_range(session, adapter, rover_id, from_sol, to_sol, progress)
                return await _finish(session, rover_id, run_id, mode, from_sol, to_sol, progress)
            except BaseException as exc:
                with suppress(Exception):
                    await session.rollback()
                failed = _crashed_failed_sols(mode, recorded, progress)
                await self._mark_crashed(run_id, rover_id, progress, failed, exc)
                raise

    async def _incremental_range(
        self, session: AsyncSession, adapter: SourceAdapter, rover_id: str
    ) -> tuple[int, int]:
        """Up to the latest sol, from the overlap below the lowest of: the stored max sol,
        the latest sol and the sols the previous incremental run failed to sync.
        """
        latest = await adapter.latest_sol()
        rover = await rovers_repo.get_rover(session, rover_id)
        base = rover.max_sol if rover is not None and rover.max_sol is not None else latest
        pending = await runs_repo.pending_failed_sols(session, rover_id)
        lowest = min(base, latest, *pending)
        if pending:
            logger.info(
                "Sync retries failed sols rover=%s sols=%s", rover_id, compact_ranges(pending)
            )
        return max(0, lowest - self._settings.sync_overlap_sols), latest

    async def _sync_range(
        self,
        session: AsyncSession,
        adapter: SourceAdapter,
        rover_id: str,
        from_sol: int,
        to_sol: int,
        progress: _Progress,
    ) -> None:
        for chunk_from, chunk_to in chunk_ranges(from_sol, to_sol, self._settings.sync_chunk_sols):
            await _sync_chunk(session, adapter, rover_id, chunk_from, chunk_to, progress)

    async def _mark_crashed(
        self,
        run_id: int,
        rover_id: str,
        progress: _Progress,
        failed_sols: Iterable[int],
        exc: BaseException,
    ) -> None:
        """Record an unexpected exception in a fresh session; never masks the original one."""
        if isinstance(exc, asyncio.CancelledError):
            logger.warning("Sync cancelled run_id=%d rover=%s", run_id, rover_id)
        else:
            logger.error("Sync crashed run_id=%d rover=%s error=%r", run_id, rover_id, exc)
        try:
            async with self._session_factory() as session:
                await runs_repo.finish_run(
                    session,
                    run_id,
                    status="failed",
                    photos_upserted=progress.photos_upserted,
                    error=repr(exc)[:MAX_ERROR_LENGTH],
                    failed_sols=list(failed_sols),
                )
                await session.commit()
        except Exception:
            logger.exception("Failed to mark sync run %d as failed", run_id)


def _start_error(exc: BaseException) -> BaseException:
    """What a run interrupted inside ``start()`` records as its error."""
    if isinstance(exc, asyncio.CancelledError):
        return asyncio.CancelledError("cancelled during start")
    return exc


async def _unlock(conn: AsyncConnection, params: dict[str, int]) -> None:
    """Release the lock; if that fails, close the connection (which releases it too)."""
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), params)
        await conn.commit()
    except BaseException as exc:
        # The connection must never go back to the pool still holding the lock,
        # also when the unlock is interrupted by a cancellation.
        logger.warning(
            "Failed to release sync lock %s (%s); discarding the connection",
            params,
            describe_error(exc),
        )
        with suppress(Exception):
            await conn.invalidate()
        if not isinstance(exc, Exception):
            raise


def _crashed_failed_sols(
    mode: Mode, recorded: tuple[int, int] | None, progress: _Progress
) -> list[int]:
    """Failed sols of a crashed or cancelled run.

    An incremental run with a stored range fails as a whole, so the next incremental
    run (which looks only at the latest finished one) retries the range and older holes
    are not lost. Without a stored range the run is ignored by that lookup anyway.
    """
    if mode == "incremental" and recorded is not None:
        return list(range(recorded[0], recorded[1] + 1))
    return sorted(progress.failed_sols)


async def _abandon_stale_runs(session: AsyncSession, rover_id: str) -> None:
    """Close ``running`` rows left by a dead process; safe because we hold the rover lock."""
    abandoned = await runs_repo.abandon_running_runs(session, rover_id)
    if abandoned:
        logger.warning("Marked %d abandoned sync run(s) as failed rover=%s", abandoned, rover_id)


async def _sync_chunk(
    session: AsyncSession,
    adapter: SourceAdapter,
    rover_id: str,
    chunk_from: int,
    chunk_to: int,
    progress: _Progress,
) -> None:
    """Store every yielded sol; on an upstream failure the not yet yielded sols fail."""
    pending = set(range(chunk_from, chunk_to + 1))
    stream = adapter.fetch_sols(chunk_from, chunk_to)
    stats = _stream_stats(stream)
    try:
        async for sol, records in stream:
            # A sol yielded again (out-of-order feed) and then cut off by a failure is
            # not reported: that needs the failing sol in the adapter exception.
            pending.discard(sol)
            counts = _sol_counts(stats, sol, records)
            await _store_sol(session, rover_id, sol, records, counts, progress)
    except UpstreamError as exc:
        # A failed refetch also names sols that were yielded (and stored) before it.
        incomplete = exc.sols if isinstance(exc, SolsFetchFailed) else frozenset()
        pending.update(sol for sol in incomplete if chunk_from <= sol <= chunk_to)
        progress.fail(pending, exc)
        progress.add_source_total(None)
        logger.warning(
            "Sync chunk failed rover=%s chunk=%d..%d unfinished_sols=%d error=%s",
            rover_id,
            chunk_from,
            chunk_to,
            len(pending),
            describe_error(exc),
        )
        return
    if stats is None or not stats.complete:
        progress.add_source_total(None)
        return
    stored = await _store_final_counts(session, rover_id, stats.sols)
    # Without the chunk's final counts the run total cannot be checked against them.
    progress.add_source_total(_attributed_total(stats, rover_id) if stored else None)


def _attributed_total(stats: FetchStats, rover_id: str) -> int | None:
    """The range total minus images the source gave no (or an out-of-range) sol.

    Those images are in no sol's ``received``; subtracting them keeps the run's recorded
    ``source_total`` comparable with the sum of its sols' ``received`` (reconcile).
    """
    if stats.range_total is None:
        return None
    if stats.unattributed_dropped:
        logger.warning(
            "Source images without a sol in range rover=%s count=%d (not in the run total)",
            rover_id,
            stats.unattributed_dropped,
        )
    return stats.range_total - stats.unattributed_dropped


def _stream_stats(stream: object) -> FetchStats | None:
    """Statistics of a real adapter stream; None for adapters without them."""
    stats = getattr(stream, "stats", None)
    return stats if isinstance(stats, FetchStats) else None


def _sol_counts(stats: FetchStats | None, sol: int, records: list[PhotoRecord]) -> SourceCountRow:
    """Counts of a delivered sol; without adapter stats, what was delivered."""
    sol_stats = stats.sols.get(sol) if stats is not None else None
    if sol_stats is None:
        return SourceCountRow(received=len(records), dropped=0, source_total=None)
    return SourceCountRow(sol_stats.received, sol_stats.dropped, sol_stats.source_total)


async def _store_final_counts(
    session: AsyncSession, rover_id: str, stats: dict[int, SolStats]
) -> bool:
    """Record the final counts of every sol of a finished chunk: also sols that delivered
    nothing (empty or all dropped) and sols a re-yield changed after they were stored.

    False if they could not be written."""
    rows = {
        sol: SourceCountRow(item.received, item.dropped, item.source_total)
        for sol, item in stats.items()
    }
    if not rows:
        return True
    try:
        await counts_repo.upsert_sol_source_counts(session, rover_id, rows)
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.warning(
            "Sync failed to store source counts rover=%s sols=%s error=%s",
            rover_id,
            compact_ranges(rows),
            describe_error(exc),
        )
        return False
    return True


async def _store_sol(
    session: AsyncSession,
    rover_id: str,
    sol: int,
    records: list[PhotoRecord],
    counts: SourceCountRow,
    progress: _Progress,
) -> None:
    """Upsert one sol, its summaries and its source counts in its own transaction."""
    try:
        # Photos the source moved to this sol leave their old sols: recompute those too.
        moved_from = await photos_repo.moved_from_sols(session, rover_id, records)
        count = await photos_repo.upsert_photos(session, rover_id, records)
        await photos_repo.recompute_sol_summaries(session, rover_id, [sol, *moved_from])
        await counts_repo.upsert_sol_source_counts(session, rover_id, {sol: counts})
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        progress.fail([sol], exc)
        logger.warning(
            "Sync failed to store rover=%s sol=%d error=%s", rover_id, sol, describe_error(exc)
        )
        return
    progress.photos_upserted += count
    progress.stored_sols += 1


async def _finish(
    session: AsyncSession,
    rover_id: str,
    run_id: int,
    mode: Mode,
    from_sol: int,
    to_sol: int,
    progress: _Progress,
) -> SyncResult:
    status = progress.status
    error = progress.error_text()
    await rovers_repo.refresh_rover_stats(session, rover_id)
    if status in ("success", "partial"):
        await rovers_repo.touch_last_synced(session, rover_id)
    await runs_repo.finish_run(
        session,
        run_id,
        status=status,
        photos_upserted=progress.photos_upserted,
        error=error,
        failed_sols=progress.failed_sols,
        source_total=progress.source_total,
    )
    await session.commit()
    result = SyncResult(
        run_id=run_id,
        rover_id=rover_id,
        mode=mode,
        from_sol=from_sol,
        to_sol=to_sol,
        status=status,
        photos_upserted=progress.photos_upserted,
        failed_sols=sorted(progress.failed_sols),
        error=error,
        source_total=progress.source_total,
    )
    _log_result(result, progress.stored_sols)
    return result


async def _fail_without_range(
    session: AsyncSession, rover_id: str, run_id: int, exc: UpstreamError
) -> SyncResult:
    """latest_sol() failed: the run ends as failed and its range stays unknown."""
    error = f"latest_sol failed: {describe_error(exc)}"[:MAX_ERROR_LENGTH]
    await runs_repo.finish_run(session, run_id, status="failed", photos_upserted=0, error=error)
    await session.commit()
    result = SyncResult(
        run_id=run_id,
        rover_id=rover_id,
        mode="incremental",
        from_sol=None,
        to_sol=None,
        status="failed",
        photos_upserted=0,
        failed_sols=[],
        error=error,
    )
    _log_result(result, 0)
    return result


def _log_result(result: SyncResult, stored_sols: int) -> None:
    logger.info(
        "Sync finished run_id=%d rover=%s mode=%s range=%s..%s status=%s photos=%d "
        "stored_sols=%d failed_sols=%d",
        result.run_id,
        result.rover_id,
        result.mode,
        result.from_sol,
        result.to_sol,
        result.status,
        result.photos_upserted,
        stored_sols,
        len(result.failed_sols),
    )
