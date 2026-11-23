"""Weather per sol: raw files, daily summaries, hourly means and the state of the data."""

import struct
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WeatherDaily, WeatherDataset, WeatherFile, WeatherHourly
from app.repositories._common import FRESH

# Columns whose change makes a summary "changed" (``updated_at`` moves, the hours are rewritten);
# the parser revision alone does not.
DAILY_VALUE_COLUMNS = tuple(
    column.name
    for column in WeatherDaily.__table__.columns
    if column.name not in ("rover_id", "sol", "updated_at", "parser_revision")
)
DAILY_COLUMNS = tuple(column.name for column in WeatherDaily.__table__.columns)
HOURLY_COLUMNS = tuple(column.name for column in WeatherHourly.__table__.columns)
DATASET_COLUMNS = tuple(column.name for column in WeatherDataset.__table__.columns)
_FILE_KEYS = ("rover_id", "path")
_IN_CHUNK = 5000


@dataclass(frozen=True)
class WeatherStatus:
    """What ``weather status`` shows for one rover."""

    dataset: WeatherDataset | None
    files_by_status: dict[str, int]
    sols: int
    min_sol: int | None
    max_sol: int | None
    updated_at: datetime | None
    file_sols: frozenset[int]


# --- files ---------------------------------------------------------------------------------


async def list_files(session: AsyncSession, rover: str) -> dict[int, dict[str, WeatherFile]]:
    """Every known file of the rover, by sol and path."""
    query = select(WeatherFile).where(WeatherFile.rover_id == rover)
    found: dict[int, dict[str, WeatherFile]] = defaultdict(dict)
    for row in (await session.scalars(query, execution_options=FRESH)).all():
        found[row.sol][row.path] = row
    return found


async def upsert_files(session: AsyncSession, rows: Sequence[Mapping[str, Any]]) -> None:
    """Insert files or replace the stored ones; every row carries all the columns it sets."""
    if not rows:
        return
    statement = pg_insert(WeatherFile)
    columns = {
        name: getattr(statement.excluded, name) for name in rows[0] if name not in _FILE_KEYS
    }
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[WeatherFile.rover_id, WeatherFile.path], set_=columns
        ),
        list(rows),
    )


async def delete_files(session: AsyncSession, rover: str, paths: Collection[str]) -> None:
    for chunk in _chunks(sorted(paths)):
        await session.execute(
            delete(WeatherFile).where(WeatherFile.rover_id == rover, WeatherFile.path.in_(chunk))
        )


async def delete_files_of_sols(session: AsyncSession, rover: str, sols: Collection[int]) -> None:
    for chunk in _chunks(sorted(sols)):
        await session.execute(
            delete(WeatherFile).where(WeatherFile.rover_id == rover, WeatherFile.sol.in_(chunk))
        )


async def list_sol_files(session: AsyncSession, rover: str, sol: int) -> Sequence[WeatherFile]:
    """The files a sol was computed from (read successfully), by product."""
    query = (
        select(WeatherFile)
        .where(
            WeatherFile.rover_id == rover,
            WeatherFile.sol == sol,
            WeatherFile.status == "parsed",
        )
        .order_by(WeatherFile.product, WeatherFile.path)
    )
    return (await session.scalars(query, execution_options=FRESH)).all()


# --- daily and hourly ----------------------------------------------------------------------


async def daily_revisions(session: AsyncSession, rover: str) -> dict[int, int]:
    """The parser revision of every stored summary of the rover, by sol."""
    query = select(WeatherDaily.sol, WeatherDaily.parser_revision).where(
        WeatherDaily.rover_id == rover
    )
    return {sol: revision for sol, revision in (await session.execute(query)).all()}


async def upsert_daily(session: AsyncSession, row: Mapping[str, Any]) -> bool:
    """Insert the summary or replace the stored one. A summary with the same values is left
    alone (``updated_at`` stays; only the parser revision is brought up to date). True when the
    values were written."""
    statement = pg_insert(WeatherDaily).values(**row)
    excluded = statement.excluded
    changed = or_(
        *(
            getattr(WeatherDaily, name).is_distinct_from(getattr(excluded, name))
            for name in DAILY_VALUE_COLUMNS
        )
    )
    values = {name: getattr(excluded, name) for name in DAILY_VALUE_COLUMNS}
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=[WeatherDaily.rover_id, WeatherDaily.sol],
            set_={**values, "parser_revision": excluded.parser_revision, "updated_at": func.now()},
            where=changed,
        )
    )
    if result.rowcount:  # type: ignore[attr-defined]
        return True
    await session.execute(
        update(WeatherDaily)
        .where(WeatherDaily.rover_id == row["rover_id"], WeatherDaily.sol == row["sol"])
        .values(parser_revision=row["parser_revision"])
    )
    return False


async def replace_hourly(
    session: AsyncSession, rover: str, sol: int, rows: Sequence[Mapping[str, Any]]
) -> bool:
    """Replace the hours of one sol (the summary must exist); an identical set is left alone.
    True when the stored hours changed."""
    stored = await session.execute(
        select(*WeatherHourly.__table__.columns).where(
            WeatherHourly.rover_id == rover, WeatherHourly.sol == sol
        )
    )
    old = {row.hour: dict(row._mapping) for row in stored}
    # ``coverage`` is a REAL column: compare it as it is stored (single precision).
    new = {row["hour"]: {**row, "coverage": _as_real(row["coverage"])} for row in rows}
    if old == new:
        return False
    await session.execute(
        delete(WeatherHourly).where(WeatherHourly.rover_id == rover, WeatherHourly.sol == sol)
    )
    if rows:
        await session.execute(pg_insert(WeatherHourly), list(rows))
    return True


async def delete_daily(session: AsyncSession, rover: str, sols: Collection[int]) -> int:
    """Delete the summaries (their hours go with them); returns how many existed."""
    deleted = 0
    for chunk in _chunks(sorted(sols)):
        result = await session.execute(
            delete(WeatherDaily).where(WeatherDaily.rover_id == rover, WeatherDaily.sol.in_(chunk))
        )
        deleted += result.rowcount  # type: ignore[attr-defined]
    return deleted


async def list_daily(
    session: AsyncSession, rover: str, sol_min: int | None, sol_max: int | None
) -> Sequence[Row[Any]]:
    """Summaries in ``[sol_min, sol_max]`` by sol, as plain rows (a list is ~4700 long)."""
    query = (
        select(*WeatherDaily.__table__.columns)
        .where(WeatherDaily.rover_id == rover)
        .order_by(WeatherDaily.sol)
    )
    if sol_min is not None:
        query = query.where(WeatherDaily.sol >= sol_min)
    if sol_max is not None:
        query = query.where(WeatherDaily.sol <= sol_max)
    return (await session.execute(query, execution_options=FRESH)).all()


async def get_daily(session: AsyncSession, rover: str, sol: int) -> Row[Any] | None:
    query = select(*WeatherDaily.__table__.columns).where(
        WeatherDaily.rover_id == rover, WeatherDaily.sol == sol
    )
    return (await session.execute(query, execution_options=FRESH)).one_or_none()


async def list_hourly(session: AsyncSession, rover: str, sol: int) -> Sequence[Row[Any]]:
    query = (
        select(*WeatherHourly.__table__.columns)
        .where(WeatherHourly.rover_id == rover, WeatherHourly.sol == sol)
        .order_by(WeatherHourly.hour)
    )
    return (await session.execute(query, execution_options=FRESH)).all()


async def daily_extent(session: AsyncSession, rover: str) -> tuple[int | None, datetime | None]:
    """The highest sol and the latest change of the rover's summaries (None, None if empty)."""
    row = (
        await session.execute(
            select(func.max(WeatherDaily.sol), func.max(WeatherDaily.updated_at)).where(
                WeatherDaily.rover_id == rover
            )
        )
    ).one()
    return row[0], row[1]


async def daily_fingerprint(session: AsyncSession, rover: str) -> tuple[Any, ...]:
    """Changes whenever the rover's summaries change (count, highest sol, latest change of any
    summary, ``updated_at`` of the dataset row): the key of cached responses."""
    row = (
        await session.execute(
            select(
                func.count(), func.max(WeatherDaily.sol), func.max(WeatherDaily.updated_at)
            ).where(WeatherDaily.rover_id == rover)
        )
    ).one()
    dataset = await get_dataset(session, rover)
    return (*row, dataset.updated_at if dataset else None)


# --- state of the dataset ------------------------------------------------------------------


async def get_dataset(session: AsyncSession, rover: str) -> WeatherDataset | None:
    return await session.get(WeatherDataset, rover, populate_existing=True)


async def save_dataset(session: AsyncSession, rover: str, **fields: Any) -> None:
    """Create the state row if missing and set ``fields`` on it (``ON CONFLICT``)."""
    statement = pg_insert(WeatherDataset).values(rover_id=rover, **fields)
    if fields:
        statement = statement.on_conflict_do_update(
            index_elements=[WeatherDataset.rover_id], set_=fields
        )
    else:
        statement = statement.on_conflict_do_nothing()
    await session.execute(statement)


async def touch_dataset(session: AsyncSession, rover: str) -> None:
    """Mark that a summary of the rover changed: the cache fingerprint and ``updated_at``."""
    await save_dataset(session, rover, changed_at=func.now(), updated_at=func.now())


async def status(session: AsyncSession, rover: str) -> WeatherStatus:
    counts = (
        await session.execute(
            select(WeatherFile.status, func.count())
            .where(WeatherFile.rover_id == rover)
            .group_by(WeatherFile.status)
        )
    ).all()
    total, low, high, changed = (
        await session.execute(
            select(
                func.count(),
                func.min(WeatherDaily.sol),
                func.max(WeatherDaily.sol),
                func.max(WeatherDaily.updated_at),
            ).where(WeatherDaily.rover_id == rover)
        )
    ).one()
    daily_sols = (
        await session.scalars(select(WeatherDaily.sol).where(WeatherDaily.rover_id == rover))
    ).all()
    file_sols = (
        await session.scalars(
            select(WeatherFile.sol).where(WeatherFile.rover_id == rover).distinct()
        )
    ).all()
    return WeatherStatus(
        await get_dataset(session, rover),
        {name: count for name, count in counts},
        total,
        low,
        high,
        changed,
        frozenset(file_sols) | frozenset(daily_sols),
    )


# --- export and import ---------------------------------------------------------------------


async def export_rows(
    session: AsyncSession, rover: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """The rover's summaries, hours and state row as plain dicts (by sol, then hour)."""
    daily = await session.execute(
        select(*WeatherDaily.__table__.columns)
        .where(WeatherDaily.rover_id == rover)
        .order_by(WeatherDaily.sol)
    )
    hourly = await session.execute(
        select(*WeatherHourly.__table__.columns)
        .where(WeatherHourly.rover_id == rover)
        .order_by(WeatherHourly.sol, WeatherHourly.hour)
    )
    dataset = await session.execute(
        select(*WeatherDataset.__table__.columns).where(WeatherDataset.rover_id == rover)
    )
    state = dataset.mappings().one_or_none()
    return (
        [dict(row) for row in daily.mappings()],
        [dict(row) for row in hourly.mappings()],
        dict(state) if state is not None else None,
    )


async def replace_rover(
    session: AsyncSession,
    rover: str,
    daily: Sequence[Mapping[str, Any]],
    hourly: Sequence[Mapping[str, Any]],
    dataset: Mapping[str, Any] | None,
) -> None:
    """Replace the data of one rover; the caller commits (one transaction for all rovers)."""
    await session.execute(delete(WeatherDaily).where(WeatherDaily.rover_id == rover))
    await session.execute(delete(WeatherDataset).where(WeatherDataset.rover_id == rover))
    for chunk in _chunks(daily):
        await session.execute(pg_insert(WeatherDaily), list(chunk))
    for chunk in _chunks(hourly):
        await session.execute(pg_insert(WeatherHourly), list(chunk))
    if dataset is not None:
        await session.execute(pg_insert(WeatherDataset), [dict(dataset)])


def _as_real(value: float) -> float:
    """``value`` rounded to single precision, as a REAL column returns it."""
    return struct.unpack("f", struct.pack("f", value))[0]


def _chunks[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[i : i + _IN_CHUNK] for i in range(0, len(items), _IN_CHUNK)]
