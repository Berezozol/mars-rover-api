"""Queries of the RSS feed: photos stored by the regular sync (``photos.feed_at``).

Every query is served by the partial index ``ix_photos_feed`` (rover_id, feed_at DESC, id DESC).
A batch is the photos of one sol that share ``feed_at``: a sol is committed in one transaction,
so ``now()`` is the same for all of its photos.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Camera, Photo
from app.repositories._common import FRESH

BatchKey = tuple[datetime, int]


@dataclass(frozen=True)
class FeedBatchRow:
    feed_at: datetime
    sol: int
    total: int
    # (camera code, photos) ordered by code.
    cameras: list[tuple[str, int]]
    # Distinct non-empty credits, sorted.
    credits: list[str]


def _feed_filter(
    stmt: Select[Any],
    rover_id: str,
    *,
    camera_id: int | None,
    sample_type: str | None,
    since: datetime,
) -> Select[Any]:
    stmt = stmt.where(
        Photo.rover_id == rover_id, Photo.feed_at.is_not(None), Photo.feed_at >= since
    )
    if camera_id is not None:
        stmt = stmt.where(Photo.camera_id == camera_id)
    if sample_type is not None:
        stmt = stmt.where(Photo.sample_type == sample_type.lower())
    return stmt


async def latest_feed_at(
    session: AsyncSession,
    rover_id: str,
    *,
    camera_id: int | None,
    sample_type: str | None,
    since: datetime,
) -> datetime | None:
    """The newest ``feed_at`` of the filtered feed; None when it is empty."""
    stmt = _feed_filter(
        select(func.max(Photo.feed_at)),
        rover_id,
        camera_id=camera_id,
        sample_type=sample_type,
        since=since,
    )
    return await session.scalar(stmt)


async def feed_photos(
    session: AsyncSession,
    rover_id: str,
    *,
    camera_id: int | None,
    sample_type: str | None,
    since: datetime,
    limit: int,
) -> list[Photo]:
    """Photos by arrival, newest first (``feed_at DESC, id DESC``), cameras loaded."""
    stmt = _feed_filter(
        select(Photo).options(selectinload(Photo.camera)),
        rover_id,
        camera_id=camera_id,
        sample_type=sample_type,
        since=since,
    )
    stmt = stmt.order_by(
        Photo.feed_at.desc(), Photo.sol.desc(), Photo.taken_at.desc(), Photo.id.desc()
    ).limit(limit)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def feed_batches(
    session: AsyncSession,
    rover_id: str,
    *,
    camera_id: int | None,
    sample_type: str | None,
    since: datetime,
    limit: int,
) -> list[FeedBatchRow]:
    """Batches (feed_at, sol) newest first: by ``feed_at DESC, sol DESC``."""
    flt = {"camera_id": camera_id, "sample_type": sample_type, "since": since}
    head = _feed_filter(select(Photo.feed_at, Photo.sol, func.count(Photo.id)), rover_id, **flt)
    head = (
        head.group_by(Photo.feed_at, Photo.sol)
        .order_by(Photo.feed_at.desc(), Photo.sol.desc())
        .limit(limit)
    )
    heads = [(feed_at, sol, total) for feed_at, sol, total in await session.execute(head)]
    if not heads:
        return []
    keys = [(feed_at, sol) for feed_at, sol, _ in heads]
    cameras = await _batch_cameras(session, rover_id, keys, **flt)
    credits = await _batch_credits(session, rover_id, keys, **flt)
    return [
        FeedBatchRow(
            feed_at, sol, total, cameras.get((feed_at, sol), []), credits.get((feed_at, sol), [])
        )
        for feed_at, sol, total in heads
    ]


async def _batch_cameras(
    session: AsyncSession, rover_id: str, keys: Sequence[BatchKey], **flt: Any
) -> dict[BatchKey, list[tuple[str, int]]]:
    stmt = _feed_filter(
        select(Photo.feed_at, Photo.sol, Camera.code, func.count(Photo.id)).join(Photo.camera),
        rover_id,
        **flt,
    )
    stmt = stmt.where(tuple_(Photo.feed_at, Photo.sol).in_(list(keys)))
    stmt = stmt.group_by(Photo.feed_at, Photo.sol, Camera.code).order_by(Camera.code)
    result: dict[BatchKey, list[tuple[str, int]]] = {}
    for feed_at, sol, code, count in await session.execute(stmt):
        result.setdefault((feed_at, sol), []).append((code, count))
    return result


async def _batch_credits(
    session: AsyncSession, rover_id: str, keys: Sequence[BatchKey], **flt: Any
) -> dict[BatchKey, list[str]]:
    stmt = _feed_filter(
        select(Photo.feed_at, Photo.sol, Photo.credit).distinct(), rover_id, **flt
    ).where(
        tuple_(Photo.feed_at, Photo.sol).in_(list(keys)),
        Photo.credit.is_not(None),
        Photo.credit != "",
    )
    result: dict[BatchKey, list[str]] = {}
    for feed_at, sol, credit in await session.execute(stmt.order_by(Photo.credit)):
        result.setdefault((feed_at, sol), []).append(credit)
    return result


async def batch_thumbnails(
    session: AsyncSession,
    rover_id: str,
    keys: Sequence[BatchKey],
    *,
    camera_id: int | None,
    sample_type: str | None,
    since: datetime | None = None,
    per_batch: int = 6,
) -> dict[BatchKey, list[Photo]]:
    """The first ``per_batch`` photos of every batch in the batch order
    (``taken_at DESC, id DESC``); the sol is part of the key."""
    if not keys:
        return {}
    rank = (
        func.row_number()
        .over(
            partition_by=(Photo.feed_at, Photo.sol),
            order_by=(Photo.taken_at.desc(), Photo.id.desc()),
        )
        .label("rank")
    )
    inner = select(Photo.id, rank).where(
        Photo.rover_id == rover_id,
        Photo.feed_at.is_not(None),
        tuple_(Photo.feed_at, Photo.sol).in_(list(keys)),
    )
    if camera_id is not None:
        inner = inner.where(Photo.camera_id == camera_id)
    if sample_type is not None:
        inner = inner.where(Photo.sample_type == sample_type.lower())
    if since is not None:
        inner = inner.where(Photo.feed_at >= since)
    ranked = inner.subquery()
    stmt = (
        select(Photo)
        .join(ranked, ranked.c.id == Photo.id)
        .where(ranked.c.rank <= per_batch)
        .options(selectinload(Photo.camera))
        .order_by(Photo.feed_at.desc(), Photo.sol.desc(), Photo.taken_at.desc(), Photo.id.desc())
    )
    result: dict[BatchKey, list[Photo]] = {}
    for photo in await session.scalars(stmt, execution_options=FRESH):
        if photo.feed_at is None:
            continue
        result.setdefault((photo.feed_at, photo.sol), []).append(photo)
    return result
