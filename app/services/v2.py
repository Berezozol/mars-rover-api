"""Read logic of API v2 (docs/API.md).

v2 serves every sample type, the real capture date (``photos.earth_date``) and exact camera
codes. Parameters arrive already typed and range-checked by the router; this module checks
what needs the database (rover, camera codes) and combinations of parameters.
"""

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParam, PhotoNotFound, RoverNotFound
from app.db.models import Camera, Photo, Rover, SolSummary
from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories.photos import PhotoFilter

# Values for the ``sample_type`` filter; other source values are only visible unfiltered.
SAMPLE_TYPES: tuple[str, ...] = ("full", "subframe", "downsampled")

# Photo ids are bigint; anything outside cannot exist and is answered without a query.
_MAX_INT8 = 2**63 - 1

# Rover slugs are Latin letters only; anything else never reaches the database.
_ROVER_SLUG_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class PageRequest:
    page: int
    per_page: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


@dataclass(frozen=True)
class RoverInfo:
    rover: Rover
    cameras_count: int


@dataclass(frozen=True)
class CameraInfo:
    camera: Camera
    total_photos: int


@dataclass(frozen=True)
class PhotoQuery:
    sol: int | None = None
    earth_date: date | None = None
    camera: str | None = None
    sample_type: str | None = None


@dataclass(frozen=True)
class PhotoPage:
    items: list[Photo]
    page: int
    per_page: int
    total: int


@dataclass(frozen=True)
class LatestPhotos:
    sol: int | None
    photos: PhotoPage


@dataclass(frozen=True)
class Manifest:
    rover: RoverInfo
    sols: list[SolSummary]


async def list_rovers(session: AsyncSession) -> list[RoverInfo]:
    """All rovers ordered by slug, with their camera counts (two queries)."""
    rovers = await rovers_repo.list_rovers(session)
    counts = await rovers_repo.count_cameras(session)
    return [RoverInfo(rover, counts.get(rover.id, 0)) for rover in sorted(rovers, key=_slug)]


async def get_rover(session: AsyncSession, slug: str) -> RoverInfo:
    rover = await _find_rover(session, slug)
    counts = await rovers_repo.count_cameras(session, rover.id)
    return RoverInfo(rover, counts.get(rover.id, 0))


async def list_cameras(session: AsyncSession, slug: str) -> list[CameraInfo]:
    """The rover's cameras ordered by code, with photo counts over all sample types."""
    rover = await _find_rover(session, slug)
    rows = await rovers_repo.list_cameras_with_counts(session, rover.id)
    return [CameraInfo(camera, total) for camera, total in rows]


async def search_photos(
    session: AsyncSession, slug: str, query: PhotoQuery, page: PageRequest
) -> PhotoPage:
    """Photos by optional sol / real earth_date / camera / sample type, newest first."""
    if query.sol is not None and query.earth_date is not None:
        raise InvalidParam("earth_date", "sol and earth_date cannot be used together")
    rover = await _find_rover(session, slug)
    flt = PhotoFilter(
        rover_id=rover.id,
        sol=query.sol,
        earth_date=query.earth_date,
        camera_codes=await _camera_codes(session, rover.id, query.camera),
        sample_type=_sample_type(query.sample_type),
    )
    return await _photo_page(session, rover, flt, page)


async def latest_photos(
    session: AsyncSession, slug: str, camera: str | None, page: PageRequest
) -> LatestPhotos:
    """Photos of the rover's highest sol over all sample types; no data → sol None."""
    rover = await _find_rover(session, slug)
    camera_codes = await _camera_codes(session, rover.id, camera)
    if rover.max_sol is None:
        return LatestPhotos(None, PhotoPage([], page.page, page.per_page, 0))
    flt = PhotoFilter(rover_id=rover.id, sol=rover.max_sol, camera_codes=camera_codes)
    return LatestPhotos(rover.max_sol, await _photo_page(session, rover, flt, page))


async def get_photo(session: AsyncSession, photo_id: int) -> Photo:
    """Photo of any sample type; ids outside bigint range → ``PhotoNotFound`` without a query."""
    if not 0 < photo_id <= _MAX_INT8:
        raise PhotoNotFound()
    photo = await photos_repo.get_photo(session, photo_id)
    if photo is None:
        raise PhotoNotFound()
    return photo


async def get_manifest(
    session: AsyncSession, slug: str, from_sol: int | None, to_sol: int | None
) -> Manifest:
    """The rover and its per-sol summaries over all sample types, sol descending."""
    if from_sol is not None and to_sol is not None and from_sol > to_sol:
        raise InvalidParam("from_sol", "from_sol must be less than or equal to to_sol")
    rover = await get_rover(session, slug)
    summaries = await photos_repo.list_sol_summaries(
        session, rover.rover.id, from_sol=from_sol, to_sol=to_sol, descending=True
    )
    return Manifest(rover, summaries)


def _slug(rover: Rover) -> str:
    return rover.id


async def _find_rover(session: AsyncSession, slug: str) -> Rover:
    """Validate the slug before any query; unknown or malformed → ``RoverNotFound``."""
    if not _ROVER_SLUG_RE.fullmatch(slug):
        raise RoverNotFound(slug)
    rover = await rovers_repo.get_rover(session, slug)
    if rover is None:
        raise RoverNotFound(slug)
    return rover


async def _camera_codes(
    session: AsyncSession, rover_id: str, camera: str | None
) -> list[str] | None:
    """The exact camera code (case-insensitive) as a filter; unknown → ``InvalidParam``."""
    if camera is None:
        return None
    codes = sorted(c.code for c in await rovers_repo.list_cameras(session, rover_id))
    # ASCII only: str.upper() maps e.g. "ı" to "I", so "mahlı" would match MAHLI.
    wanted = camera.upper() if camera.isascii() else None
    if wanted is None or wanted not in codes:
        allowed = ", ".join(codes) or "none"
        raise InvalidParam("camera", f"Unknown camera for rover {rover_id}; allowed: {allowed}")
    return [wanted]


def _sample_type(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered not in SAMPLE_TYPES:
        raise InvalidParam("sample_type", f"Must be one of: {', '.join(SAMPLE_TYPES)}")
    return lowered


async def _total(session: AsyncSession, rover: Rover, flt: PhotoFilter) -> int:
    """Total for the filter, read from sol_summaries when they answer it exactly.

    Not rovers.total_photos: that cache is refreshed only when a sync finishes, while photos
    and summaries are committed per sol, so it lags during a sync or after a crash.
    """
    narrowed = flt.earth_date is not None or flt.camera_codes is not None
    if narrowed or flt.sample_type is not None:
        return await photos_repo.count_photos(session, flt)
    if flt.sol is None:
        return await photos_repo.rover_total_photos_from_summaries(session, rover.id)
    return await photos_repo.sol_total_photos(session, rover.id, flt.sol)


async def _photo_page(
    session: AsyncSession, rover: Rover, flt: PhotoFilter, page: PageRequest
) -> PhotoPage:
    """Total first; the list query runs only when the page can hold rows."""
    total = await _total(session, rover, flt)
    items: list[Photo] = []
    if page.offset < total:
        items = await photos_repo.list_photos(
            session, flt, order="newest", limit=page.per_page, offset=page.offset
        )
    return PhotoPage(items, page.page, page.per_page, total)
