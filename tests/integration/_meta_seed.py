"""Helpers for the 18B-2 stored-columns tests: seed the meta dataset, read columns, null them."""

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.photos import recompute_sol_summaries, upsert_photos
from app.repositories.rovers import refresh_rover_stats
from tests.api.v2._meta import ALL_ROVERS, MRow, build_meta_rows, record_of

SessionFactory = async_sessionmaker[AsyncSession]


async def seed_meta_rows(factory: SessionFactory, rovers: Iterable[str] = ALL_ROVERS) -> list[MRow]:
    """Write the `_meta.py` dataset through the sync write path; return the rows with ids."""
    chosen = set(rovers)
    rows = [r for r in build_meta_rows() if r.rover in chosen]
    for rover in ALL_ROVERS:
        mine = [r for r in rows if r.rover == rover]
        if not mine:
            continue
        async with factory() as session:
            await upsert_photos(session, rover, [record_of(r) for r in mine])
            await recompute_sol_summaries(session, rover, {r.sol for r in mine})
            await refresh_rover_stats(session, rover)
            await session.commit()
    async with factory() as session:
        id_of = dict((await session.execute(text("SELECT source_id, id FROM photos"))).all())
    return [replace(r, id=id_of[r.source_id]) for r in rows]


async def null_meta_columns(factory: SessionFactory, where: str = "true") -> None:
    """Make photos look like they were stored before migration 0013 (no data in the columns)."""
    async with factory() as session:
        await session.execute(
            text(
                f"UPDATE photos SET local_time_s = NULL, width = NULL, height = NULL WHERE {where}"
            )
        )
        await session.commit()


async def stored_meta(factory: SessionFactory) -> dict[str, tuple[Any, Any, Any]]:
    """source_id -> (local_time_s, width, height) as stored."""
    async with factory() as session:
        rows = await session.execute(
            text("SELECT source_id, local_time_s, width, height FROM photos")
        )
        return {sid: (t, w, h) for sid, t, w, h in rows}


def expected_meta(row: MRow) -> tuple[int | None, int | None, int | None]:
    """What the columns must hold for a dataset row: size columns are both set or both NULL."""
    width, height = row.size if row.size is not None else (None, None)
    return row.time_s, width, height
