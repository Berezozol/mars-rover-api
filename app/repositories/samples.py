"""Perseverance samples, the state of their source page and the links to photos and stops."""

from collections.abc import Collection, Sequence
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, column, delete, select, true, values
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Photo, RoverSample, RoverWaypoint, SampleDataset, SolSummary
from app.repositories._common import FRESH
from app.sources.samples import SampleRecord


async def list_samples(session: AsyncSession, rover_id: str) -> Sequence[RoverSample]:
    """All samples of the rover by number."""
    query = select(RoverSample).where(RoverSample.rover_id == rover_id).order_by(RoverSample.number)
    return (await session.scalars(query, execution_options=FRESH)).all()


async def get_sample(session: AsyncSession, rover_id: str, number: int) -> RoverSample | None:
    return await session.get(RoverSample, (rover_id, number), populate_existing=True)


async def replace_samples(
    session: AsyncSession, rover_id: str, items: Sequence[SampleRecord]
) -> None:
    """Delete the rover's samples and insert ``items``; the caller commits (one transaction)."""
    await session.execute(delete(RoverSample).where(RoverSample.rover_id == rover_id))
    rows = [_row(rover_id, item) for item in items]
    if rows:
        await session.execute(pg_insert(RoverSample), rows)


def _row(rover_id: str, item: SampleRecord) -> dict[str, Any]:
    row = {"rover_id": rover_id, **asdict(item)}
    if item.height_cm is not None:
        row["height_cm"] = Decimal(str(item.height_cm))
    return row


async def get_dataset(session: AsyncSession, rover_id: str) -> SampleDataset | None:
    return await session.get(SampleDataset, rover_id, populate_existing=True)


async def save_dataset(session: AsyncSession, rover_id: str, **fields: Any) -> None:
    """Create the state row if missing and set ``fields`` on it (``ON CONFLICT``)."""
    statement = pg_insert(SampleDataset).values(rover_id=rover_id, **fields)
    if fields:
        statement = statement.on_conflict_do_update(
            index_elements=[SampleDataset.rover_id], set_=fields
        )
    else:
        statement = statement.on_conflict_do_nothing()
    await session.execute(statement)


async def sample_numbers_by_sol(session: AsyncSession, rover_id: str, sol: int) -> list[int]:
    """Numbers of the samples sealed in ``sol``, ascending."""
    query = (
        select(RoverSample.number)
        .where(RoverSample.rover_id == rover_id, RoverSample.sol == sol)
        .order_by(RoverSample.number)
    )
    return list((await session.scalars(query, execution_options=FRESH)).all())


async def sol_positions(
    session: AsyncSession, rover_id: str, sols: Collection[int]
) -> dict[int, list[tuple[int, int]]]:
    """Distinct ``(site, drive)`` pairs of each sol's photos (photos without a pair skipped)."""
    if not sols:
        return {}
    query = (
        select(Photo.sol, Photo.site, Photo.drive)
        .where(
            Photo.rover_id == rover_id,
            Photo.sol.in_(sols),
            Photo.site.is_not(None),
            Photo.drive.is_not(None),
        )
        .distinct()
        .order_by(Photo.sol, Photo.site, Photo.drive)
    )
    result: dict[int, list[tuple[int, int]]] = {}
    for sol, site, drive in await session.execute(query):
        result.setdefault(sol, []).append((site, drive))
    return result


async def sol_photo_totals(
    session: AsyncSession, rover_id: str, sols: Collection[int]
) -> dict[int, int]:
    """Photos per sol (all types) from ``sol_summaries``; a sol without a row is absent."""
    if not sols:
        return {}
    query = select(SolSummary.sol, SolSummary.total_photos).where(
        SolSummary.rover_id == rover_id, SolSummary.sol.in_(sols)
    )
    return {sol: total for sol, total in await session.execute(query)}


async def waypoints_for_samples(
    session: AsyncSession,
    rover_id: str,
    by_stop: dict[int, tuple[int, int]],
    by_sol: dict[int, int],
) -> tuple[dict[int, RoverWaypoint], dict[int, RoverWaypoint]]:
    """Two lookups keyed by sample number, one ``LATERAL`` query each.

    ``by_stop``: number -> ``(site, drive)``, the last waypoint of that site whose drive is
    not above ``drive`` (the rule of the photo card). ``by_sol``: number -> sol, the last
    waypoint in driving order with a sol not above it. Samples without a hit are absent.
    """
    return (
        await _nearest_before_drive(session, rover_id, by_stop),
        await _last_before_sol(session, rover_id, by_sol),
    )


async def _nearest_before_drive(
    session: AsyncSession, rover_id: str, by_stop: dict[int, tuple[int, int]]
) -> dict[int, RoverWaypoint]:
    if not by_stop:
        return {}
    keys = values(
        column("key", Integer), column("site", Integer), column("drive", Integer), name="k"
    ).data([(number, site, drive) for number, (site, drive) in by_stop.items()])
    waypoint = (
        select(RoverWaypoint)
        .where(
            RoverWaypoint.rover_id == rover_id,
            RoverWaypoint.site == keys.c.site,
            RoverWaypoint.drive <= keys.c.drive,
        )
        .order_by(RoverWaypoint.drive.desc())
        .limit(1)
        .lateral()
    )
    return await _lateral_rows(session, keys, waypoint)


async def _last_before_sol(
    session: AsyncSession, rover_id: str, by_sol: dict[int, int]
) -> dict[int, RoverWaypoint]:
    if not by_sol:
        return {}
    keys = values(column("key", Integer), column("sol", Integer), name="k").data(
        list(by_sol.items())
    )
    waypoint = (
        select(RoverWaypoint)
        .where(RoverWaypoint.rover_id == rover_id, RoverWaypoint.sol <= keys.c.sol)
        .order_by(RoverWaypoint.sol.desc(), RoverWaypoint.site.desc(), RoverWaypoint.drive.desc())
        .limit(1)
        .lateral()
    )
    return await _lateral_rows(session, keys, waypoint)


async def _lateral_rows(session: AsyncSession, keys: Any, lateral: Any) -> dict[int, RoverWaypoint]:
    found = aliased(RoverWaypoint, lateral)
    query = select(keys.c.key, found).select_from(keys.join(lateral, true()))
    result = await session.execute(query, execution_options=FRESH)
    return {key: waypoint for key, waypoint in result}
