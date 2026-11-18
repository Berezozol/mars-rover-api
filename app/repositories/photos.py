"""Photos and per-sol summaries."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import (
    BindParameter,
    Integer,
    Select,
    Text,
    bindparam,
    delete,
    distinct,
    false,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, Insert, aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.db.models import Camera, CameraSolCount, Photo, SolSummary
from app.repositories._common import FRESH
from app.repositories.rovers import get_or_create_cameras
from app.sources.base import PhotoRecord

FULL_SAMPLE_TYPE = "full"

# asyncpg allows at most 32767 bind parameters per statement (~20 per photo row).
_UPSERT_CHUNK = 1000

# Columns overwritten when a photo is upserted again; created_at is never touched.
_DATA_COLUMNS = (
    "camera_id",
    "sol",
    "earth_date",
    "taken_at",
    "received_at",
    "sample_type",
    "img_full",
    "img_small",
    "img_medium",
    "img_large",
    "title",
    "caption",
    "credit",
    "source_link",
    "site",
    "drive",
    "extra",
)

_EMPTY_TEXT_ARRAY = literal_column("'{}'::text[]")


@dataclass
class PhotoFilter:
    rover_id: str
    sol: int | None = None
    earth_date: date | None = None
    # Case-insensitive exact codes; an empty list matches nothing.
    camera_codes: Sequence[str] | None = None
    sample_type: str | None = None


async def upsert_photos(
    session: AsyncSession, rover_id: str, records: Sequence[PhotoRecord]
) -> int:
    """Insert or update photos by (rover_id, source_id); returns the number of unique records."""
    unique = list({record.source_id: record for record in records}.values())
    if not unique:
        return 0
    cameras = await get_or_create_cameras(session, rover_id, (r.camera_code for r in unique))
    rows = [_photo_row(rover_id, cameras[r.camera_code.upper()].id, r) for r in unique]
    for start in range(0, len(rows), _UPSERT_CHUNK):
        await session.execute(_upsert_statement(rows[start : start + _UPSERT_CHUNK]))
    return len(unique)


async def moved_from_sols(
    session: AsyncSession, rover_id: str, records: Sequence[PhotoRecord]
) -> set[int]:
    """Sols where already stored photos of these records sit under a different sol."""
    new_sol = {record.source_id: record.sol for record in records}
    if not new_sol:
        return set()
    ids_param = bindparam("ids", value=list(new_sol), type_=ARRAY(Text))
    stmt = select(Photo.source_id, Photo.sol).where(
        Photo.rover_id == rover_id, Photo.source_id == func.any(ids_param)
    )
    rows = await session.execute(stmt)
    return {sol for source_id, sol in rows if sol != new_sol[source_id]}


def _photo_row(rover_id: str, camera_id: int, record: PhotoRecord) -> dict[str, Any]:
    return {
        "rover_id": rover_id,
        "camera_id": camera_id,
        "source_id": record.source_id,
        "sol": record.sol,
        "earth_date": record.earth_date,
        "taken_at": record.taken_at,
        "received_at": record.received_at,
        "sample_type": record.sample_type.lower(),
        "img_full": record.img_full,
        "img_small": record.img_small,
        "img_medium": record.img_medium,
        "img_large": record.img_large,
        "title": record.title,
        "caption": record.caption,
        "credit": record.credit,
        "source_link": record.source_link,
        "site": record.site,
        "drive": record.drive,
        "extra": record.extra,
    }


def _upsert_statement(rows: list[dict[str, Any]]) -> Insert:
    """Upsert that touches a row (and updated_at) only when some data column changed."""
    stmt = pg_insert(Photo).values(rows)
    updates: dict[str, Any] = {name: stmt.excluded[name] for name in _DATA_COLUMNS}
    updates["updated_at"] = func.now()
    changed = or_(
        *(Photo.__table__.c[name].is_distinct_from(stmt.excluded[name]) for name in _DATA_COLUMNS)
    )
    return stmt.on_conflict_do_update(
        index_elements=[Photo.rover_id, Photo.source_id], set_=updates, where=changed
    )


def _filtered(stmt: Select[Any], flt: PhotoFilter, *, joined_camera: bool = False) -> Select[Any]:
    """Apply the filter; joins cameras only for a non-empty camera filter (unless joined)."""
    stmt = stmt.where(Photo.rover_id == flt.rover_id)
    if flt.sol is not None:
        stmt = stmt.where(Photo.sol == flt.sol)
    if flt.earth_date is not None:
        stmt = stmt.where(Photo.earth_date == flt.earth_date)
    if flt.camera_codes is not None:
        codes = [code.upper() for code in flt.camera_codes]
        if not codes:
            return stmt.where(false())
        if not joined_camera:
            stmt = stmt.join(Photo.camera)
        stmt = stmt.where(Camera.code.in_(codes))
    if flt.sample_type is not None:
        stmt = stmt.where(Photo.sample_type == flt.sample_type.lower())
    return stmt


async def list_photos(
    session: AsyncSession,
    flt: PhotoFilter,
    *,
    order: Literal["legacy", "newest"] = "newest",
    limit: int | None = None,
    offset: int = 0,
) -> list[Photo]:
    if order == "legacy":
        # Ordering by camera needs the join anyway; reuse it to load Photo.camera.
        stmt = select(Photo).join(Photo.camera).options(contains_eager(Photo.camera))
        stmt = _filtered(stmt, flt, joined_camera=True)
        stmt = stmt.order_by(Camera.sort_order, Photo.id)
    else:
        stmt = _filtered(select(Photo).options(selectinload(Photo.camera)), flt)
        stmt = stmt.order_by(Photo.sol.desc(), Photo.taken_at.desc(), Photo.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def count_photos(session: AsyncSession, flt: PhotoFilter) -> int:
    stmt = _filtered(select(func.count(Photo.id)), flt)
    return await session.scalar(stmt) or 0


async def camera_total_photos(
    session: AsyncSession, rover_id: str, camera_codes: Sequence[str], *, full_only: bool = False
) -> int:
    """Photos of the cameras over all sols, summed from camera_sol_counts (full frames only
    with ``full_only``); kept by the sync together with sol_summaries."""
    codes = [code.upper() for code in camera_codes]
    if not codes:
        return 0
    column = CameraSolCount.full_photos if full_only else CameraSolCount.total_photos
    stmt = (
        select(func.coalesce(func.sum(column), 0))
        .join(Camera, Camera.id == CameraSolCount.camera_id)
        .where(CameraSolCount.rover_id == rover_id, Camera.code.in_(codes))
    )
    return int(await session.scalar(stmt) or 0)


async def sol_total_photos(session: AsyncSession, rover_id: str, sol: int) -> int:
    """Photos of the sol over all sample types, from sol_summaries (0 without a row)."""
    stmt = select(SolSummary.total_photos).where(
        SolSummary.rover_id == rover_id, SolSummary.sol == sol
    )
    return await session.scalar(stmt) or 0


async def rover_total_photos_from_summaries(session: AsyncSession, rover_id: str) -> int:
    """Photos of the rover over all sample types, summed from sol_summaries.

    Unlike the rovers.total_photos cache, the summaries are committed per sol by the sync.
    """
    stmt = select(func.coalesce(func.sum(SolSummary.total_photos), 0)).where(
        SolSummary.rover_id == rover_id
    )
    return int(await session.scalar(stmt) or 0)


async def get_photo(session: AsyncSession, photo_id: int) -> Photo | None:
    stmt = select(Photo).where(Photo.id == photo_id).options(selectinload(Photo.camera))
    return await session.scalar(stmt, execution_options=FRESH)


async def max_sol(session: AsyncSession, rover_id: str, *, full_only: bool = False) -> int | None:
    stmt = select(func.max(Photo.sol)).where(Photo.rover_id == rover_id)
    if full_only:
        stmt = stmt.where(Photo.sample_type == FULL_SAMPLE_TYPE)
    return await session.scalar(stmt)


async def recompute_sol_summaries(
    session: AsyncSession, rover_id: str, sols: Iterable[int]
) -> None:
    """Rebuild summaries and camera counts for the given sols; sols without photos lose
    their rows."""
    sol_list = sorted(set(sols))
    if not sol_list:
        return
    # One array parameter instead of an expanded IN list: "sol = ANY(:sols)".
    sols_param = bindparam("sols", value=sol_list, type_=ARRAY(Integer))
    stale = delete(SolSummary).where(
        SolSummary.rover_id == rover_id,
        SolSummary.sol == func.any(sols_param),
        SolSummary.sol.not_in(
            select(Photo.sol).where(Photo.rover_id == rover_id, Photo.sol == func.any(sols_param))
        ),
    )
    await session.execute(stale)
    await session.execute(_summary_upsert(rover_id, sols_param))
    await _recompute_camera_counts(session, rover_id, sols_param)


async def _recompute_camera_counts(
    session: AsyncSession, rover_id: str, sols_param: BindParameter[list[int]]
) -> None:
    """Replace the camera counts of the given sols with fresh aggregates."""
    await session.execute(
        delete(CameraSolCount).where(
            CameraSolCount.rover_id == rover_id, CameraSolCount.sol == func.any(sols_param)
        )
    )
    aggregated = (
        select(
            Photo.rover_id,
            Photo.sol,
            Photo.camera_id,
            func.count(Photo.id),
            func.count(Photo.id).filter(Photo.sample_type == FULL_SAMPLE_TYPE),
        )
        .where(Photo.rover_id == rover_id, Photo.sol == func.any(sols_param))
        .group_by(Photo.rover_id, Photo.sol, Photo.camera_id)
    )
    columns = ["rover_id", "sol", "camera_id", "total_photos", "full_photos"]
    await session.execute(pg_insert(CameraSolCount).from_select(columns, aggregated))


def _summary_upsert(rover_id: str, sols_param: BindParameter[list[int]]) -> Insert:
    """INSERT ... SELECT of per-sol aggregates for the given sols."""
    is_full = Photo.sample_type == FULL_SAMPLE_TYPE
    codes = func.array_agg(aggregate_order_by(distinct(Camera.code), Camera.code))
    aggregated = (
        select(
            Photo.rover_id,
            Photo.sol,
            func.min(Photo.earth_date),
            func.count(Photo.id),
            codes,
            func.count(Photo.id).filter(is_full),
            func.coalesce(codes.filter(is_full), _EMPTY_TEXT_ARRAY),
        )
        .join(Photo.camera)
        .where(Photo.rover_id == rover_id, Photo.sol == func.any(sols_param))
        .group_by(Photo.rover_id, Photo.sol)
    )
    columns = [
        "rover_id",
        "sol",
        "earth_date",
        "total_photos",
        "cameras",
        "full_photos",
        "full_cameras",
    ]
    stmt = pg_insert(SolSummary).from_select(columns, aggregated)
    return stmt.on_conflict_do_update(
        index_elements=[SolSummary.rover_id, SolSummary.sol],
        set_={name: stmt.excluded[name] for name in columns[2:]},
    )


async def list_sol_summaries(
    session: AsyncSession,
    rover_id: str,
    *,
    from_sol: int | None = None,
    to_sol: int | None = None,
    full_only: bool = False,
    descending: bool = False,
) -> list[SolSummary]:
    """Summaries by sol (ascending unless ``descending``); ``full_only`` keeps sols with
    full-frame photos."""
    stmt = select(SolSummary).where(SolSummary.rover_id == rover_id)
    if full_only:
        stmt = stmt.where(SolSummary.full_photos > 0)
    if from_sol is not None:
        stmt = stmt.where(SolSummary.sol >= from_sol)
    if to_sol is not None:
        stmt = stmt.where(SolSummary.sol <= to_sol)
    order = SolSummary.sol.desc() if descending else SolSummary.sol
    return list(await session.scalars(stmt.order_by(order), execution_options=FRESH))
