"""Curiosity thumbnails: match feed thumbnails to stored frames and write ``photos.img_small``.

Only the MSSS cameras (Mastcam, MAHLI, MARDI) get one; see docs/proposals/msl-thumbnails.md.
A thumbnail is matched to the frames of its sol by ``thumbnail_key`` (one thumbnail may belong
to 2-3 frames of one shot). A thumbnail without a stored frame is not kept: the next pass
(sync of the sol or the ``thumbnails`` command) picks it up once the frame exists.
"""

import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import BigInteger, Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Camera, Photo
from app.sources.base import PhotoRecord
from app.sources.http import UpstreamError
from app.sources.msl import Thumbnail, thumbnail_key

logger = logging.getLogger(__name__)

MSL_ROVER_ID = "curiosity"
MSL_THUMBNAIL_CAMERAS = frozenset({"MAST_LEFT", "MAST_RIGHT", "MAHLI", "MARDI"})

# Frames and URLs of one UPDATE statement.
_UPDATE_CHUNK = 5000

_UPDATE = text(
    """
    UPDATE photos AS p
    SET img_small = v.url, updated_at = now()
    FROM unnest(:ids, :urls) AS v(id, url)
    WHERE p.id = v.id AND p.img_small IS DISTINCT FROM v.url
    """
).bindparams(
    bindparam("ids", type_=ARRAY(BigInteger)),
    bindparam("urls", type_=ARRAY(Text)),
)


@dataclass(frozen=True)
class ThumbnailResult:
    """``matched``: frames that found a thumbnail; ``updated``: of them, frames whose
    ``img_small`` changed; ``skipped``: thumbnails with no stored frame (yet)."""

    matched: int = 0
    updated: int = 0
    skipped: int = 0

    def __add__(self, other: "ThumbnailResult") -> "ThumbnailResult":
        return ThumbnailResult(
            self.matched + other.matched, self.updated + other.updated, self.skipped + other.skipped
        )


async def apply_thumbnails(
    session: AsyncSession,
    rover: str,
    sols: Sequence[int],
    thumbnails: Sequence[Thumbnail],
) -> ThumbnailResult:
    """Write the thumbnail URL into ``img_small`` of the matching frames of ``sols``.

    Only frames of ``MSL_THUMBNAIL_CAMERAS`` are touched, and only where the value differs.
    The caller commits. Raises ``ValueError`` for any rover but Curiosity.
    """
    if rover != MSL_ROVER_ID:
        raise ValueError(f"Thumbnails are supported for {MSL_ROVER_ID} only, not {rover!r}")
    if not sols or not thumbnails:
        return ThumbnailResult(skipped=len({t.key for t in thumbnails}))
    by_key = {t.key: t for t in thumbnails}
    frames = await _load_frames(session, rover, sols)
    ids: list[int] = []
    urls: list[str] = []
    seen_keys: set[str] = set()
    for photo_id, imageid in frames:
        key = thumbnail_key(imageid) if imageid else None
        thumbnail = by_key.get(key) if key is not None else None
        if thumbnail is None:
            continue
        seen_keys.add(thumbnail.key)
        ids.append(photo_id)
        urls.append(thumbnail.url)
    pairs = sorted(zip(ids, urls, strict=True))
    updated = await _update(session, [i for i, _ in pairs], [u for _, u in pairs])
    return ThumbnailResult(matched=len(ids), updated=updated, skipped=len(by_key) - len(seen_keys))


async def _load_frames(
    session: AsyncSession, rover: str, sols: Sequence[int]
) -> list[tuple[int, str | None]]:
    stmt = (
        select(Photo.id, Photo.extra["imageid"].astext)
        .join(Camera, Camera.id == Photo.camera_id)
        .where(
            Photo.rover_id == rover,
            Photo.sol.in_(list(sols)),
            Camera.code.in_(sorted(MSL_THUMBNAIL_CAMERAS)),
        )
    )
    return [(row[0], row[1]) for row in await session.execute(stmt)]


async def _update(session: AsyncSession, ids: list[int], urls: list[str]) -> int:
    """Rows are updated in ``id`` order (the caller sorts), so a concurrent sync that upserts
    the same photos locks them in the same order and cannot deadlock with this statement."""
    updated = 0
    for start in range(0, len(ids), _UPDATE_CHUNK):
        result = await session.execute(
            _UPDATE,
            {
                "ids": ids[start : start + _UPDATE_CHUNK],
                "urls": urls[start : start + _UPDATE_CHUNK],
            },
        )
        updated += result.rowcount  # type: ignore[attr-defined]
    return updated


class ThumbnailSource(Protocol):
    async def fetch_thumbnails(self, sols: Sequence[int]) -> list[Thumbnail]: ...


async def sync_thumbnails(
    session: AsyncSession, source: ThumbnailSource, sols: Sequence[int]
) -> ThumbnailResult:
    """Fetch the thumbnails of ``sols`` (one feed query) and apply them; commits on success.

    Raises ``UpstreamError`` (from the source) if the feed fails (nothing is written then).
    """
    thumbnails = await source.fetch_thumbnails(sols)
    result = await apply_thumbnails(session, MSL_ROVER_ID, sols, thumbnails)
    await session.commit()
    return result


def has_thumbnail_cameras(records: Sequence[PhotoRecord]) -> bool:
    """True if any record is from a camera that gets thumbnails."""
    return any(r.camera_code.upper() in MSL_THUMBNAIL_CAMERAS for r in records)


async def sync_sol_thumbnails(session: AsyncSession, source: ThumbnailSource, sol: int) -> None:
    """The thumbnail step of a Curiosity sync; any failure is logged and swallowed, the
    frames of the sol stay (``img_small`` empty until a later pass)."""
    try:
        result = await sync_thumbnails(session, source, [sol])
    except Exception as exc:
        with suppress(Exception):
            await session.rollback()
        if isinstance(exc, UpstreamError | SQLAlchemyError):
            logger.warning(
                "Thumbnails of curiosity sol %d not loaded: %s: %s", sol, type(exc).__name__, exc
            )
        else:
            logger.exception("Thumbnails of curiosity sol %d failed unexpectedly", sol)
        return
    logger.debug(
        "Thumbnails curiosity sol=%d matched=%d updated=%d skipped=%d",
        sol,
        result.matched,
        result.updated,
        result.skipped,
    )


DEFAULT_BATCH_SOLS = 20


@dataclass
class ThumbnailRun:
    """Outcome of ``backfill_thumbnails``: the summed counters and the sol batches that failed."""

    batches: int = 0
    result: ThumbnailResult = field(default_factory=ThumbnailResult)
    failed_batches: list[tuple[int, int]] = field(default_factory=list)


async def backfill_thumbnails(
    session_factory: async_sessionmaker[AsyncSession],
    source: ThumbnailSource,
    from_sol: int,
    to_sol: int,
    *,
    batch_sols: int = DEFAULT_BATCH_SOLS,
) -> ThumbnailRun:
    """Thumbnails of the sols ``from_sol..to_sol``, ``batch_sols`` sols per feed query.

    Every batch is committed on its own and logged, so an interrupted run is continued with
    ``from_sol`` set to the last logged batch's end + 1 (rerunning a batch is harmless: only
    differing values are written). A failed batch is logged and counted; the run goes on.
    """
    if batch_sols < 1 or from_sol < 0 or to_sol < from_sol:
        raise ValueError(f"Invalid sol range {from_sol}..{to_sol} or batch size {batch_sols}")
    run = ThumbnailRun()
    for start in range(from_sol, to_sol + 1, batch_sols):
        end = min(start + batch_sols - 1, to_sol)
        try:
            async with session_factory() as session:
                result = await sync_thumbnails(session, source, range(start, end + 1))
        except (SQLAlchemyError, UpstreamError) as exc:
            run.failed_batches.append((start, end))
            logger.warning(
                "Thumbnails sols %d-%d failed: %s: %s", start, end, type(exc).__name__, exc
            )
            continue
        run.batches += 1
        run.result += result
        logger.info(
            "Thumbnails sols %d-%d of %d: matched=%d updated=%d skipped=%d",
            start, end, to_sol, result.matched, result.updated, result.skipped,
        )  # fmt: skip
    return run
