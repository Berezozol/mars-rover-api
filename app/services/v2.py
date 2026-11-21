"""Read logic of API v2 (docs/API.md).

v2 serves every sample type, the real capture date (``photos.earth_date``) and exact camera
codes. Parameters arrive already typed and range-checked by the router; this module checks
what needs the database (rover, camera codes) and combinations of parameters.
"""

import math
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    VALIDATION_MESSAGE,
    FlightNotFound,
    InvalidParam,
    NotFoundError,
    PhotoNotFound,
    RoverNotFound,
    ValidationAppError,
)
from app.core.overload import HeavyQueryGate
from app.db.models import Camera, IngenuityFlight, Photo, Rover, SolSummary
from app.repositories import flights as flights_repo
from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories import samples as samples_repo
from app.repositories.photos import DEFAULT_SORT, PhotoFilter, PhotoSort
from app.services.locations import PhotoLocation, photo_location
from app.services.rover_lookup import _ROVER_SLUG_RE, find_rover
from app.sources import photo_meta as meta_parser
from app.sources.locations import LOCATION_MISSIONS
from app.sources.photo_meta import LocalTime
from app.sources.registry import SYNCABLE_ROVERS
from app.sources.samples import SAMPLES_ROVER

# Values for the ``sample_type`` filter; other source values are only visible unfiltered.
SAMPLE_TYPES: tuple[str, ...] = ("full", "subframe", "downsampled")

# Every rover, for the ``rovers`` parameter of GET /photos (stage 18B).
ROVER_SLUGS: tuple[str, ...] = ("curiosity", "ingenuity", "opportunity", "perseverance", "spirit")
MAX_ROVERS_LENGTH = 200

# Camera codes per request, and the deepest window (page * per_page) of GET /photos.
MAX_CAMERA_CODES = 10
MAX_RESULT_WINDOW = 10_000

# A rover's sol / date / site range matching at most this many photos is selected before
# sorting (``PhotoFilter.narrow``); see ``_hint_narrow``.
_NARROW_MATCHES = 100_000

# Golden hour in seconds of the local day: 05:30-07:00 and 17:00-18:30.
GOLDEN_HOUR_WINDOWS: tuple[tuple[int, int], ...] = ((19_800, 25_200), (61_200, 66_600))

# Stage 18B-3: rovers with stops (waypoints) to search around, and the default radius.
NEAR_ROVERS: frozenset[str] = frozenset(LOCATION_MISSIONS)
DEFAULT_RADIUS_KM = 1.0

# A coordinate: optional sign, digits, optional fraction; no exponent, "nan" or "inf".
_COORD_RE = re.compile(r"[+-]?[0-9]{1,3}(?:\.[0-9]{1,15})?")

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
    # Inclusive ranges (stage 18B); either end may be open.
    sol_min: int | None = None
    sol_max: int | None = None
    date_min: date | None = None
    date_max: date | None = None
    site_min: int | None = None
    site_max: int | None = None
    sort: PhotoSort = DEFAULT_SORT
    # Stage 18B-2: local solar time window ("HH:MM", already format-checked), golden hour,
    # minimum frame size.
    local_time_min: str | None = None
    local_time_max: str | None = None
    golden_hour: bool = False
    min_width: int | None = None
    min_height: int | None = None
    # Stage 18B-3: "LAT,LON" (format checked by ``parse_near``) and the radius in km.
    near: str | None = None
    radius_km: float | None = None


@dataclass(frozen=True)
class MultiPhotoQuery(PhotoQuery):
    """GET /photos: the filters of one rover's list plus a comma-separated ``rovers``."""

    rovers: str | None = None


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
    session: AsyncSession,
    slug: str,
    query: PhotoQuery,
    page: PageRequest,
    gate: HeavyQueryGate | None = None,
    client: str | None = None,
) -> PhotoPage:
    """Photos of one rover by the query's filters, in the order of ``query.sort``.

    A heavy search (see ``needs_heavy_slot``) takes a slot of ``gate`` for ``client`` after the
    checks that need no database and before the first query (also before the rover lookup);
    with no free slot it fails with ``ServiceBusy``.
    """
    _check_rover_slug(slug)
    _check_query(query)
    if (query.camera is not None and "," in query.camera) or query.near is not None:
        _check_window(page)
    shape = _shape_filter(slug, query)
    async with _heavy_gate(gate, [shape], page, client, query.sort):
        rover = await find_rover(session, slug)
        _check_near_scope(query, [rover.id])
        codes = await _camera_filters(session, [rover.id], query.camera)
        flt = _rover_filter(rover.id, query, None if codes is None else codes[rover.id])
        result = await _photo_page(session, rover, flt, page, query.sort)
    if result.total == 0 and (query.sol is not None or query.earth_date is not None):
        # Intentionally outside the gate: two indexed reads of sol_summaries, only with sol/date.
        before, after = await photos_repo.nearest_sols(
            session, rover.id, sol=query.sol, earth_date=query.earth_date
        )
        result = replace(result, nearest_sols=NearestSols(before, after))
    return result


def _check_rover_slug(slug: str) -> None:
    """Unknown or malformed slug -> ``RoverNotFound`` without the database (before the gate)."""
    if not _ROVER_SLUG_RE.fullmatch(slug) or slug.lower() not in ROVER_SLUGS:
        raise RoverNotFound(slug)


def _shape_filter(rover_id: str, query: PhotoQuery) -> PhotoFilter:
    """The filter of the query without database lookups, for the gate decision only.

    A requested camera is stood in by an empty list: ``classify_total`` looks at whether a
    camera filter is present, not at which cameras.
    """
    return _rover_filter(rover_id, query, [] if query.camera is not None else None)


async def search_photos_multi(
    session: AsyncSession,
    query: MultiPhotoQuery,
    page: PageRequest,
    gate: HeavyQueryGate | None = None,
    client: str | None = None,
) -> PhotoPage:
    """Photos of several rovers (all by default) as one list in the order of ``query.sort``.

    One gate slot for the whole request if any rover's filter is heavy (see ``search_photos``);
    taken before the camera lookup, from the shape of the filters.
    """
    rover_ids = parse_rovers(query.rovers)
    _check_query(query)
    _check_site_scope(query, rover_ids)
    _check_near_scope(query, rover_ids)
    _check_window(page)
    shapes = [_shape_filter(rover_id, query) for rover_id in rover_ids]
    async with _heavy_gate(gate, shapes, page, client, query.sort):
        codes = await _camera_filters(session, rover_ids, query.camera)
        filters = [
            _rover_filter(rover_id, query, None if codes is None else codes[rover_id])
            for rover_id in rover_ids
            if codes is None or codes[rover_id]
        ]
        return await _multi_page(session, filters, query.sort, page)


async def _multi_page(
    session: AsyncSession, filters: list[PhotoFilter], sort: PhotoSort, page: PageRequest
) -> PhotoPage:
    # Totals and the list are separate statements (read committed): a sync committing between
    # them can make the page and ``total`` differ by a few rows; the next request is exact.
    total = 0
    hinted: list[PhotoFilter] = []
    for flt in filters:
        found = await _total(session, flt.rover_id, flt)
        total += found
        if found:
            # A rover without matches adds nothing to the page, and proving it by walking
            # the sort index can read the whole rover.
            hinted.append(_hint_narrow(flt, found))
    items: list[Photo] = []
    if page.offset < total:
        items = await photos_repo.list_photos_multi(
            session, hinted, sort, page.per_page, page.offset
        )
    return PhotoPage(items, page.page, page.per_page, total)


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
    # Perseverance: numbers of the samples sealed in the photo's sol, ascending.
    samples: list[int] = field(default_factory=list)


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
    samples: list[int] = []
    if photo.rover_id == SAMPLES_ROVER:
        samples = await samples_repo.sample_numbers_by_sol(session, photo.rover_id, photo.sol)
    return PhotoDetail(
        photo, location, SolPosition(position, total, newer_id, older_id), flight, samples
    )


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


def _check_window(page: PageRequest) -> None:
    """Deep pages are capped: every skipped row is read from an index."""
    if page.page * page.per_page > MAX_RESULT_WINDOW:
        raise InvalidParam(
            "page", f"page * per_page must not exceed {MAX_RESULT_WINDOW}; narrow the filters"
        )


def parse_rovers(value: str | None) -> list[str]:
    """Slugs of the ``rovers`` parameter: case-insensitive, repeats collapsed; None → all."""
    if value is None:
        return list(ROVER_SLUGS)
    message = f"Must be a comma-separated list of rovers: {', '.join(ROVER_SLUGS)}"
    if len(value) > MAX_ROVERS_LENGTH:
        raise InvalidParam("rovers", message)
    slugs: list[str] = []
    for part in value.split(","):
        # ASCII only: str.lower() would map e.g. "K" (Kelvin sign) onto a Latin letter.
        slug = part.lower() if part.isascii() else ""
        if slug not in ROVER_SLUGS:
            raise InvalidParam("rovers", message)
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def _check_query(query: PhotoQuery) -> None:
    """Combinations of filters that need no database."""
    if query.sol is not None and query.earth_date is not None:
        raise InvalidParam("earth_date", "sol and earth_date cannot be used together")
    _check_drive_range(query)
    has_sol_range = query.sol_min is not None or query.sol_max is not None
    has_date_range = query.date_min is not None or query.date_max is not None
    if query.sol is not None and has_sol_range:
        raise InvalidParam("sol", "sol cannot be used with sol_min or sol_max")
    if query.earth_date is not None and has_date_range:
        raise InvalidParam("earth_date", "earth_date cannot be used with date_min or date_max")
    if (query.sol is not None or has_sol_range) and (
        query.earth_date is not None or has_date_range
    ):
        # The first date parameter that was actually sent.
        sent = (("earth_date", query.earth_date), ("date_min", query.date_min))
        field = next((name for name, value in sent if value is not None), "date_max")
        raise InvalidParam(field, "Sol and date filters cannot be used together")
    _check_order(query.sol_min, query.sol_max, "sol_min", "sol_max")
    _check_order(query.date_min, query.date_max, "date_min", "date_max")
    _check_order(query.site_min, query.site_max, "site_min", "site_max")
    _check_local_time(query)
    _check_near(query)
    if query.site is not None and (query.site_min is not None or query.site_max is not None):
        raise InvalidParam("site", "site cannot be used with site_min or site_max")


@lru_cache(maxsize=256)
def parse_near(value: str) -> tuple[float, float]:
    """``LAT,LON`` in degrees → ``(lat, lon)``; anything else is a 422 on ``near``.

    Cached: the request validates ``near`` early and builds the filter later, so each distinct
    value is parsed once (errors are not cached, they are raised again).
    """
    parts = value.split(",")
    message = "near must be LAT,LON in degrees: lat -90..90, lon -180..180, e.g. 18.43,77.44"
    if len(parts) != 2 or any(_COORD_RE.fullmatch(part) is None for part in parts):
        raise InvalidParam("near", message)
    lat, lon = float(parts[0]), float(parts[1])
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        raise InvalidParam("near", message)
    return lat, lon


def _check_near(query: PhotoQuery) -> None:
    if query.near is None:
        if query.radius_km is not None:
            raise InvalidParam("radius_km", "radius_km needs near")
        return
    parse_near(query.near)


def _check_near_scope(query: PhotoQuery, rover_ids: list[str]) -> None:
    """Only a single rover that has stops can be searched around a point."""
    if query.near is not None and (len(rover_ids) != 1 or rover_ids[0] not in NEAR_ROVERS):
        raise InvalidParam("near", "near needs exactly one rover: curiosity or perseverance")


def _near_filter(query: PhotoQuery) -> tuple[float, float, float] | None:
    if query.near is None:
        return None
    lat, lon = parse_near(query.near)
    return lat, lon, DEFAULT_RADIUS_KM if query.radius_km is None else query.radius_km


def _seconds(value: str | None) -> int | None:
    """``HH:MM`` (validated by the router) as seconds of the day."""
    if value is None:
        return None
    hours, minutes = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60


def _check_local_time(query: PhotoQuery) -> None:
    if query.golden_hour and (query.local_time_min or query.local_time_max):
        raise InvalidParam("golden_hour", "golden_hour cannot be used with local_time_min/max")
    if query.local_time_min is not None and query.local_time_min == query.local_time_max:
        raise InvalidParam("local_time_max", "local_time_max must differ from local_time_min")


def _local_time_ranges(query: PhotoQuery) -> tuple[tuple[int, int], ...]:
    """Windows ``[start, end)`` in seconds of the day for the filter; empty: no time filter.

    ``min > max`` wraps over midnight; a single bound leaves the other end open.
    """
    if query.golden_hour:
        return GOLDEN_HOUR_WINDOWS
    low, high = _seconds(query.local_time_min), _seconds(query.local_time_max)
    if low is None and high is None:
        return ()
    if low is None:
        return ((0, high or 0),)
    if high is None:
        return ((low, photos_repo.SECONDS_PER_DAY),)
    if low < high:
        return ((low, high),)
    return ((low, photos_repo.SECONDS_PER_DAY), (0, high))


def _has_meta_filter(flt: PhotoFilter) -> bool:
    return bool(flt.local_time_ranges) or flt.min_width is not None or flt.min_height is not None


def _hint_narrow(flt: PhotoFilter, matches: int) -> PhotoFilter:
    """Mark a range filter that matches few photos, so the rows are selected before sorting.

    Walking the sort index and filtering is fastest when matches are dense, but a range whose
    photos sit far from the start of the sort order (late sols under ``sort=taken_at``, an
    early year under ``-sol``, old stops) makes the planner read the whole rover: 0.3-1 s.
    Selecting and sorting the matches costs about 0.5 ms per thousand rows, so up to
    ``_NARROW_MATCHES`` (about 50 ms) it is the safe choice; wider ranges are dense enough
    that the index walk finds a page quickly. ``matches`` is the already computed total.
    """
    ranged = (
        any(value is not None for value in (flt.sol_min, flt.sol_max, flt.date_min, flt.date_max))
        or flt.site_min is not None
        or flt.site_max is not None
        or _has_meta_filter(flt)
    )
    return replace(flt, narrow=ranged and matches <= _NARROW_MATCHES)


def _check_order(low: object, high: object, low_name: str, high_name: str) -> None:
    if low is not None and high is not None and low > high:  # type: ignore[operator]
        raise InvalidParam(low_name, f"{low_name} must be less than or equal to {high_name}")


def _check_site_scope(query: PhotoQuery, rover_ids: list[str]) -> None:
    """Stop filters name a rover's own stops, so they need exactly one rover."""
    if len(rover_ids) == 1:
        return
    for name in ("site", "site_min", "site_max"):
        if getattr(query, name) is not None:
            raise InvalidParam(name, f"{name} needs exactly one rover in rovers")


def _rover_filter(rover_id: str, query: PhotoQuery, cameras: list[Camera] | None) -> PhotoFilter:
    return PhotoFilter(
        rover_id=rover_id,
        sol=query.sol,
        earth_date=query.earth_date,
        camera_codes=None if cameras is None else [c.code for c in cameras],
        camera_ids=None if cameras is None else [c.id for c in cameras],
        sample_type=_sample_type(query.sample_type),
        site=query.site,
        drive_from=query.drive_from,
        drive_to=query.drive_to,
        sol_min=query.sol_min,
        sol_max=query.sol_max,
        date_min=query.date_min,
        date_max=query.date_max,
        site_min=query.site_min,
        site_max=query.site_max,
        local_time_ranges=_local_time_ranges(query),
        min_width=query.min_width,
        min_height=query.min_height,
        near=_near_filter(query),
    )


async def _camera_filters(
    session: AsyncSession, rover_ids: list[str], camera: str | None
) -> dict[str, list[Camera]] | None:
    """Cameras of the comma-separated ``camera`` per rover (may be empty for a rover).

    Every code must exist for at least one of the rovers; otherwise ``422`` listing the
    allowed codes (by rover when there are several). Matching is case-insensitive, ASCII only.
    """
    if camera is None:
        return None
    wanted = camera.split(",")
    if len(wanted) > MAX_CAMERA_CODES:
        raise InvalidParam("camera", f"At most {MAX_CAMERA_CODES} camera codes")
    upper = [code.upper() if code.isascii() else "" for code in wanted]
    cameras = await rovers_repo.list_cameras_of(session, rover_ids)
    allowed = {rover_id: sorted(c.code for c in found) for rover_id, found in cameras.items()}
    known = {code for codes in allowed.values() for code in codes}
    if any(code not in known for code in upper):
        _raise_unknown_camera(allowed)
    return {
        rover_id: sorted((c for c in found if c.code in upper), key=_camera_code)
        for rover_id, found in cameras.items()
    }


def _camera_code(camera: Camera) -> str:
    return camera.code


def _raise_unknown_camera(allowed: dict[str, list[str]]) -> None:
    if len(allowed) == 1:
        ((rover_id, codes),) = allowed.items()
        raise InvalidParam(
            "camera", f"Unknown camera for rover {rover_id}; allowed: {', '.join(codes) or 'none'}"
        )
    details = [
        {
            "field": "camera",
            "message": f"Unknown camera code; allowed for {rover_id}: {', '.join(codes) or 'none'}",
        }
        for rover_id, codes in allowed.items()
    ]
    raise ValidationAppError(VALIDATION_MESSAGE, details=details)


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


# Shared with the RSS feed service, which validates the same filters.
resolve_camera_codes = _camera_codes
normalize_sample_type = _sample_type


class TotalSource(StrEnum):
    """Where the ``total`` of a filter comes from; also how heavy the search is."""

    # Read from sol_summaries / camera_sol_counts: no scan of photos.
    SUMMARY = "summary"
    # Counted in photos from a narrow slice (one sol or day) or index-only: cheap.
    SCAN_LIGHT = "scan_light"
    # Counted over the rover's photos (heap scan): the search takes a gate slot.
    SCAN_HEAVY = "scan_heavy"


def _is_full_or_any(flt: PhotoFilter) -> bool:
    return flt.sample_type is None or flt.sample_type.lower() == photos_repo.FULL_SAMPLE_TYPE


def _answered_by_summaries(flt: PhotoFilter) -> bool:
    """True when sol_summaries / camera_sol_counts give the exact total of the filter."""
    stops = flt.site is not None or flt.site_min is not None or flt.site_max is not None
    dates = flt.date_min is not None or flt.date_max is not None
    if stops or dates or flt.near is not None or _has_meta_filter(flt):
        return False
    if flt.sol_min is not None or flt.sol_max is not None:
        return _is_full_or_any(flt)
    by_date = flt.sol is not None or flt.earth_date is not None
    if flt.camera_codes is not None:
        # Camera alone, or camera + full: summed from camera_sol_counts.
        return not by_date and _is_full_or_any(flt)
    if flt.earth_date is not None:
        return False
    if flt.sample_type is not None:
        # Only "full" without a sol (summed full_photos); a sol with a type is counted.
        return _is_full_or_any(flt) and flt.sol is None
    return True


def classify_total(flt: PhotoFilter) -> TotalSource:
    """The source of ``total`` for one rover's filter, from its shape alone (no database).

    ``_total`` follows it; a search with a ``SCAN_HEAVY`` filter takes a gate slot. A ``sol`` or
    ``earth_date`` pins the search to one sol or day, so it is at most ``SCAN_LIGHT``. Stops and
    ``near`` are index-only over ``ix_photos_near_cover`` (which includes ``sol`` and
    ``sample_type``), so they are light with ``sample_type``, heavy with a camera, dates or
    local time / frame size. Anything else counted in photos is heavy: dates, local time, frame
    size, a ``sample_type`` other than ``full`` (also with a sol range or a camera).
    """
    if _answered_by_summaries(flt):
        return TotalSource.SUMMARY
    if flt.sol is not None or flt.earth_date is not None:
        return TotalSource.SCAN_LIGHT
    located = (
        flt.site is not None
        or flt.site_min is not None
        or flt.site_max is not None
        or flt.near is not None
    )
    if located:
        other = (
            flt.camera_codes is not None
            or flt.camera_ids is not None
            or flt.date_min is not None
            or flt.date_max is not None
            or _has_meta_filter(flt)
        )
        return TotalSource.SCAN_HEAVY if other else TotalSource.SCAN_LIGHT
    return TotalSource.SCAN_HEAVY


async def _summary_total(session: AsyncSession, rover_id: str, flt: PhotoFilter) -> int:
    """Total from the per-sol counts for a filter ``_answered_by_summaries`` accepted."""
    full_only = flt.sample_type is not None
    if flt.camera_codes is not None:
        return await photos_repo.camera_total_photos(
            session,
            rover_id,
            flt.camera_codes,
            full_only=full_only,
            sol_min=flt.sol_min,
            sol_max=flt.sol_max,
        )
    if flt.sol_min is not None or flt.sol_max is not None or full_only:
        return await photos_repo.sol_range_total_photos(
            session, rover_id, flt.sol_min, flt.sol_max, full_only=full_only
        )
    if flt.sol is None:
        return await photos_repo.rover_total_photos_from_summaries(session, rover_id)
    return await photos_repo.sol_total_photos(session, rover_id, flt.sol)


async def _total(session: AsyncSession, rover_id: str, flt: PhotoFilter) -> int:
    """Total for the filter, read from sol_summaries when they answer it exactly
    (``classify_total``).

    Not rovers.total_photos: that cache is refreshed only when a sync finishes, while photos
    and summaries are committed per sol, so it lags during a sync or after a crash.
    """
    if classify_total(flt) is TotalSource.SUMMARY:
        return await _summary_total(session, rover_id, flt)
    return await photos_repo.count_photos(session, flt)


def _taken_at_scan(flt: PhotoFilter, sort: PhotoSort) -> bool:
    """A camera or sample_type with a taken_at sort and no sol / earth_date: no index serves it."""
    if sort not in ("taken_at", "-taken_at") or flt.sol is not None or flt.earth_date is not None:
        return False
    return flt.camera_codes is not None or flt.sample_type is not None


def needs_heavy_slot(
    filters: Sequence[PhotoFilter], page: PageRequest, sort: PhotoSort = DEFAULT_SORT
) -> bool:
    """True when the search takes a gate slot: a ``SCAN_HEAVY`` filter (``classify_total``), a
    deep page (``offset >= MAX_RESULT_WINDOW``, every skipped row is read) or a camera /
    ``sample_type`` with ``sort=taken_at`` / ``-taken_at`` and no sol or earth_date.
    """
    if page.offset >= MAX_RESULT_WINDOW:
        return True
    return any(
        classify_total(flt) is TotalSource.SCAN_HEAVY or _taken_at_scan(flt, sort)
        for flt in filters
    )


@asynccontextmanager
async def _heavy_gate(
    gate: HeavyQueryGate | None,
    filters: Sequence[PhotoFilter],
    page: PageRequest,
    client: str | None = None,
    sort: PhotoSort = DEFAULT_SORT,
) -> AsyncIterator[None]:
    """Hold one gate slot while a heavy search (``needs_heavy_slot``) reads the database.

    One slot per request, whatever the number of rovers. Without a gate (None) nothing is held.
    """
    if gate is None or not needs_heavy_slot(filters, page, sort):
        yield
        return
    async with gate.slot(client):
        yield


async def _photo_page(
    session: AsyncSession,
    rover: Rover,
    flt: PhotoFilter,
    page: PageRequest,
    sort: PhotoSort = DEFAULT_SORT,
) -> PhotoPage:
    """Total first; the list query runs only when the page can hold rows."""
    total = await _total(session, rover.id, flt)
    items: list[Photo] = []
    if page.offset < total:
        items = await photos_repo.list_photos_multi(
            session, [_hint_narrow(flt, total)], sort, page.per_page, page.offset
        )
    return PhotoPage(items, page.page, page.per_page, total)


# --- GET /photos/stats and /photos/batch (stage 18C) ---------------------------------------

STATS_GROUP_BY: tuple[str, ...] = ("rover", "camera", "year", "month")
BATCH_MAX_IDS = 100
BATCH_IDS_MAX_LEN = 2000
_ID_RE = re.compile(r"[0-9]{1,19}")


@dataclass(frozen=True)
class StatsQuery:
    rovers: str | None = None
    camera: str | None = None
    sol_min: int | None = None
    sol_max: int | None = None
    date_min: date | None = None
    date_max: date | None = None
    sample_type: str | None = None
    group_by: str = "rover"


@dataclass(frozen=True)
class PhotoStats:
    group_by: str
    total_photos: int
    sols_with_photos: int
    first_date: date | None
    last_date: date | None
    groups: list[photos_repo.StatsRow]


def _check_stats_query(query: StatsQuery) -> None:
    has_sols = query.sol_min is not None or query.sol_max is not None
    if has_sols and (query.date_min is not None or query.date_max is not None):
        field = "date_min" if query.date_min is not None else "date_max"
        raise InvalidParam(field, "Sol and date filters cannot be used together")
    _check_order(query.sol_min, query.sol_max, "sol_min", "sol_max")
    _check_order(query.date_min, query.date_max, "date_min", "date_max")
    if query.group_by not in STATS_GROUP_BY:
        raise InvalidParam("group_by", f"Must be one of: {', '.join(STATS_GROUP_BY)}")
    if query.sample_type is not None and query.sample_type.lower() != "full":
        raise InvalidParam("sample_type", "Must be: full")


async def _stats_rows(
    session: AsyncSession,
    query: StatsQuery,
    rover_ids: list[str],
    camera_ids: list[int] | None,
    group_by: Literal["rover", "camera", "year", "month"],
) -> list[photos_repo.StatsRow]:
    return await photos_repo.summary_stats(
        session,
        rover_ids,
        camera_ids,
        query.sol_min,
        query.sol_max,
        query.date_min,
        query.date_max,
        query.sample_type is not None,
        group_by,
    )


def _rover_rank(rovers: Sequence[Rover]) -> dict[str, int]:
    return {rover.id: index for index, rover in enumerate(rovers)}


async def photo_stats(session: AsyncSession, query: StatsQuery) -> PhotoStats:
    """Counts by rover / camera / year / month from the per-sol summaries only (no photos)."""
    rover_ids = parse_rovers(query.rovers)
    _check_stats_query(query)
    codes = await _camera_filters(session, rover_ids, query.camera)
    camera_ids = None if codes is None else [c.id for found in codes.values() for c in found]
    group_by: Literal["rover", "camera", "year", "month"] = query.group_by  # type: ignore[assignment]
    groups = await _stats_rows(session, query, rover_ids, camera_ids, group_by)
    # A sol belongs to one rover and one period, so those groups add up to the totals; only
    # camera groups overlap (a sol has several cameras) and need the per-rover query.
    totals = (
        await _stats_rows(session, query, rover_ids, camera_ids, "rover")
        if group_by == "camera"
        else groups
    )
    # SQL orders camera code / period within a rover (stable sort keeps it); rovers by slug.
    rank = _rover_rank(sorted(await rovers_repo.list_rovers(session), key=_slug))
    groups = sorted(groups, key=lambda g: rank.get(g.rover_id, len(rank)))
    return PhotoStats(
        group_by=group_by,
        total_photos=sum(t.total_photos for t in totals),
        sols_with_photos=sum(t.sols_with_photos for t in totals),
        first_date=min((t.first_date for t in totals), default=None),
        last_date=max((t.last_date for t in totals), default=None),
        groups=groups,
    )


def parse_ids(value: str) -> list[int]:
    """``ids`` of GET /photos/batch: 1..100 comma-separated ids of 1..19 digits; repeats
    collapse, first-occurrence order is kept. Values beyond bigint are kept (they end up in
    ``missing``)."""
    message = f"Must be 1 to {BATCH_MAX_IDS} comma-separated ids of 1 to 19 digits"
    if not value or len(value) > BATCH_IDS_MAX_LEN:
        raise InvalidParam("ids", message)
    parts = value.split(",")
    if len(parts) > BATCH_MAX_IDS or any(_ID_RE.fullmatch(part) is None for part in parts):
        raise InvalidParam("ids", message)
    return list(dict.fromkeys(int(part) for part in parts))


async def photos_by_ids(session: AsyncSession, raw_ids: str) -> tuple[list[Photo], list[int]]:
    """Found photos in request order and the ids that were not found (also in request order)."""
    ids = parse_ids(raw_ids)
    candidates = [i for i in ids if 0 < i <= _MAX_INT8]
    found = {p.id: p for p in await photos_repo.get_photos_by_ids(session, candidates)}
    return [found[i] for i in ids if i in found], [i for i in ids if i not in found]
