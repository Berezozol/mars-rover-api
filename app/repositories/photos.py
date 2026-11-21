"""Photos and per-sol summaries."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    BindParameter,
    ColumnElement,
    DateTime,
    Integer,
    Select,
    SmallInteger,
    Text,
    and_,
    bindparam,
    delete,
    distinct,
    false,
    func,
    literal_column,
    or_,
    select,
    text,
    tuple_,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, Insert, aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.db.models import Camera, CameraSolCount, Photo, SolSummary
from app.repositories._common import FRESH
from app.repositories.rovers import get_or_create_cameras
from app.sources.base import PhotoRecord
from app.sources.photo_meta import STORED_META_PATHS, StoredMeta, stored_meta

FULL_SAMPLE_TYPE = "full"
SECONDS_PER_DAY = 86_400

# Order of a photo list: sol first (``sol DESC, taken_at DESC, id DESC``) or capture time first.
PhotoSort = Literal["-sol", "sol", "-taken_at", "taken_at"]
DEFAULT_SORT: PhotoSort = "-sol"

# asyncpg allows at most 32767 bind parameters per statement (~20 per photo row).
_UPSERT_CHUNK = 1000

# Columns overwritten when a photo is upserted again; created_at and feed_at are never touched.
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
    "local_time_s",
    "width",
    "height",
)

_EMPTY_TEXT_ARRAY = literal_column("'{}'::text[]")


@dataclass
class PhotoFilter:
    rover_id: str
    sol: int | None = None
    earth_date: date | None = None
    # Case-insensitive exact codes; an empty list matches nothing.
    camera_codes: Sequence[str] | None = None
    # Camera ids for list queries; replace the camera_codes join when set.
    camera_ids: Sequence[int] | None = None
    sample_type: str | None = None
    # Stop filter: ``site`` alone, or with an inclusive ``drive`` range (either end may be open).
    site: int | None = None
    drive_from: int | None = None
    drive_to: int | None = None
    # Inclusive ranges; either end may be open.
    sol_min: int | None = None
    sol_max: int | None = None
    date_min: date | None = None
    date_max: date | None = None
    site_min: int | None = None
    site_max: int | None = None
    # Local solar time windows [start, end) in seconds of the day, any of which matches; a
    # window over midnight is given as two. Empty: no filter.
    local_time_ranges: tuple[tuple[int, int], ...] = ()
    # Full frame size at least this many pixels (photos without a size never match).
    min_width: int | None = None
    min_height: int | None = None
    # Plan hint for list queries, set by the caller that knows the filter matches few rows:
    # select the matching rows first, then sort them (see ``_keys_query``).
    narrow: bool = False


async def upsert_photos(
    session: AsyncSession,
    rover_id: str,
    records: Sequence[PhotoRecord],
    *,
    mark_new: bool = False,
) -> int:
    """Insert or update photos by (rover_id, source_id); returns the number of unique records.

    ``mark_new`` (incremental sync) stamps ``feed_at = now()`` on inserted rows; ``feed_at`` is
    never part of the conflict update, so an existing photo keeps its value (or NULL).
    """
    unique = list({record.source_id: record for record in records}.values())
    if not unique:
        return 0
    cameras = await get_or_create_cameras(session, rover_id, (r.camera_code for r in unique))
    rows = [_photo_row(rover_id, cameras[r.camera_code.upper()].id, r) for r in unique]
    if mark_new:
        for row in rows:
            row["feed_at"] = func.now()
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
    meta = stored_meta(rover_id, record.extra)
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
        "local_time_s": meta.local_time_s,
        "width": meta.width,
        "height": meta.height,
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


def _filtered(
    stmt: Select[Any],
    flt: PhotoFilter,
    *,
    joined_camera: bool = False,
    bound_taken_at: bool = True,
) -> Select[Any]:
    """Apply the filter; joins cameras only for a non-empty camera filter (unless joined)."""
    stmt = stmt.where(Photo.rover_id == flt.rover_id)
    if flt.sol is not None:
        stmt = stmt.where(Photo.sol == flt.sol)
    if flt.earth_date is not None:
        stmt = stmt.where(Photo.earth_date == flt.earth_date)
    if flt.camera_ids is not None:
        stmt = stmt.where(Photo.camera_id.in_(list(flt.camera_ids)))
    elif flt.camera_codes is not None:
        codes = [code.upper() for code in flt.camera_codes]
        if not codes:
            return stmt.where(false())
        if not joined_camera:
            stmt = stmt.join(Photo.camera)
        stmt = stmt.where(Camera.code.in_(codes))
    if flt.sample_type is not None:
        stmt = stmt.where(Photo.sample_type == flt.sample_type.lower())
    stmt = _ranged(stmt, flt, bound_taken_at)
    if flt.site is not None:
        stmt = stmt.where(Photo.site == flt.site)
    if flt.drive_from is not None:
        stmt = stmt.where(Photo.drive >= flt.drive_from)
    if flt.drive_to is not None:
        stmt = stmt.where(Photo.drive <= flt.drive_to)
    return _meta_filtered(stmt, flt)


def _meta_filtered(stmt: Select[Any], flt: PhotoFilter) -> Select[Any]:
    """Local time windows and minimum frame size (columns filled from ``extra``)."""
    if flt.local_time_ranges:
        stmt = stmt.where(or_(*(_time_window(low, high) for low, high in flt.local_time_ranges)))
    if flt.min_width is not None:
        stmt = stmt.where(Photo.width >= flt.min_width)
    if flt.min_height is not None:
        stmt = stmt.where(Photo.height >= flt.min_height)
    return stmt


def _time_window(low: int, high: int) -> ColumnElement[bool]:
    """``low <= local_time_s < high``; a bound at the edge of the day is left out. A comparison
    never matches NULL, and the partial index on local_time_s covers it."""
    if low >= high:
        return false()
    bounds = []
    if low > 0:
        bounds.append(Photo.local_time_s >= low)
    if high < SECONDS_PER_DAY:
        bounds.append(Photo.local_time_s < high)
    return and_(*bounds) if bounds else Photo.local_time_s.is_not(None)


def _day_start(day: date, shift_days: int) -> datetime | None:
    """UTC midnight of ``day + shift_days``; None past the calendar edge (no bound then)."""
    try:
        return datetime.combine(day + timedelta(days=shift_days), time.min, tzinfo=UTC)
    except OverflowError:
        return None


def _ranged(stmt: Select[Any], flt: PhotoFilter, bound_taken_at: bool = True) -> Select[Any]:
    """Sol, date and site ranges.

    A date range also bounds ``taken_at`` by a day on each side: ``earth_date`` is the UTC date
    of ``taken_at``, so the redundant bound never drops a row, and it lets the
    (rover_id, taken_at) index serve both the filter and the order. Counts skip the bound
    (``bound_taken_at=False``): ``earth_date`` alone is answered from its index.
    """
    if flt.sol_min is not None:
        stmt = stmt.where(Photo.sol >= flt.sol_min)
    if flt.sol_max is not None:
        stmt = stmt.where(Photo.sol <= flt.sol_max)
    if flt.date_min is not None:
        stmt = stmt.where(Photo.earth_date >= flt.date_min)
        if bound_taken_at and (lower := _day_start(flt.date_min, -1)) is not None:
            stmt = stmt.where(Photo.taken_at >= lower)
    if flt.date_max is not None:
        stmt = stmt.where(Photo.earth_date <= flt.date_max)
        if bound_taken_at and (upper := _day_start(flt.date_max, 2)) is not None:
            stmt = stmt.where(Photo.taken_at < upper)
    if flt.site_min is not None:
        stmt = stmt.where(Photo.site >= flt.site_min)
    if flt.site_max is not None:
        stmt = stmt.where(Photo.site <= flt.site_max)
    return stmt


def _sort_order(sort: PhotoSort, sol: Any, taken_at: Any, photo_id: Any) -> list[Any]:
    """ORDER BY clauses of the sort over the given columns (``id`` breaks every tie)."""
    descending = sort.startswith("-")
    keys = [taken_at, photo_id] if sort.endswith("taken_at") else [sol, taken_at, photo_id]
    return [key.desc() if descending else key.asc() for key in keys]


async def list_photos(
    session: AsyncSession,
    flt: PhotoFilter,
    *,
    order: Literal["legacy", "newest"] = "newest",
    sort: PhotoSort = DEFAULT_SORT,
    limit: int | None = None,
    offset: int = 0,
) -> list[Photo]:
    """Photos of one rover; ``order="legacy"`` is the v1 order (camera, id) and ignores sort."""
    if order == "legacy":
        # Ordering by camera needs the join anyway; reuse it to load Photo.camera.
        stmt = select(Photo).join(Photo.camera).options(contains_eager(Photo.camera))
        stmt = _filtered(stmt, flt, joined_camera=True)
        stmt = stmt.order_by(Camera.sort_order, Photo.id)
    else:
        stmt = _filtered(select(Photo).options(selectinload(Photo.camera)), flt)
        stmt = stmt.order_by(*_sort_order(sort, Photo.sol, Photo.taken_at, Photo.id))
    stmt = stmt.limit(limit).offset(offset)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def list_photos_multi(
    session: AsyncSession,
    filters: Sequence[PhotoFilter],
    sort: PhotoSort,
    limit: int,
    offset: int,
) -> list[Photo]:
    """One page of the merged list of one or several rovers (one filter per rover).

    Two phases, also for a single filter: the sort keys (id, sol, taken_at) of the first
    ``offset + limit`` photos of each rover are read from an index without touching the table
    rows; the keys are merged and cut to the page; only those rows are loaded by id. Deep
    pages therefore cost an index scan, not a fetch of every skipped row.
    """
    filters = [part for flt in filters for part in _per_camera(flt)]
    if not filters:
        return []
    if len(filters) == 1:
        keys_stmt = _keys_query(filters[0], sort, limit, offset, 0)
        page_ids = [row.id for row in await session.execute(keys_stmt, execution_options=FRESH)]
    else:
        parts = [_keys_query(flt, sort, offset + limit, 0, n) for n, flt in enumerate(filters)]
        merged = union_all(*parts).subquery()
        ordered = _sort_order(sort, merged.c.sol, merged.c.taken_at, merged.c.id)
        page_ids = list(
            await session.scalars(
                select(merged.c.id).order_by(*ordered).limit(limit).offset(offset),
                execution_options=FRESH,
            )
        )
    if not page_ids:
        return []
    stmt = select(Photo).where(Photo.id.in_(page_ids)).options(selectinload(Photo.camera))
    by_id = {photo.id: photo for photo in await session.scalars(stmt, execution_options=FRESH)}
    return [by_id[photo_id] for photo_id in page_ids if photo_id in by_id]


def _per_camera(flt: PhotoFilter) -> list[PhotoFilter]:
    """One filter per camera id: each part then reads the (rover_id, camera_id, ...) indexes
    instead of filtering a scan of the whole rover by camera."""
    if flt.camera_ids is None or flt.narrow:
        # A narrow filter selects its few rows once, whatever their cameras.
        return [flt]
    return [replace(flt, camera_ids=[camera_id]) for camera_id in flt.camera_ids]


def _keys_query(
    flt: PhotoFilter, sort: PhotoSort, limit: int, offset: int, part: int
) -> Select[Any]:
    """Sort keys of the rover's photos ``offset .. offset + limit``, id first.

    ``sol`` is selected only for the sol sorts: for ``taken_at`` sorts a constant is
    selected instead, so the (rover_id, taken_at, id) index alone can answer the query.
    Normally the planner walks the sort index and filters; with ``flt.narrow`` the matching
    rows are selected first (a MATERIALIZED CTE, named by ``part`` to stay unique in a
    union) and only then sorted, which is the right plan when they sit far from the start
    of the sort order (e.g. an early date range under ``-sol``).
    """
    by_time = sort.endswith("taken_at")
    sol = literal_column("0").label("sol") if by_time else Photo.sol
    stmt = _filtered(select(Photo.id, sol, Photo.taken_at), flt)
    if flt.narrow:
        keys = stmt.cte(f"keys{part}").prefix_with("MATERIALIZED")
        order = _sort_order(sort, keys.c.sol, keys.c.taken_at, keys.c.id)
        stmt = select(keys.c.id, keys.c.sol, keys.c.taken_at).order_by(*order)
    else:
        stmt = stmt.order_by(*_sort_order(sort, sol, Photo.taken_at, Photo.id))
    return stmt.limit(limit).offset(offset or None)


async def count_photos(session: AsyncSession, flt: PhotoFilter) -> int:
    stmt = _filtered(select(func.count()).select_from(Photo), flt, bound_taken_at=False)
    return await session.scalar(stmt) or 0


async def camera_total_photos(
    session: AsyncSession,
    rover_id: str,
    camera_codes: Sequence[str],
    *,
    full_only: bool = False,
    sol_min: int | None = None,
    sol_max: int | None = None,
) -> int:
    """Photos of the cameras in the sol range (default: all sols), summed from
    camera_sol_counts (full frames only with ``full_only``); kept by the sync together with
    sol_summaries."""
    codes = [code.upper() for code in camera_codes]
    if not codes:
        return 0
    column = CameraSolCount.full_photos if full_only else CameraSolCount.total_photos
    stmt = (
        select(func.coalesce(func.sum(column), 0))
        .join(Camera, Camera.id == CameraSolCount.camera_id)
        .where(CameraSolCount.rover_id == rover_id, Camera.code.in_(codes))
    )
    if sol_min is not None:
        stmt = stmt.where(CameraSolCount.sol >= sol_min)
    if sol_max is not None:
        stmt = stmt.where(CameraSolCount.sol <= sol_max)
    return int(await session.scalar(stmt) or 0)


async def sol_range_total_photos(
    session: AsyncSession,
    rover_id: str,
    sol_min: int | None,
    sol_max: int | None,
    *,
    full_only: bool = False,
) -> int:
    """Photos of the rover in the sol range, from sol_summaries (full frames only with
    ``full_only``)."""
    column = SolSummary.full_photos if full_only else SolSummary.total_photos
    stmt = select(func.coalesce(func.sum(column), 0)).where(SolSummary.rover_id == rover_id)
    if sol_min is not None:
        stmt = stmt.where(SolSummary.sol >= sol_min)
    if sol_max is not None:
        stmt = stmt.where(SolSummary.sol <= sol_max)
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


async def newest_earth_date(session: AsyncSession, rover_id: str) -> date | None:
    """Latest real earth_date of the rover's photos (index ix_photos_rover_id_earth_date)."""
    stmt = select(func.max(Photo.earth_date)).where(Photo.rover_id == rover_id)
    return await session.scalar(stmt)


async def sol_position(
    session: AsyncSession,
    flt: PhotoFilter,
    taken_at: datetime,
    photo_id: int,
    matches: bool = True,
) -> tuple[int | None, int, int | None, int | None]:
    """Place of a photo among the filter's photos of one sol, in the /photos order.

    Returns ``(position, total, newer_id, older_id)``: ``position`` counts from 1 and is None
    when the photo itself does not match the filter; neighbours are the nearest matching
    photos before / after it in the list order (``taken_at DESC, id DESC``). ``flt.sol`` is
    required. ``matches`` tells whether the photo itself passes the filter (the caller knows
    it from the loaded photo).

    Without camera / sample_type filters ``total`` comes from sol_summaries and assumes they
    are in sync with photos (the sync commits both together).
    """
    if flt.sol is None:
        raise ValueError("sol_position needs flt.sol")
    key = tuple_(Photo.taken_at, Photo.id)
    here = tuple_(taken_at, photo_id)
    newer_stmt = (
        _filtered(select(Photo.id), flt)
        .where(key > here)
        .order_by(Photo.taken_at, Photo.id)
        .limit(1)
    )
    older_stmt = (
        _filtered(select(Photo.id), flt)
        .where(key < here)
        .order_by(Photo.taken_at.desc(), Photo.id.desc())
        .limit(1)
    )
    newer_id = await session.scalar(newer_stmt)
    older_id = await session.scalar(older_stmt)
    if flt.camera_codes is None and flt.sample_type is None:
        total = await sol_total_photos(session, flt.rover_id, flt.sol)
        ahead = await session.scalar(_filtered(select(func.count(Photo.id)), flt).where(key > here))
        return (ahead or 0) + 1, total, newer_id, older_id
    total = await count_photos(session, flt)
    if not matches:
        return None, total, newer_id, older_id
    ahead = await session.scalar(_filtered(select(func.count(Photo.id)), flt).where(key > here))
    return (ahead or 0) + 1, total, newer_id, older_id


async def nearest_sols(
    session: AsyncSession,
    rover_id: str,
    *,
    sol: int | None = None,
    earth_date: date | None = None,
) -> tuple[int | None, int | None]:
    """``(before, after)``: the nearest sols with photos below / above the sol or earth_date.

    By ``earth_date`` the sol's earliest capture date (sol_summaries) is compared. Without
    either argument both are None.
    """
    if sol is not None:
        below, above = SolSummary.sol < sol, SolSummary.sol > sol
    elif earth_date is not None:
        below, above = SolSummary.earth_date < earth_date, SolSummary.earth_date > earth_date
    else:
        return None, None
    base = SolSummary.rover_id == rover_id
    before = await session.scalar(select(func.max(SolSummary.sol)).where(base, below))
    after = await session.scalar(select(func.min(SolSummary.sol)).where(base, above))
    return before, after


def _extra_projection(paths: Sequence[tuple[str, ...]]) -> ColumnElement[Any]:
    """JSONB object with only the given paths of ``photos.extra`` (missing values as null)."""
    tree: dict[str, Any] = {}
    for path in paths:
        node = tree
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = tuple(path)

    def build(level: dict[str, Any], prefix: tuple[str, ...]) -> ColumnElement[Any]:
        args: list[Any] = []
        for key, child in level.items():
            args.append(key)
            if isinstance(child, dict):
                args.append(build(child, (*prefix, key)))
            else:
                args.append(Photo.extra.op("#>")(text("'{" + ",".join(child) + "}'")))
        return func.jsonb_build_object(*args)

    return build(tree, ())


# The backfill reads these few values instead of the whole (about 1 KB) ``extra``.
_META_EXTRA = _extra_projection(STORED_META_PATHS)

# Locks the rows in id order first (the same order concurrent upserts take them in, so no
# deadlock), then writes only rows that still carry the ``updated_at`` that was read: a row
# upserted in between is skipped and picked up by the next run.
_UPDATE_META = text(
    "WITH locked AS MATERIALIZED ("
    "SELECT id FROM photos WHERE id = ANY(:ids) ORDER BY id FOR UPDATE) "
    "UPDATE photos AS p SET local_time_s = v.t, width = v.w, height = v.h "
    "FROM unnest(:ids, :times, :widths, :heights, :stamps) AS v(id, t, w, h, u), locked "
    "WHERE p.id = v.id AND locked.id = p.id AND p.updated_at = v.u "
    "AND (p.local_time_s IS DISTINCT FROM v.t "
    "OR p.width IS DISTINCT FROM v.w OR p.height IS DISTINCT FROM v.h)"
).bindparams(
    bindparam("ids", type_=ARRAY(BigInteger)),
    bindparam("times", type_=ARRAY(Integer)),
    bindparam("widths", type_=ARRAY(SmallInteger)),
    bindparam("heights", type_=ARRAY(SmallInteger)),
    bindparam("stamps", type_=ARRAY(DateTime(timezone=True))),
)


async def sol_bounds(session: AsyncSession, rover_id: str) -> tuple[int, int] | None:
    """Lowest and highest sol with photos of the rover; None without photos."""
    stmt = select(func.min(Photo.sol), func.max(Photo.sol)).where(Photo.rover_id == rover_id)
    low, high = (await session.execute(stmt)).one()
    return None if low is None else (low, high)


async def meta_backfill_batch(
    session: AsyncSession, rover_id: str, sol_from: int, sol_to: int
) -> tuple[int, int]:
    """Set the meta columns of the rover's photos in a sol range from ``extra``.

    Recomputes every row of the range, so stale stored values are corrected too, and writes
    only rows whose derived values differ from the stored ones (so a second run writes
    nothing and bloats nothing). ``updated_at`` is not touched: the photos themselves did
    not change. A row changed by a concurrent upsert since it was read is skipped (see
    ``_UPDATE_META``). Returns ``(rows looked at, rows updated)``.
    """
    stmt = select(
        Photo.id, _META_EXTRA, Photo.updated_at, Photo.local_time_s, Photo.width, Photo.height
    ).where(Photo.rover_id == rover_id, Photo.sol >= sol_from, Photo.sol <= sol_to)
    rows = (await session.execute(stmt)).all()
    derived = [
        (row.id, row.updated_at, stored_meta(rover_id, row[1]), StoredMeta(*row[3:]))
        for row in rows
    ]
    values = [(i, stamp, new) for i, stamp, new, stored in derived if new != stored]
    if not values:
        return len(rows), 0
    result = await session.execute(
        _UPDATE_META,
        {
            "ids": [i for i, _, _ in values],
            "stamps": [stamp for _, stamp, _ in values],
            "times": [m.local_time_s for _, _, m in values],
            "widths": [m.width for _, _, m in values],
            "heights": [m.height for _, _, m in values],
        },
    )
    return len(rows), int(result.rowcount)  # type: ignore[attr-defined]
