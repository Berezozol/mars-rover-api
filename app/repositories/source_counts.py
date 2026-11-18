"""What the source returned per sol (sol_source_counts) and reconciliation reads."""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CameraSolCount, Photo, SolSourceCount, SolSummary


@dataclass(frozen=True)
class SourceCountRow:
    received: int
    dropped: int
    source_total: int | None


async def upsert_sol_source_counts(
    session: AsyncSession, rover_id: str, counts: Mapping[int, SourceCountRow]
) -> None:
    """Overwrite the rows of the given sols with the latest fetch (fetched_at = now)."""
    if not counts:
        return
    rows = [
        {
            "rover_id": rover_id,
            "sol": sol,
            "received": row.received,
            "dropped": row.dropped,
            "source_total": row.source_total,
            "fetched_at": func.now(),
        }
        for sol, row in counts.items()
    ]
    stmt = pg_insert(SolSourceCount).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SolSourceCount.rover_id, SolSourceCount.sol],
        set_={
            name: stmt.excluded[name]
            for name in ("received", "dropped", "source_total", "fetched_at")
        },
    )
    await session.execute(stmt)


async def list_sol_source_counts(session: AsyncSession, rover_id: str) -> list[SolSourceCount]:
    stmt = (
        select(SolSourceCount)
        .where(SolSourceCount.rover_id == rover_id)
        .order_by(SolSourceCount.sol)
    )
    return list(await session.scalars(stmt))


async def stored_photos_by_sol(session: AsyncSession, rover_id: str) -> dict[int, int]:
    """Photo count per sol straight from photos (reconciliation only: a full scan)."""
    stmt = (
        select(Photo.sol, func.count(Photo.id))
        .where(Photo.rover_id == rover_id)
        .group_by(Photo.sol)
    )
    return {sol: int(count) for sol, count in await session.execute(stmt)}


async def summary_counts_by_sol(session: AsyncSession, rover_id: str) -> dict[int, tuple[int, int]]:
    """``sol -> (total_photos, full_photos)`` from sol_summaries."""
    stmt = select(SolSummary.sol, SolSummary.total_photos, SolSummary.full_photos).where(
        SolSummary.rover_id == rover_id
    )
    return {sol: (int(total), int(full)) for sol, total, full in await session.execute(stmt)}


async def camera_counts_total(session: AsyncSession, rover_id: str) -> int:
    stmt = select(func.coalesce(func.sum(CameraSolCount.total_photos), 0)).where(
        CameraSolCount.rover_id == rover_id
    )
    return int(await session.scalar(stmt) or 0)
