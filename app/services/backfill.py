"""Full-history backfill: a sol range walked downwards in batches, one sync run per batch.

Batches lie on a fixed grid ``[k*N, k*N+N-1]`` (clipped to the range), so their bounds do
not depend on the latest sol. A batch is skipped when every sol in it is already loaded
(resume, see ``loaded_sols``); the failed sols of this invocation are retried once at the
end. A rover locked by another sync is waited for; consecutive failed batches stop it.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import SyncInProgress
from app.db.models import SyncRun
from app.repositories import sync_runs as runs_repo
from app.services.sync import SyncResult, SyncService, compact_ranges
from app.sources.base import SourceAdapter

logger = logging.getLogger(__name__)

StopReason = Literal["locked", "failed_batches"]
_GOOD_STATUSES = ("success", "partial")


@dataclass
class BackfillResult:
    rover_id: str
    from_sol: int
    to_sol: int
    batches_total: int
    batches_skipped: int = 0
    photos_upserted: int = 0
    runs: list[SyncResult] = field(default_factory=list)
    retry_runs: list[SyncResult] = field(default_factory=list)
    # Sols still failed: after the retry, or collected so far when stopped early.
    failed_sols: list[int] = field(default_factory=list)
    # Set when the backfill stopped early: lock wait exceeded or too many failed batches.
    stopped: StopReason | None = None
    # Sols not attempted yet when stopped early (None if nothing is left).
    remaining: tuple[int, int] | None = None

    @property
    def ok(self) -> bool:
        return self.stopped is None and not self.failed_sols


@dataclass(frozen=True)
class BackfillOptions:
    batch_sols: int = 100
    resume: bool = True
    lock_retry_seconds: float = 30.0
    lock_max_wait_seconds: float = 600.0
    max_failed_batches: int = 3


class BackfillLocked(Exception):
    """The rover stayed locked by another sync longer than the allowed wait."""


def batch_grid(from_sol: int, to_sol: int, size: int) -> list[tuple[int, int]]:
    """Descending batches ``[k*size, k*size+size-1]`` clipped to ``[from_sol, to_sol]``."""
    return [
        (max(from_sol, k * size), min(to_sol, k * size + size - 1))
        for k in range(to_sol // size, from_sol // size - 1, -1)
    ]


def loaded_sols(runs: Sequence[SyncRun], from_sol: int, to_sol: int) -> set[int]:
    """Sols of [from_sol, to_sol] whose most recently finished covering backfill run is
    ``success`` or ``partial`` and does not list the sol as failed.

    ``runs`` must be finished runs ordered most recently finished first.
    """
    decided: set[int] = set()
    loaded: set[int] = set()
    for run in runs:
        if run.from_sol is None or run.to_sol is None:
            continue
        low, high = max(from_sol, run.from_sol), min(to_sol, run.to_sol)
        failed = set(run.failed_sols or ())
        good = run.status in _GOOD_STATUSES
        for sol in range(low, high + 1):
            if sol in decided:
                continue
            decided.add(sol)
            if good and sol not in failed:
                loaded.add(sol)
    return loaded


def contiguous_groups(sols: Iterable[int]) -> list[tuple[int, int]]:
    """Group sols into descending ``(from_sol, to_sol)`` runs of consecutive sols."""
    groups: list[list[int]] = []
    for sol in sorted(set(sols), reverse=True):
        if groups and sol == groups[-1][0] - 1:
            groups[-1][0] = sol
        else:
            groups.append([sol, sol])
    return [(low, high) for low, high in groups]


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s"


class BackfillService:
    """Runs a backfill through ``SyncService`` (same lock, journal and storage)."""

    def __init__(
        self,
        sync_service: SyncService,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sync = sync_service
        self._session_factory = session_factory
        self._clock = clock
        self._sleep = sleep

    async def run(
        self,
        adapter: SourceAdapter,
        *,
        from_sol: int = 0,
        to_sol: int | None = None,
        options: BackfillOptions | None = None,
    ) -> BackfillResult:
        """Backfill [from_sol, to_sol] (to_sol defaults to the source's latest sol).

        Raises ``ValueError`` for an invalid range and ``UpstreamError`` if the latest sol
        cannot be fetched. A lock held too long or too many failed batches end the
        backfill early (``result.stopped``); then the retry phase is skipped. Cancellation
        ends the current run as failed (see ``SyncService``) and propagates.
        """
        opts = options or BackfillOptions()
        if opts.batch_sols < 1:
            raise ValueError(f"batch_sols must be at least 1, got {opts.batch_sols}")
        if to_sol is None:
            to_sol = await adapter.latest_sol()
        if from_sol < 0 or from_sol > to_sol:
            raise ValueError(f"Invalid sol range {from_sol}..{to_sol}")
        batches = batch_grid(from_sol, to_sol, opts.batch_sols)
        loaded = await self._loaded(adapter.rover_id, from_sol, to_sol) if opts.resume else set()
        done = [all(sol in loaded for sol in range(a, b + 1)) for a, b in batches]
        result = BackfillResult(adapter.rover_id, from_sol, to_sol, len(batches))
        try:
            await self._run_batches(adapter, batches, done, opts, result)
            if result.stopped is None:
                await self._retry_failed(adapter, opts, result)
        except BackfillLocked:
            result.stopped = "locked"
        if result.stopped is not None and not result.failed_sols:
            result.failed_sols = sorted({sol for run in result.runs for sol in run.failed_sols})
        return result

    async def _loaded(self, rover_id: str, from_sol: int, to_sol: int) -> set[int]:
        async with self._session_factory() as session:
            runs = await runs_repo.finished_runs(session, rover_id, mode="backfill")
        return loaded_sols(runs, from_sol, to_sol)

    async def _run_batches(
        self,
        adapter: SourceAdapter,
        batches: list[tuple[int, int]],
        done: list[bool],
        opts: BackfillOptions,
        result: BackfillResult,
    ) -> None:
        todo = done.count(False)
        logger.info(
            "Backfill started rover=%s sols=%d..%d batches=%d to_run=%d resume=%s",
            adapter.rover_id,
            result.from_sol,
            result.to_sol,
            len(batches),
            todo,
            opts.resume,
        )
        durations: list[float] = []
        failed_in_row = 0
        for index, (batch, skip) in enumerate(zip(batches, done, strict=True), start=1):
            if skip:
                result.batches_skipped += 1
                logger.info(
                    "Backfill batch %d/%d sols=%d..%d skipped: already loaded",
                    index,
                    len(batches),
                    *batch,
                )
                continue
            # What is left if this batch never gets the lock.
            result.remaining = (result.from_sol, batch[1])
            started = self._clock()
            run = await self._run_locked(adapter, batch, opts)
            durations.append(self._clock() - started)
            result.runs.append(run)
            result.photos_upserted += run.photos_upserted
            eta = sum(durations) / len(durations) * (todo - len(durations))
            logger.info(
                "Backfill batch %d/%d sols=%d..%d status=%s photos=%d cumulative=%d "
                "failed_sols=%d eta=%s",
                index,
                len(batches),
                batch[0],
                batch[1],
                run.status,
                run.photos_upserted,
                result.photos_upserted,
                len(run.failed_sols),
                format_eta(eta),
            )
            failed_in_row = failed_in_row + 1 if run.status == "failed" else 0
            if failed_in_row >= opts.max_failed_batches:
                _stop_failed_batches(result, batch, failed_in_row)
                return
        result.remaining = None

    async def _run_locked(
        self, adapter: SourceAdapter, sols: tuple[int, int], opts: BackfillOptions
    ) -> SyncResult:
        """One backfill run; waits while another sync holds the rover lock.

        Raises ``BackfillLocked`` once ``lock_max_wait_seconds`` have been waited.
        """
        waited = 0.0
        while True:
            try:
                return await self._sync.run(adapter, from_sol=sols[0], to_sol=sols[1])
            except SyncInProgress:
                if waited >= opts.lock_max_wait_seconds:
                    logger.error(
                        "Backfill gave up rover=%s sols=%d..%d: locked for %.0fs",
                        adapter.rover_id,
                        sols[0],
                        sols[1],
                        waited,
                    )
                    raise BackfillLocked from None
                delay = min(opts.lock_retry_seconds, opts.lock_max_wait_seconds - waited)
                logger.warning(
                    "Backfill waiting for the sync lock rover=%s sols=%d..%d "
                    "retry_in=%.0fs waited=%.0fs max_wait=%.0fs",
                    adapter.rover_id,
                    sols[0],
                    sols[1],
                    delay,
                    waited,
                    opts.lock_max_wait_seconds,
                )
                await self._sleep(delay)
                waited += delay

    async def _retry_failed(
        self, adapter: SourceAdapter, opts: BackfillOptions, result: BackfillResult
    ) -> None:
        """Retry the union of failed sols once, one backfill run per contiguous group."""
        failed = {sol for run in result.runs for sol in run.failed_sols}
        if not failed:
            return
        logger.info(
            "Backfill retrying failed sols rover=%s sols=%s",
            adapter.rover_id,
            compact_ranges(failed),
        )
        still_failed: set[int] = set()
        groups = contiguous_groups(failed)
        for position, group in enumerate(groups):
            try:
                run = await self._run_locked(adapter, group, opts)
            except BackfillLocked:
                untried = {sol for low, high in groups[position:] for sol in range(low, high + 1)}
                result.failed_sols = sorted(still_failed | untried)
                raise
            result.retry_runs.append(run)
            result.photos_upserted += run.photos_upserted
            still_failed.update(run.failed_sols)
        result.failed_sols = sorted(still_failed)
        logger.info(
            "Backfill retry finished rover=%s still_failed=%s",
            adapter.rover_id,
            compact_ranges(still_failed) or "none",
        )


def _stop_failed_batches(result: BackfillResult, batch: tuple[int, int], in_row: int) -> None:
    result.stopped = "failed_batches"
    result.remaining = (result.from_sol, batch[0] - 1) if batch[0] > result.from_sol else None
    remaining = "none" if result.remaining is None else "{}..{}".format(*result.remaining)
    logger.error(
        "Backfill stopped rover=%s: %d consecutive failed batches, remaining=%s",
        result.rover_id,
        in_row,
        remaining,
    )
