"""Rover waypoints, traverse segments and the state of their source files."""

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from sqlalchemy import Row, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LocationDataset, RoverTraverseSegment, RoverWaypoint
from app.sources.locations import TraverseSegment, WaypointRecord

# asyncpg allows at most 32767 bind parameters per statement (14 per waypoint row).
_WAYPOINT_CHUNK = 1000
_SEGMENT_CHUNK = 500


async def replace_waypoints(
    session: AsyncSession, rover_id: str, items: Sequence[WaypointRecord]
) -> None:
    """Delete the rover's waypoints and insert ``items``; the caller commits (one transaction)."""
    await session.execute(delete(RoverWaypoint).where(RoverWaypoint.rover_id == rover_id))
    for start in range(0, len(items), _WAYPOINT_CHUNK):
        rows = [
            {"rover_id": rover_id, **asdict(item)}
            for item in items[start : start + _WAYPOINT_CHUNK]
        ]
        await session.execute(pg_insert(RoverWaypoint), rows)


async def replace_traverse(
    session: AsyncSession, rover_id: str, items: Sequence[TraverseSegment]
) -> None:
    """Delete the rover's route and insert ``items``; the caller commits (one transaction)."""
    await session.execute(
        delete(RoverTraverseSegment).where(RoverTraverseSegment.rover_id == rover_id)
    )
    for start in range(0, len(items), _SEGMENT_CHUNK):
        rows = [
            {
                "rover_id": rover_id,
                "seq": item.seq,
                "sol": item.sol,
                "length_m": item.length_m,
                "coordinates": [list(point) for point in item.coordinates],
            }
            for item in items[start : start + _SEGMENT_CHUNK]
        ]
        await session.execute(pg_insert(RoverTraverseSegment), rows)


async def get_dataset(session: AsyncSession, rover_id: str, kind: str) -> LocationDataset | None:
    return await session.get(LocationDataset, (rover_id, kind))


async def list_datasets(session: AsyncSession) -> Sequence[LocationDataset]:
    result = await session.scalars(select(LocationDataset))
    return result.all()


async def save_dataset(session: AsyncSession, rover_id: str, kind: str, **fields: Any) -> None:
    """Create the state row if missing and set ``fields`` on it (``ON CONFLICT``)."""
    statement = pg_insert(LocationDataset).values(rover_id=rover_id, kind=kind, **fields)
    if fields:
        statement = statement.on_conflict_do_update(
            index_elements=[LocationDataset.rover_id, LocationDataset.kind], set_=fields
        )
    else:
        statement = statement.on_conflict_do_nothing()
    await session.execute(statement)


async def list_waypoints(
    session: AsyncSession, rover_id: str, from_sol: int | None, to_sol: int | None
) -> Sequence[RoverWaypoint]:
    """Waypoints in driving order, optionally limited to sols ``from_sol..to_sol`` inclusive."""
    query = select(RoverWaypoint).where(RoverWaypoint.rover_id == rover_id)
    if from_sol is not None:
        query = query.where(RoverWaypoint.sol >= from_sol)
    if to_sol is not None:
        query = query.where(RoverWaypoint.sol <= to_sol)
    query = query.order_by(RoverWaypoint.sol, RoverWaypoint.site, RoverWaypoint.drive)
    return (await session.scalars(query)).all()


async def list_traverse(session: AsyncSession, rover_id: str) -> Sequence[Row[Any]]:
    """The whole route as ``(sol, length_m, coordinates)`` rows in driving order."""
    query = (
        select(
            RoverTraverseSegment.sol,
            RoverTraverseSegment.length_m,
            RoverTraverseSegment.coordinates,
        )
        .where(RoverTraverseSegment.rover_id == rover_id)
        .order_by(RoverTraverseSegment.seq)
    )
    return (await session.execute(query)).all()


async def latest_waypoint(session: AsyncSession, rover_id: str) -> RoverWaypoint | None:
    """The last stop in driving order (highest sol, site, drive), or None without stops."""
    query = (
        select(RoverWaypoint)
        .where(RoverWaypoint.rover_id == rover_id)
        .order_by(RoverWaypoint.sol.desc(), RoverWaypoint.site.desc(), RoverWaypoint.drive.desc())
        .limit(1)
    )
    return await session.scalar(query)


async def find_waypoint_for_photo(
    session: AsyncSession, rover_id: str, site: int, drive: int
) -> RoverWaypoint | None:
    """The last waypoint of ``site`` whose drive is not above ``drive`` (by the primary key)."""
    query = (
        select(RoverWaypoint)
        .where(
            RoverWaypoint.rover_id == rover_id,
            RoverWaypoint.site == site,
            RoverWaypoint.drive <= drive,
        )
        .order_by(RoverWaypoint.drive.desc())
        .limit(1)
    )
    return await session.scalar(query)
