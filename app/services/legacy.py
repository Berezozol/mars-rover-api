"""Read logic of API v1, compatible with the archived Mars Rover Photos API.

See docs/LEGACY-API.md. v1 serves full-frame photos only - except for the rovers in
``ALL_FRAMES_ROVERS`` (see ``shows_all_frames``) - uses the legacy earth_date (derived from
the sol) and the Curiosity camera groups. Every request loads the rover and
its cameras once; photos come with their camera in the same query.
"""

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cameras import legacy_camera_codes
from app.core.errors import InvalidRoverName, LegacyParamError, PhotoNotFound
from app.core.legacy_dates import legacy_earth_date, legacy_sol_for_date
from app.core.legacy_params import legacy_page_window, parse_legacy_date, parse_legacy_int
from app.db.models import Camera, Photo, Rover, SolSummary
from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories.photos import FULL_SAMPLE_TYPE, PhotoFilter

# Values outside these ranges cannot match any row; they are answered without a query
# (Postgres would reject them as out of range for integer / bigint columns).
_MAX_INT4 = 2**31 - 1
_MAX_INT8 = 2**63 - 1

_LARGE_IMAGE_ROVERS = frozenset({"perseverance"})

# Rovers whose v1 photos, latest_photos, /photos/{id}, manifest and rover counters cover every
# frame type (full, subframe, downsampled), as the old gallery did: an owner-approved deviation
# (docs/LEGACY-API.md, "Где мы сознательно отличаемся"). Every other rover: full frames only.
ALL_FRAMES_ROVERS = frozenset({"spirit", "opportunity"})

# Rover names are slugs; anything else cannot be a rover and never reaches the database
# (e.g. a NUL byte would make Postgres fail).
_ROVER_NAME_RE = re.compile(r"[A-Za-z]+")


def shows_all_frames(rover_id: str) -> bool:
    """True if v1 shows every frame type of the rover, False if full frames only."""
    return rover_id.lower() in ALL_FRAMES_ROVERS


def v1_sample_type(rover_id: str) -> str | None:
    """The sample_type v1 filters photos by; None means any."""
    return None if shows_all_frames(rover_id) else FULL_SAMPLE_TYPE


@dataclass(frozen=True)
class LegacyCounters:
    """Rover-level v1 counters; ``max_date`` is the legacy earth_date of ``max_sol``."""

    max_sol: int | None
    max_date: date | None
    total_photos: int


def legacy_counters(rover: Rover) -> LegacyCounters:
    """The counters v1 shows: over every frame for ``ALL_FRAMES_ROVERS``, else full frames."""
    if not shows_all_frames(rover.id):
        return LegacyCounters(rover.full_max_sol, rover.full_max_date, rover.full_total_photos)
    max_sol = rover.max_sol
    max_date = legacy_earth_date(rover.landing_date, max_sol) if max_sol is not None else None
    return LegacyCounters(max_sol, max_date, rover.total_photos)


@dataclass(frozen=True)
class LegacyRover:
    rover: Rover
    cameras: list[Camera]

    @property
    def counters(self) -> LegacyCounters:
        return legacy_counters(self.rover)


@dataclass(frozen=True)
class LegacyPhotoItem:
    """A photo with its v1 values: ``img_src`` and the legacy (sol-derived) earth_date."""

    photo: Photo
    img_src: str
    earth_date: date


@dataclass(frozen=True)
class LegacyPhotos:
    rover: LegacyRover
    photos: list[LegacyPhotoItem]


@dataclass(frozen=True)
class LegacyPhoto:
    rover: LegacyRover
    photo: LegacyPhotoItem


@dataclass(frozen=True)
class LegacyManifestSol:
    """Manifest entry: v1 counters (see ``shows_all_frames``), legacy earth_date."""

    sol: int
    earth_date: date
    total_photos: int
    cameras: list[str]


@dataclass(frozen=True)
class LegacyManifest:
    rover: Rover
    sols: list[LegacyManifestSol]

    @property
    def counters(self) -> LegacyCounters:
        return legacy_counters(self.rover)


@dataclass(frozen=True)
class _Query:
    """Validated v1 photo query: sol None means "no photos"."""

    sol: int | None
    camera: str | None
    window: tuple[int, int] | None


def legacy_img_src(rover_id: str, photo: Photo) -> str:
    """Perseverance: the large JPG (full image if missing); other rovers: the full image."""
    if rover_id in _LARGE_IMAGE_ROVERS:
        return photo.img_large or photo.img_full
    return photo.img_full


async def list_rovers(session: AsyncSession) -> list[LegacyRover]:
    """All rovers ordered by name, each with its cameras (two queries in total)."""
    rovers = await rovers_repo.list_rovers(session)
    cameras: dict[str, list[Camera]] = {rover.id: [] for rover in rovers}
    for camera in await rovers_repo.list_all_cameras(session):
        cameras.setdefault(camera.rover_id, []).append(camera)
    return [LegacyRover(rover, cameras[rover.id]) for rover in sorted(rovers, key=lambda r: r.name)]


async def get_rover(session: AsyncSession, name: str) -> LegacyRover:
    """Rover by case-insensitive name; unknown → ``InvalidRoverName``."""
    rover = await _find_rover(session, name)
    return LegacyRover(rover, await rovers_repo.list_cameras(session, rover.id))


async def search_photos(
    session: AsyncSession,
    rover_name: str,
    *,
    sol: str | None,
    earth_date: str | None,
    camera: str | None,
    page: str | None,
    per_page: str | None,
) -> LegacyPhotos:
    """``/rovers/{rover}/photos``: by sol (wins) or legacy earth_date; neither → no photos."""
    rover = await get_rover(session, rover_name)
    query = _Query(
        sol=_resolve_sol(rover.rover.landing_date, sol, earth_date),
        camera=camera,
        window=legacy_page_window(page, per_page),
    )
    return _photo_items(rover, await _list_photos(session, rover, query))


async def latest_photos(
    session: AsyncSession,
    rover_name: str,
    *,
    camera: str | None,
    page: str | None,
    per_page: str | None,
) -> LegacyPhotos:
    """``/rovers/{rover}/latest_photos``: photos of the rover's highest v1 sol."""
    rover = await get_rover(session, rover_name)
    query = _Query(
        sol=rover.counters.max_sol,
        camera=camera,
        window=legacy_page_window(page, per_page),
    )
    return _photo_items(rover, await _list_photos(session, rover, query))


async def get_photo(session: AsyncSession, raw_id: str) -> LegacyPhoto:
    """Photo by id; non-integer → 400 "Invalid id"; missing or hidden from v1 → 404."""
    photo_id = parse_legacy_int(raw_id, "id")
    if not 0 < photo_id <= _MAX_INT8:
        raise PhotoNotFound()
    photo = await photos_repo.get_photo(session, photo_id)
    if photo is None or not _visible(photo):
        raise PhotoNotFound()
    rover = await get_rover(session, photo.rover_id)
    return LegacyPhoto(rover, _photo_item(rover.rover, photo))


async def get_manifest(session: AsyncSession, rover_name: str) -> LegacyManifest:
    """Rover counters plus one entry per sol with v1 photos, by sol ascending."""
    rover = await _find_rover(session, rover_name)
    all_frames = shows_all_frames(rover.id)
    summaries = await photos_repo.list_sol_summaries(session, rover.id, full_only=not all_frames)
    return LegacyManifest(
        rover, [_manifest_sol(rover, summary, all_frames) for summary in summaries]
    )


async def _find_rover(session: AsyncSession, name: str) -> Rover:
    """Validate the name before any query; unknown or malformed → ``InvalidRoverName``."""
    if not _ROVER_NAME_RE.fullmatch(name):
        raise InvalidRoverName()
    rover = await rovers_repo.get_rover(session, name)
    if rover is None:
        raise InvalidRoverName()
    return rover


def _photo_item(rover: Rover, photo: Photo) -> LegacyPhotoItem:
    return LegacyPhotoItem(
        photo=photo,
        img_src=legacy_img_src(rover.id, photo),
        earth_date=legacy_earth_date(rover.landing_date, photo.sol),
    )


def _photo_items(rover: LegacyRover, photos: list[Photo]) -> LegacyPhotos:
    return LegacyPhotos(rover, [_photo_item(rover.rover, photo) for photo in photos])


def _visible(photo: Photo) -> bool:
    expected = v1_sample_type(photo.rover_id)
    return expected is None or photo.sample_type == expected


def _manifest_sol(rover: Rover, summary: SolSummary, all_frames: bool) -> LegacyManifestSol:
    return LegacyManifestSol(
        sol=summary.sol,
        earth_date=legacy_earth_date(rover.landing_date, summary.sol),
        total_photos=summary.total_photos if all_frames else summary.full_photos,
        cameras=list(summary.cameras if all_frames else summary.full_cameras),
    )


def _resolve_sol(landing_date: date, sol: str | None, earth_date: str | None) -> int | None:
    """The sol to show; None when nothing was asked or the date maps to no sol."""
    if sol is not None:
        return parse_legacy_int(sol, "sol")
    if earth_date is None:
        return None
    parsed = parse_legacy_date(earth_date)
    if parsed is None:
        raise LegacyParamError("earth_date")
    return legacy_sol_for_date(landing_date, parsed)


async def _list_photos(session: AsyncSession, rover: LegacyRover, query: _Query) -> list[Photo]:
    if query.sol is None or not 0 <= query.sol <= _MAX_INT4:
        return []
    camera_codes = None
    if query.camera is not None:
        known = (camera.code for camera in rover.cameras)
        camera_codes = legacy_camera_codes(rover.rover.id, query.camera, known)
        if not camera_codes:
            return []
    limit, offset = query.window or (None, 0)
    if offset > _MAX_INT8:
        return []
    flt = PhotoFilter(
        rover_id=rover.rover.id,
        sol=query.sol,
        camera_codes=camera_codes,
        sample_type=v1_sample_type(rover.rover.id),
    )
    return await photos_repo.list_photos(session, flt, order="legacy", limit=limit, offset=offset)
