"""Read logic of the rover position endpoints and the photo card (docs/API.md, stage 11)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParam
from app.db.models import Photo, RoverWaypoint
from app.repositories import locations as locations_repo
from app.repositories._common import AnyRow
from app.services.rover_lookup import find_rover
from app.sources.locations import LOCATION_MISSIONS


@dataclass(frozen=True)
class LocationData:
    rover: str
    # False for rovers without a position source (Spirit, Opportunity).
    available: bool
    updated_at: datetime | None
    source_max_sol: int | None
    items: Sequence[RoverWaypoint]


@dataclass(frozen=True)
class TraverseData:
    rover: str
    available: bool
    updated_at: datetime | None
    source_max_sol: int | None
    # Rows of (sol, length_m, coordinates) in driving order.
    segments: Sequence[AnyRow]


@dataclass(frozen=True)
class PhotoLocation:
    waypoint: RoverWaypoint
    # True when the photo's drive equals the waypoint's drive.
    exact: bool


async def get_location(
    session: AsyncSession,
    slug: str,
    sol: int | None,
    from_sol: int | None,
    to_sol: int | None,
    latest: bool = False,
) -> LocationData:
    """Waypoints of the rover, optionally for one sol or a sol range (inclusive).

    With ``latest`` only the last stop (or none); it excludes ``sol``, ``from_sol``, ``to_sol``.
    """
    if latest and (sol is not None or from_sol is not None or to_sol is not None):
        raise InvalidParam("latest", "latest cannot be used together with sol, from_sol or to_sol")
    if sol is not None and (from_sol is not None or to_sol is not None):
        raise InvalidParam("sol", "sol cannot be used together with from_sol or to_sol")
    if from_sol is not None and to_sol is not None and from_sol > to_sol:
        raise InvalidParam("from_sol", "from_sol must be less than or equal to to_sol")
    rover = await find_rover(session, slug)
    if rover.id not in LOCATION_MISSIONS:
        return LocationData(rover.id, False, None, None, [])
    if sol is not None:
        from_sol = to_sol = sol
    dataset = await locations_repo.get_dataset(session, rover.id, "waypoints")
    if latest:
        last = await locations_repo.latest_waypoint(session, rover.id)
        items: Sequence[RoverWaypoint] = [last] if last is not None else []
    else:
        items = await locations_repo.list_waypoints(session, rover.id, from_sol, to_sol)
    return LocationData(
        rover.id,
        True,
        dataset.changed_at if dataset else None,
        dataset.max_sol if dataset else None,
        items,
    )


async def get_traverse(session: AsyncSession, slug: str) -> TraverseData:
    """The whole route of the rover as stored segments."""
    rover = await find_rover(session, slug)
    if rover.id not in LOCATION_MISSIONS:
        return TraverseData(rover.id, False, None, None, [])
    dataset = await locations_repo.get_dataset(session, rover.id, "traverse")
    segments = await locations_repo.list_traverse(session, rover.id)
    return TraverseData(
        rover.id,
        True,
        dataset.changed_at if dataset else None,
        dataset.max_sol if dataset else None,
        segments,
    )


async def photo_location(session: AsyncSession, photo: Photo) -> PhotoLocation | None:
    """The last waypoint of the photo's site with ``drive`` not above the photo's, if any.

    None without ``site``/``drive``, for rovers without a position source and when the
    site has no waypoint at or before the photo's drive.
    """
    if photo.site is None or photo.drive is None or photo.rover_id not in LOCATION_MISSIONS:
        return None
    waypoint = await locations_repo.find_waypoint_for_photo(
        session, photo.rover_id, photo.site, photo.drive
    )
    if waypoint is None:
        return None
    return PhotoLocation(waypoint, exact=waypoint.drive == photo.drive)
