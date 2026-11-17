"""Rover synchronization: fetch a sol range from the source and store it.

See docs/ARCHITECTURE.md, "Синхронизация".
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
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
from app.repositories import sync_runs as runs_repo
from app.sources.base import PhotoRecord, SourceAdapter
from app.sources.http import UpstreamError

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


@dataclass
class _Progress:
    photos_upserted: int = 0
    stored_sols: int = 0
    failed_sols: set[int] = field(default_factory=set)
    last_error: BaseException | None = None

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


class SyncService:
    """Syncs a rover; one sync per rover at a time (Postgres advisory lock)."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

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
        mode = resolve_mode(from_sol, to_sol)
        rover = await self._load_rover(adapter.rover_id)
        async with self._advisory_lock(rover):
            return await self._run_locked(adapter, rover.id, mode, from_sol, to_sol)

    async def _load_rover(self, rover_id: str) -> Rover:
        async with self._session_factory() as session:
            rover = await rovers_repo.get_rover(session, rover_id)
        if rover is None:
            raise ValueError(f"Unknown rover: {rover_id}")
        return rover

    @asynccontextmanager
    async def _advisory_lock(self, rover: Rover) -> AsyncIterator[None]:
        """Hold a session-level advisory lock on a dedicated connection."""
        params = {"ns": SYNC_LOCK_NAMESPACE, "key": rover.legacy_id}
        async with self._engine.connect() as conn:
            acquired = await conn.scalar(text("SELECT pg_try_advisory_lock(:ns, :key)"), params)
            # The lock is session-level; do not keep a transaction open meanwhile.
            await conn.commit()
            if not acquired:
                raise SyncInProgress(rover.id)
            try:
                yield
            finally:
                await _unlock(conn, params)

    async def _run_locked(
        self,
        adapter: SourceAdapter,
        rover_id: str,
        mode: Mode,
        from_sol: int | None,
        to_sol: int | None,
    ) -> SyncResult:
        progress = _Progress()
        # Incremental range once it is stored in the run row (known after latest_sol()).
        recorded: tuple[int, int] | None = None
        async with self._session_factory() as session:
            await _abandon_stale_runs(session, rover_id)
            run = await runs_repo.create_run(session, rover_id, mode, from_sol, to_sol)
            await session.commit()
            run_id = run.id
            logger.info(
                "Sync started run_id=%d rover=%s mode=%s range=%s..%s",
                run_id,
                rover_id,
                mode,
                from_sol,
                to_sol,
            )
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
    try:
        async for sol, records in adapter.fetch_sols(chunk_from, chunk_to):
            # A sol yielded again (out-of-order feed) and then cut off by a failure is
            # not reported: that needs the failing sol in the adapter exception.
            pending.discard(sol)
            await _store_sol(session, rover_id, sol, records, progress)
    except UpstreamError as exc:
        progress.fail(pending, exc)
        logger.warning(
            "Sync chunk failed rover=%s chunk=%d..%d unfinished_sols=%d error=%s",
            rover_id,
            chunk_from,
            chunk_to,
            len(pending),
            describe_error(exc),
        )


async def _store_sol(
    session: AsyncSession,
    rover_id: str,
    sol: int,
    records: list[PhotoRecord],
    progress: _Progress,
) -> None:
    """Upsert one sol and its summary in its own transaction."""
    try:
        count = await photos_repo.upsert_photos(session, rover_id, records)
        await photos_repo.recompute_sol_summaries(session, rover_id, [sol])
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
