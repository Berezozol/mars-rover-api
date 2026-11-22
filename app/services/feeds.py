"""Read logic of the RSS feed of new photos (docs/API.md, stage 16).

The feed lists photos stored by the regular sync within ``FEED_WINDOW_DAYS`` (``photos.feed_at``).
Archive rovers have no feed. Entries are either one photo each (``group=photo``) or batches:
the photos of one sol stored by one sync transaction (``group=sol``).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models import Photo
from app.repositories import feeds as feeds_repo
from app.repositories import rovers as rovers_repo
from app.services import v2
from app.services.rover_lookup import find_rover
from app.sources.registry import SYNCABLE_ROVERS

FEED_GROUPS: tuple[str, ...] = ("sol", "photo")
FEED_DEFAULT_LIMIT: dict[str, int] = {"sol": 20, "photo": 50}
FEED_MAX_LIMIT = 100
# Thumbnails shown in the description of a batch entry.
FEED_THUMBNAILS = 6
DEFAULT_CREDIT = "NASA/JPL-Caltech"


@dataclass(frozen=True)
class FeedQuery:
    rover: str
    camera: str | None = None
    sample_type: str | None = None
    group: str = "sol"
    # None: the default of the group.
    limit: int | None = None
    # None: Settings.feed_window_days.
    window_days: int | None = None


@dataclass(frozen=True)
class FeedBatch:
    """Photos of one sol stored by one sync transaction."""

    feed_at: datetime
    sol: int
    total: int
    # (camera code, photos) ordered by code.
    cameras: list[tuple[str, int]]
    # Never empty: DEFAULT_CREDIT when the source named no author.
    credits: list[str]
    thumbnails: list[Photo]


@dataclass(frozen=True)
class Feed:
    # FeedBatch items for group=sol, Photo items (cameras loaded) for group=photo.
    items: list[FeedBatch] | list[Photo]
    # Time of the newest entry; None for an empty feed.
    last_modified: datetime | None
    rover: str = ""
    rover_name: str = ""
    group: str = "sol"
    # Normalized filters: camera code in upper case, sample type in lower case.
    camera: str | None = None
    sample_type: str | None = None


async def build_feed(session: AsyncSession, query: FeedQuery, now: datetime | None = None) -> Feed:
    """The feed for the query; unknown rover, archive rover, camera or sample type -> error."""
    rover = await find_rover(session, query.rover)
    if rover.id not in SYNCABLE_ROVERS:
        raise NotFoundError(f"Rover {rover.id} has no feed")
    sample_type = v2.normalize_sample_type(query.sample_type)
    camera = await _camera(session, rover.id, query.camera)
    group = query.group
    limit = _limit(query, group)
    days = query.window_days or get_settings().feed_window_days
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    camera_id = camera[0] if camera else None
    last = await feeds_repo.latest_feed_at(
        session, rover.id, camera_id=camera_id, sample_type=sample_type, since=since
    )
    args: feeds_repo.FeedFilterArgs = {
        "camera_id": camera_id,
        "sample_type": sample_type,
        "since": since,
    }
    if group == "photo":
        items: list[FeedBatch] | list[Photo] = await feeds_repo.feed_photos(
            session, rover.id, limit=limit, **args
        )
    else:
        items = await _batches(session, rover.id, limit, **args)
    return Feed(
        items, last, rover.id, rover.name, group, camera[1] if camera else None, sample_type
    )


def _limit(query: FeedQuery, group: str) -> int:
    limit = query.limit if query.limit is not None else FEED_DEFAULT_LIMIT[group]
    return max(1, min(limit, FEED_MAX_LIMIT))


async def _camera(
    session: AsyncSession, rover_id: str, camera: str | None
) -> tuple[int, str] | None:
    """``(id, code)`` of the filter camera; unknown -> ``InvalidParam`` (as in /photos)."""
    codes = await v2.resolve_camera_codes(session, rover_id, camera)
    if codes is None:
        return None
    for item in await rovers_repo.list_cameras(session, rover_id):
        if item.code == codes[0]:
            return item.id, item.code
    return None


async def _batches(
    session: AsyncSession,
    rover_id: str,
    limit: int,
    *,
    camera_id: int | None,
    sample_type: str | None,
    since: datetime,
) -> list[FeedBatch]:
    rows = await feeds_repo.feed_batches(
        session,
        rover_id,
        camera_id=camera_id,
        sample_type=sample_type,
        since=since,
        limit=limit,
    )
    thumbs = await feeds_repo.batch_thumbnails(
        session,
        rover_id,
        [(row.feed_at, row.sol) for row in rows],
        camera_id=camera_id,
        sample_type=sample_type,
        since=since,
        per_batch=FEED_THUMBNAILS,
    )
    return [
        FeedBatch(
            row.feed_at,
            row.sol,
            row.total,
            row.cameras,
            row.credits or [DEFAULT_CREDIT],
            thumbs.get((row.feed_at, row.sol), []),
        )
        for row in rows
    ]
