"""Rovers and cameras."""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cameras import camera_full_name
from app.db.models import Camera, Rover
from app.repositories._common import FRESH


async def list_rovers(session: AsyncSession) -> list[Rover]:
    stmt = select(Rover).order_by(Rover.legacy_id)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def get_rover(session: AsyncSession, rover_id: str) -> Rover | None:
    """Find a rover by slug, case-insensitively (slugs are stored in lower case)."""
    return await session.get(Rover, rover_id.lower(), populate_existing=True)


async def list_cameras(session: AsyncSession, rover_id: str) -> list[Camera]:
    stmt = select(Camera).where(Camera.rover_id == rover_id).order_by(Camera.sort_order)
    return list(await session.scalars(stmt, execution_options=FRESH))


async def get_or_create_cameras(
    session: AsyncSession, rover_id: str, codes: Iterable[str]
) -> dict[str, Camera]:
    """Return cameras keyed by upper-case code; unknown codes are appended to the order."""
    wanted = list(dict.fromkeys(code.upper() for code in codes))
    if not wanted:
        return {}
    found = await _cameras_by_code(session, rover_id, wanted)
    missing = [code for code in wanted if code not in found]
    if missing:
        await _insert_cameras(session, rover_id, missing)
        found = await _cameras_by_code(session, rover_id, wanted)
    return found


async def _cameras_by_code(
    session: AsyncSession, rover_id: str, codes: list[str]
) -> dict[str, Camera]:
    stmt = select(Camera).where(Camera.rover_id == rover_id, Camera.code.in_(codes))
    return {camera.code: camera for camera in await session.scalars(stmt)}


async def _insert_cameras(session: AsyncSession, rover_id: str, codes: list[str]) -> None:
    # max + i is race-free only because sync holds the per-rover advisory lock (stage 3);
    # a concurrent writer would at worst duplicate a sort_order, never a code.
    max_order_stmt = select(func.max(Camera.sort_order)).where(Camera.rover_id == rover_id)
    max_order: int = await session.scalar(max_order_stmt) or 0
    rows = [
        {
            "rover_id": rover_id,
            "code": code,
            "name": camera_full_name(code),
            "sort_order": max_order + i,
        }
        for i, code in enumerate(codes, start=1)
    ]
    stmt = (
        pg_insert(Camera)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[Camera.rover_id, Camera.code])
    )
    await session.execute(stmt)
