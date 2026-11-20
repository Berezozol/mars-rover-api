"""Read logic of API v2 (docs/API.md).

v2 serves every sample type, the real capture date (``photos.earth_date``) and exact camera
codes. Parameters arrive already typed and range-checked by the router; this module checks
what needs the database (rover, camera codes) and combinations of parameters.
"""

from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FlightNotFound, InvalidParam, NotFoundError, PhotoNotFound
from app.db.models import Camera, IngenuityFlight, Photo, Rover, SolSummary
from app.repositories import flights as flights_repo
from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories.photos import PhotoFilter
from app.services.locations import PhotoLocation, photo_location
from app.services.rover_lookup import find_rover
from app.sources import photo_meta as meta_parser
from app.sources.photo_meta import LocalTime
from app.sources.registry import SYNCABLE_ROVERS

# Values for the ``sample_type`` filter; other source values are only visible unfiltered.
SAMPLE_TYPES: tuple[str, ...] = ("full", "subframe", "downsampled")

# Sizes of /photos/{id}/image; a missing size falls back to "full".
IMAGE_SIZES: tuple[str, ...] = ("full", "large", "medium", "small")

# Rovers whose full frames are converted from PDS3 products (stage 9a).
MER_ROVERS: frozenset[str] = frozenset({"spirit", "opportunity"})

# The only rover with a flight log.
FLIGHT_ROVER = "ingenuity"

# Flight numbers are smallint; anything outside cannot exist and is answered without a query.
_MAX_FLIGHT = 2**15 - 1

# Photo ids are bigint; anything outside cannot exist and is answered without a query.
_MAX_INT8 = 2**63 - 1


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
    # Stop filter; the drive range needs the site.
    site: int | None = None
    drive_from: int | None = None
    drive_to: int | None = None


@dataclass(frozen=True)
class NearestSols:
    """Nearest sols with photos below / above an empty sol or date; None at the edges."""

    before: int | None
    after: int | None


@dataclass(frozen=True)
class PhotoPage:
    items: list[Photo]
    page: int
    per_page: int
    total: int
    # Set only for a sol / earth_date search with no results.
    nearest_sols: NearestSols | None = None


@dataclass(frozen=True)
class PhotoMeta:
    """Frame size and local solar time derived from the photo's ``extra``."""

    width: int | None
    height: int | None
    local_time: LocalTime | None


def photo_meta(photo: Photo) -> PhotoMeta:
    extra = photo.extra or {}
    size = meta_parser.frame_size(photo.rover_id, extra)
    width, height = size if size is not None else (None, None)
    return PhotoMeta(width, height, meta_parser.local_time(photo.rover_id, extra))


def data_source(rover_id: str) -> Literal["sync", "archive"]:
    """``sync`` for rovers fed by the regular sync, ``archive`` for one-off imports."""
    return "sync" if rover_id in SYNCABLE_ROVERS else "archive"


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
    rover = await find_rover(session, slug)
    counts = await rovers_repo.count_cameras(session, rover.id)
    return RoverInfo(rover, counts.get(rover.id, 0))


async def list_cameras(
    session: AsyncSession, slug: str, sol: int | None = None
) -> list[CameraInfo]:
    """The rover's cameras ordered by code, with photo counts over all sample types.

    With ``sol`` the counts are for that sol only (all cameras are listed, zeros included).
    """
    rover = await find_rover(session, slug)
    if sol is None:
        rows = await rovers_repo.list_cameras_with_counts(session, rover.id)
    else:
        rows = await rovers_repo.list_cameras_with_sol_counts(session, rover.id, sol)
    return [CameraInfo(camera, total) for camera, total in rows]


async def search_photos(
    session: AsyncSession, slug: str, query: PhotoQuery, page: PageRequest
) -> PhotoPage:
    """Photos by optional sol / real earth_date / camera / sample type, newest first."""
    if query.sol is not None and query.earth_date is not None:
        raise InvalidParam("earth_date", "sol and earth_date cannot be used together")
    _check_drive_range(query)
    rover = await find_rover(session, slug)
    flt = PhotoFilter(
        rover_id=rover.id,
        sol=query.sol,
        earth_date=query.earth_date,
        camera_codes=await _camera_codes(session, rover.id, query.camera),
        sample_type=_sample_type(query.sample_type),
        site=query.site,
        drive_from=query.drive_from,
        drive_to=query.drive_to,
    )
    result = await _photo_page(session, rover, flt, page)
    if result.total == 0 and (query.sol is not None or query.earth_date is not None):
        before, after = await photos_repo.nearest_sols(
            session, rover.id, sol=query.sol, earth_date=query.earth_date
        )
        result = replace(result, nearest_sols=NearestSols(before, after))
    return result


async def latest_photos(
    session: AsyncSession,
    slug: str,
    camera: str | None,
    page: PageRequest,
    sample_type: str | None = None,
) -> LatestPhotos:
    """Photos of the rover's highest sol over all sample types; no data → sol None."""
    rover = await find_rover(session, slug)
    camera_codes = await _camera_codes(session, rover.id, camera)
    wanted_type = _sample_type(sample_type)
    if rover.max_sol is None:
        return LatestPhotos(None, PhotoPage([], page.page, page.per_page, 0))
    flt = PhotoFilter(
        rover_id=rover.id, sol=rover.max_sol, camera_codes=camera_codes, sample_type=wanted_type
    )
    return LatestPhotos(rover.max_sol, await _photo_page(session, rover, flt, page))


async def get_photo(session: AsyncSession, photo_id: int) -> Photo:
    """Photo of any sample type; ids outside bigint range → ``PhotoNotFound`` without a query."""
    if not 0 < photo_id <= _MAX_INT8:
        raise PhotoNotFound()
    photo = await photos_repo.get_photo(session, photo_id)
    if photo is None:
        raise PhotoNotFound()
    return photo


@dataclass(frozen=True)
class SolPosition:
    """Place of a photo in its sol in the /photos order (newest first)."""

    position: int | None
    total: int
    newer_id: int | None
    older_id: int | None


@dataclass(frozen=True)
class PhotoDetail:
    photo: Photo
    location: PhotoLocation | None
    in_sol: SolPosition
    # Ingenuity: the flight made in the photo's sol; None for other rovers or sols.
    flight: int | None = None


async def get_photo_detail(
    session: AsyncSession,
    photo_id: int,
    camera: str | None = None,
    sample_type: str | None = None,
) -> PhotoDetail:
    """The photo, where it was taken and its place in the sol (``get_photo`` stays as it is).

    ``camera`` and ``sample_type`` only narrow the photos ``in_sol`` is counted among.
    """
    photo = await get_photo(session, photo_id)
    flt = PhotoFilter(
        rover_id=photo.rover_id,
        sol=photo.sol,
        camera_codes=await _camera_codes(session, photo.rover_id, camera),
        sample_type=_sample_type(sample_type),
    )
    position, total, newer_id, older_id = await photos_repo.sol_position(
        session, flt, photo.taken_at, photo.id, _matches(photo, flt)
    )
    location = await photo_location(session, photo)
    flight = None
    if photo.rover_id == FLIGHT_ROVER:
        flight = await flights_repo.flight_by_sol(session, photo.sol)
    return PhotoDetail(photo, location, SolPosition(position, total, newer_id, older_id), flight)


@dataclass(frozen=True)
class ImageTarget:
    """Where ``/photos/{id}/image`` leads: a redirect URL, or a photo to convert."""

    redirect_url: str | None = None
    convert: Photo | None = None


async def resolve_image(session: AsyncSession, photo_id: int, size: str) -> ImageTarget:
    """Stored URL of the photo's image in ``size`` (case-insensitive), else the full one.

    The size is checked before the database; ids out of range → ``PhotoNotFound``.
    Spirit/Opportunity: ``small`` is the stored preview, every other size is converted from
    the PDS3 product (``source_link``).
    """
    wanted = _image_size(size)
    photo = await get_photo(session, photo_id)
    if photo.rover_id in MER_ROVERS:
        if wanted == "small" and photo.img_full:
            return ImageTarget(redirect_url=photo.img_small or photo.img_full)
        if photo.source_link:
            return ImageTarget(convert=photo)
    by_size = {
        "full": photo.img_full,
        "large": photo.img_large,
        "medium": photo.img_medium,
        "small": photo.img_small,
    }
    return ImageTarget(redirect_url=by_size[wanted] or photo.img_full)


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


async def list_flights(session: AsyncSession, slug: str) -> list[tuple[IngenuityFlight, int]]:
    """All flights of Ingenuity by number, each with the photos of its sol."""
    await _flight_rover(session, slug)
    return await flights_repo.list_flights(session)


async def get_flight(session: AsyncSession, slug: str, number: int) -> tuple[IngenuityFlight, int]:
    """One flight with its photo count; no such flight (``0``, out of range) → 404."""
    await _flight_rover(session, slug)
    if not 1 <= number <= _MAX_FLIGHT:
        raise FlightNotFound()
    found = await flights_repo.get_flight(session, number)
    if found is None:
        raise FlightNotFound()
    return found


async def _flight_rover(session: AsyncSession, slug: str) -> Rover:
    """The rover of a flight endpoint: only Ingenuity has flights."""
    rover = await find_rover(session, slug)
    if rover.id != FLIGHT_ROVER:
        raise NotFoundError(f"Rover {rover.id} has no flights")
    return rover


def _check_drive_range(query: PhotoQuery) -> None:
    has_drive = query.drive_from is not None or query.drive_to is not None
    if has_drive and query.site is None:
        raise InvalidParam("site", "drive_from and drive_to need site")
    if (
        query.drive_from is not None
        and query.drive_to is not None
        and query.drive_from > query.drive_to
    ):
        raise InvalidParam("drive_from", "drive_from must be less than or equal to drive_to")


def _matches(photo: Photo, flt: PhotoFilter) -> bool:
    """Whether the loaded photo passes the camera and sample type filters."""
    if flt.camera_codes is not None and photo.camera.code not in flt.camera_codes:
        return False
    return flt.sample_type is None or photo.sample_type == flt.sample_type.lower()


def _slug(rover: Rover) -> str:
    return rover.id


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


def _image_size(value: str) -> str:
    lowered = value.lower() if value.isascii() else ""
    if lowered not in IMAGE_SIZES:
        raise InvalidParam("size", f"Must be one of: {', '.join(IMAGE_SIZES)}")
    return lowered


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
    if flt.site is not None:
        return await photos_repo.count_photos(session, flt)
    by_date = flt.sol is not None or flt.earth_date is not None
    sample_type = flt.sample_type.lower() if flt.sample_type is not None else None
    full_or_any = sample_type in (None, photos_repo.FULL_SAMPLE_TYPE)
    if flt.camera_codes is not None and not by_date and full_or_any:
        # Camera alone, or camera + full: summed from camera_sol_counts, no photos scan.
        return await photos_repo.camera_total_photos(
            session, rover.id, flt.camera_codes, full_only=sample_type is not None
        )
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
