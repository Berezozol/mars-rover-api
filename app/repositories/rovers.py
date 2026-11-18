"""Rovers and cameras."""

from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cameras import camera_full_name
from app.core.legacy_dates import legacy_earth_date
from app.db.models import Camera, CameraSolCount, Rover, SolSummary
from app.repositories._common import FRESH


async def list_rovers(session: AsyncSession) -> list[Rover]:
    stmt = select(Rover).order_by(Rover.legacy_id)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def get_rover(session: AsyncSession, rover_id: str) -> Rover | None:
    """Find a rover by slug, case-insensitively (slugs are stored in lower case)."""
    return await session.get(Rover, rover_id.lower(), populate_existing=True)


async def list_cameras(session: AsyncSession, rover_id: str) -> list[Camera]:
    stmt = select(Camera).where(Camera.rover_id == rover_id).order_by(Camera.sort_order)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def list_all_cameras(session: AsyncSession) -> list[Camera]:
    """Cameras of every rover, ordered by rover and then by sort_order (one query)."""
    stmt = select(Camera).order_by(Camera.rover_id, Camera.sort_order)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def count_cameras(session: AsyncSession, rover_id: str | None = None) -> dict[str, int]:
    """Number of cameras per rover (one grouped query); rovers without cameras are absent."""
    stmt = select(Camera.rover_id, func.count(Camera.id)).group_by(Camera.rover_id)
    if rover_id is not None:
        stmt = stmt.where(Camera.rover_id == rover_id)
    rows = await session.execute(stmt)
    return {row_rover_id: count for row_rover_id, count in rows}


async def list_cameras_with_counts(
    session: AsyncSession, rover_id: str
) -> list[tuple[Camera, int]]:
    """The rover's cameras ordered by code, each with its photo count over all sample types.

    Counts are summed from camera_sol_counts (kept by the sync), not from photos.
    """
    counts = (
        select(
            CameraSolCount.camera_id,
            func.sum(CameraSolCount.total_photos).label("total"),
        )
        .where(CameraSolCount.rover_id == rover_id)
        .group_by(CameraSolCount.camera_id)
        .subquery()
    )
    stmt = (
        select(Camera, func.coalesce(counts.c.total, 0))
        .outerjoin(counts, counts.c.camera_id == Camera.id)
        .where(Camera.rover_id == rover_id)
        .order_by(Camera.code)
    )
    rows = await session.execute(stmt, execution_options=FRESH)
    return [(camera, int(total)) for camera, total in rows]


async def get_or_create_cameras(
    session: AsyncSession, rover_id: str, codes: Iterable[str]
) -> dict[str, Camera]:
    """Return cameras keyed by upper-case code; unknown codes are appended to the order."""
    wanted = list(dict.fromkeys(code.upper() for code in codes))
    if not wanted:
        return {}
    found = await _cameras_by_code(session, rover_id, wanted)
    missing = [code for code in wanted if code not in found]
    if missing:
        await _insert_cameras(session, rover_id, missing)
        found = await _cameras_by_code(session, rover_id, wanted)
    return found


async def _cameras_by_code(
    session: AsyncSession, rover_id: str, codes: list[str]
) -> dict[str, Camera]:
    stmt = select(Camera).where(Camera.rover_id == rover_id, Camera.code.in_(codes))
    return {camera.code: camera for camera in await session.scalars(stmt)}


async def _insert_cameras(session: AsyncSession, rover_id: str, codes: list[str]) -> None:
    # max + i is race-free only because sync holds the per-rover advisory lock (stage 3);
    # a concurrent writer would at worst duplicate a sort_order, never a code.
    max_order_stmt = select(func.max(Camera.sort_order)).where(Camera.rover_id == rover_id)
    max_order: int = await session.scalar(max_order_stmt) or 0
    rows = [
        {
            "rover_id": rover_id,
            "code": code,
            "name": camera_full_name(code),
            "sort_order": max_order + i,
        }
        for i, code in enumerate(codes, start=1)
    ]
    stmt = (
        pg_insert(Camera)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[Camera.rover_id, Camera.code])
    )
    await session.execute(stmt)


async def refresh_rover_stats(session: AsyncSession, rover_id: str) -> None:
    """Recompute the rover's cache fields from sol_summaries (last_synced_at is untouched)."""
    landing_date = await session.scalar(select(Rover.landing_date).where(Rover.id == rover_id))
    if landing_date is None:
        return
    has_full = SolSummary.full_photos > 0
    # earth_date of the highest sol.
    max_date_subquery = (
        select(SolSummary.earth_date)
        .where(SolSummary.rover_id == rover_id)
        .order_by(SolSummary.sol.desc())
        .limit(1)
        .scalar_subquery()
    )
    stats = (
        await session.execute(
            select(
                func.max(SolSummary.sol).label("max_sol"),
                max_date_subquery.label("max_date"),
                func.coalesce(func.sum(SolSummary.total_photos), 0).label("total_photos"),
                func.max(SolSummary.sol).filter(has_full).label("full_max_sol"),
                func.coalesce(func.sum(SolSummary.full_photos), 0).label("full_total_photos"),
            ).where(SolSummary.rover_id == rover_id)
        )
    ).one()
    full_max_sol: int | None = stats.full_max_sol
    await session.execute(
        update(Rover)
        .where(Rover.id == rover_id)
        .values(
            max_sol=stats.max_sol,
            max_date=stats.max_date,
            total_photos=int(stats.total_photos),
            full_max_sol=full_max_sol,
            full_max_date=(
                legacy_earth_date(landing_date, full_max_sol) if full_max_sol is not None else None
            ),
            full_total_photos=int(stats.full_total_photos),
        )
    )


async def touch_last_synced(session: AsyncSession, rover_id: str) -> None:
    stmt = update(Rover).where(Rover.id == rover_id).values(last_synced_at=func.now())
    await session.execute(stmt)
