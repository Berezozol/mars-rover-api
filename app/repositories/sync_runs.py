"""Sync run journal."""

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import Integer, and_, case, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SyncRun
from app.repositories._common import FRESH

RUNNING = "running"
ABANDONED_ERROR = "abandoned: process ended before the run finished"


async def create_run(
    session: AsyncSession,
    rover_id: str,
    mode: Literal["incremental", "backfill"],
    from_sol: int | None,
    to_sol: int | None,
) -> SyncRun:
    """Add a run in status ``running``; the row is flushed so its id is known."""
    run = SyncRun(rover_id=rover_id, mode=mode, from_sol=from_sol, to_sol=to_sol, status=RUNNING)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def finish_run(
    session: AsyncSession,
    run_id: int,
    *,
    status: Literal["success", "partial", "failed"],
    photos_upserted: int,
    error: str | None,
    failed_sols: Sequence[int] = (),
) -> None:
    stmt = (
        update(SyncRun)
        .where(SyncRun.id == run_id)
        .values(
            status=status,
            photos_upserted=photos_upserted,
            error=error,
            failed_sols=sorted(set(failed_sols)),
            finished_at=func.now(),
        )
    )
    await session.execute(stmt)


async def abandon_running_runs(session: AsyncSession, rover_id: str) -> int:
    """Mark the rover's ``running`` runs as failed; call only while holding the sync lock.

    An incremental run with a known range gets the whole range as failed sols, so the
    next incremental run retries it instead of losing older holes.
    """
    whole_range = literal_column(
        "ARRAY(SELECT generate_series(sync_runs.from_sol, sync_runs.to_sol))", ARRAY(Integer)
    )
    range_known = and_(
        SyncRun.mode == "incremental", SyncRun.from_sol.is_not(None), SyncRun.to_sol.is_not(None)
    )
    stmt = (
        update(SyncRun)
        .where(SyncRun.rover_id == rover_id, SyncRun.status == RUNNING)
        .values(
            status="failed",
            finished_at=func.now(),
            error=ABANDONED_ERROR,
            failed_sols=case((range_known, whole_range), else_=SyncRun.failed_sols),
        )
    )
    result = await session.execute(stmt)
    return result.rowcount or 0  # type: ignore[attr-defined]


async def pending_failed_sols(session: AsyncSession, rover_id: str) -> list[int]:
    """Failed sols of the latest finished incremental run with a known range.

    Empty if there is no such run or it succeeded. Backfill holes are not retried here.
    """
    stmt = (
        select(SyncRun.status, SyncRun.failed_sols)
        .where(
            SyncRun.rover_id == rover_id,
            SyncRun.mode == "incremental",
            SyncRun.from_sol.is_not(None),
            SyncRun.finished_at.is_not(None),
        )
        .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None or row.status == "success":
        return []
    return sorted(row.failed_sols or [])


async def update_run_range(session: AsyncSession, run_id: int, from_sol: int, to_sol: int) -> None:
    stmt = update(SyncRun).where(SyncRun.id == run_id).values(from_sol=from_sol, to_sol=to_sol)
    await session.execute(stmt)


async def list_runs(
    session: AsyncSession,
    *,
    rover_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> list[SyncRun]:
    """Newest runs first."""
    stmt = select(SyncRun)
    if rover_id is not None:
        stmt = stmt.where(SyncRun.rover_id == rover_id)
    stmt = stmt.order_by(SyncRun.started_at.desc(), SyncRun.id.desc()).limit(limit).offset(offset)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def count_runs(session: AsyncSession, *, rover_id: str | None = None) -> int:
    stmt = select(func.count(SyncRun.id))
    if rover_id is not None:
        stmt = stmt.where(SyncRun.rover_id == rover_id)
    return await session.scalar(stmt) or 0
