"""MSL RAD radiation: files of the volume, daily summaries and the state of the dataset."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RadDaily, RadDataset, RadFile
from app.repositories._common import FRESH
from app.sources.rad import file_name

DATASET_ID = 1
# Statuses of a file that must be read again.
BAD_STATUSES = ("failed", "md5_mismatch")
DAILY_VALUE_COLUMNS = (
    "earth_date",
    "dose_b_mean",
    "dose_b_min",
    "dose_b_max",
    "dose_e_mean",
    "dose_e_min",
    "dose_e_max",
    "bu_count",
    "live_s",
    "n_used",
    "n_dropped_qc",
    "n_dropped_low_rate",
    "n_dropped_invalid",
    "qc_bits",
    "timing_caveat",
    "source_path",
)
_FILE_COLUMNS = tuple(column.name for column in RadFile.__table__.columns)
_DAILY_COLUMNS = tuple(column.name for column in RadDaily.__table__.columns)
_DATASET_COLUMNS = tuple(column.name for column in RadDataset.__table__.columns)
_FILE_KEYS = ("path",)
_IN_CHUNK = 5000


@dataclass(frozen=True)
class RadStatus:
    """What ``rad status`` shows."""

    dataset: RadDataset | None
    files_by_status: dict[str, int]
    sols: int
    min_sol: int | None
    max_sol: int | None
    updated_at: datetime | None


async def get_dataset(session: AsyncSession) -> RadDataset | None:
    return await session.get(RadDataset, DATASET_ID, populate_existing=True)


async def save_dataset(session: AsyncSession, **fields: Any) -> None:
    """Create the state row if missing and set ``fields`` on it (``ON CONFLICT``)."""
    statement = pg_insert(RadDataset).values(id=DATASET_ID, **fields)
    if fields:
        statement = statement.on_conflict_do_update(index_elements=[RadDataset.id], set_=fields)
    else:
        statement = statement.on_conflict_do_nothing()
    await session.execute(statement)


async def list_files(session: AsyncSession) -> Sequence[RadFile]:
    """Every known file."""
    return (await session.scalars(select(RadFile), execution_options=FRESH)).all()


async def upsert_files(session: AsyncSession, rows: Sequence[Mapping[str, Any]]) -> None:
    """Insert files or replace the stored ones; every row carries all columns it sets."""
    if not rows:
        return
    statement = pg_insert(RadFile)
    columns = {
        name: getattr(statement.excluded, name) for name in rows[0] if name not in _FILE_KEYS
    }
    await session.execute(
        statement.on_conflict_do_update(index_elements=[RadFile.path], set_=columns),
        list(rows),
    )


async def delete_files(session: AsyncSession, paths: Collection[str]) -> None:
    for chunk in _chunks(sorted(paths)):
        await session.execute(delete(RadFile).where(RadFile.path.in_(chunk)))


async def get_daily_map(session: AsyncSession, sols: Collection[int]) -> dict[int, RadDaily]:
    """The stored summaries of ``sols``."""
    found: dict[int, RadDaily] = {}
    for chunk in _chunks(sorted(sols)):
        query = select(RadDaily).where(RadDaily.sol.in_(chunk))
        for row in (await session.scalars(query, execution_options=FRESH)).all():
            found[row.sol] = row
    return found


async def upsert_daily(session: AsyncSession, row: Mapping[str, Any]) -> bool:
    """Insert the summary or replace the stored one; a summary with the same values is left
    alone (``updated_at`` stays). True when a row was written."""
    statement = pg_insert(RadDaily).values(**row)
    excluded = statement.excluded
    changed = or_(
        *(
            getattr(RadDaily, name).is_distinct_from(getattr(excluded, name))
            for name in DAILY_VALUE_COLUMNS
        )
    )
    values = {name: getattr(excluded, name) for name in DAILY_VALUE_COLUMNS}
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=[RadDaily.sol], set_={**values, "updated_at": func.now()}, where=changed
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def delete_daily(session: AsyncSession, sols: Collection[int]) -> None:
    for chunk in _chunks(sorted(sols)):
        await session.execute(delete(RadDaily).where(RadDaily.sol.in_(chunk)))


async def set_timing_flags(session: AsyncSession, names: Collection[str]) -> int:
    """Set ``timing_listed`` of the files and ``timing_caveat`` of the summaries from the names
    in ``TIMING_ERRORS.TXT``; only changed rows are written. Returns the changed summaries."""
    listed = set(names)
    files = (await session.execute(select(RadFile.path, RadFile.timing_listed))).all()
    stale_files = [path for path, flag in files if flag != (file_name(path) in listed)]
    await _set_flag(session, RadFile.path, "timing_listed", stale_files, listed, {})
    daily = (
        await session.execute(select(RadDaily.sol, RadDaily.source_path, RadDaily.timing_caveat))
    ).all()
    sols = {sol: path for sol, path, flag in daily if flag != (file_name(path) in listed)}
    await _set_flag(
        session, RadDaily.sol, "timing_caveat", list(sols), listed, {"updated_at": func.now()}, sols
    )
    return len(sols)


async def _set_flag(
    session: AsyncSession,
    key: Any,
    column: str,
    keys: Sequence[Any],
    listed: set[str],
    extra: dict[str, Any],
    paths: Mapping[Any, str] | None = None,
) -> None:
    """Write ``column`` of the rows ``keys`` (a file path, or a sol with its ``paths``):
    True where the file name is in ``listed``, else False."""
    model = key.class_
    flagged = [k for k in keys if file_name(paths[k] if paths else k) in listed]
    flagged_set = set(flagged)
    for value, group in ((True, flagged), (False, [k for k in keys if k not in flagged_set])):
        for chunk in _chunks(group):
            await session.execute(
                update(model).where(key.in_(chunk)).values({column: value, **extra})
            )


async def list_daily(
    session: AsyncSession, sol_min: int | None, sol_max: int | None
) -> Sequence[Row[Any]]:
    """Summaries in ``[sol_min, sol_max]`` by sol, as plain rows (the list is ~4700 long)."""
    query = select(*RadDaily.__table__.columns).order_by(RadDaily.sol)
    if sol_min is not None:
        query = query.where(RadDaily.sol >= sol_min)
    if sol_max is not None:
        query = query.where(RadDaily.sol <= sol_max)
    return (await session.execute(query, execution_options=FRESH)).all()


async def daily_extent(session: AsyncSession) -> tuple[int | None, datetime | None]:
    """The highest sol and the latest change of all summaries (None, None when empty)."""
    row = (
        await session.execute(select(func.max(RadDaily.sol), func.max(RadDaily.updated_at)))
    ).one()
    return row[0], row[1]


async def daily_fingerprint(session: AsyncSession) -> tuple[Any, ...]:
    """Changes whenever the summaries or the release date change (count, highest sol, latest
    change of any summary, ``Last-Modified`` of the listing): the key of cached responses."""
    row = (
        await session.execute(
            select(func.count(), func.max(RadDaily.sol), func.max(RadDaily.updated_at))
        )
    ).one()
    dataset = await get_dataset(session)
    return (*row, dataset.checksums_last_modified if dataset else None)


async def status(session: AsyncSession) -> RadStatus:
    counts = (
        await session.execute(select(RadFile.status, func.count()).group_by(RadFile.status))
    ).all()
    total, low, high, changed = (
        await session.execute(
            select(
                func.count(),
                func.min(RadDaily.sol),
                func.max(RadDaily.sol),
                func.max(RadDaily.updated_at),
            )
        )
    ).one()
    return RadStatus(
        await get_dataset(session),
        {name: count for name, count in counts},
        total,
        low,
        high,
        changed,
    )


async def export_rows(
    session: AsyncSession,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """All files, all summaries and the state row as plain dicts (files by path, daily by sol)."""
    files = (await session.execute(select(RadFile).order_by(RadFile.path))).scalars().all()
    daily = (await session.execute(select(RadDaily).order_by(RadDaily.sol))).scalars().all()
    dataset = await get_dataset(session)
    return (
        [_as_dict(row, _FILE_COLUMNS) for row in files],
        [_as_dict(row, _DAILY_COLUMNS) for row in daily],
        _as_dict(dataset, _DATASET_COLUMNS) if dataset is not None else None,
    )


async def replace_all(
    session: AsyncSession,
    files: Sequence[Mapping[str, Any]],
    daily: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any],
) -> None:
    """Replace the content of the three tables; the caller commits (one transaction)."""
    await session.execute(delete(RadDaily))
    await session.execute(delete(RadFile))
    await session.execute(delete(RadDataset))
    if files:
        await session.execute(pg_insert(RadFile), list(files))
    if daily:
        await session.execute(pg_insert(RadDaily), list(daily))
    await session.execute(pg_insert(RadDataset), [dict(dataset)])


def _as_dict(row: Any, columns: Sequence[str]) -> dict[str, Any]:
    return {name: getattr(row, name) for name in columns}


def _chunks[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[i : i + _IN_CHUNK] for i in range(0, len(items), _IN_CHUNK)]
