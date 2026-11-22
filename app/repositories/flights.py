"""Ingenuity flights (the NASA flight log) with the photo count of the flight's sol."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngenuityFlight, SolSummary
from app.repositories._common import FRESH, AnySelect

INGENUITY = "ingenuity"


def _with_photo_counts() -> AnySelect:
    """Flights left-joined with ``sol_summaries`` of Ingenuity (all sample types)."""
    total = SolSummary.total_photos
    return select(IngenuityFlight, total).outerjoin(
        SolSummary,
        (SolSummary.rover_id == INGENUITY) & (SolSummary.sol == IngenuityFlight.sol),
    )


async def list_flights(session: AsyncSession) -> list[tuple[IngenuityFlight, int]]:
    """All flights by number with the photos of their sol (0 for a sol without photos)."""
    stmt = _with_photo_counts().order_by(IngenuityFlight.flight)
    rows = await session.execute(stmt, execution_options=FRESH)
    return [(flight, int(total or 0)) for flight, total in rows]


async def get_flight(session: AsyncSession, number: int) -> tuple[IngenuityFlight, int] | None:
    """One flight with its photo count, or None if there is no such flight."""
    stmt = _with_photo_counts().where(IngenuityFlight.flight == number)
    row = (await session.execute(stmt, execution_options=FRESH)).first()
    if row is None:
        return None
    return row[0], int(row[1] or 0)


async def flight_by_sol(session: AsyncSession, sol: int) -> int | None:
    """The number of the flight made in ``sol``, if any (``sol`` is unique)."""
    stmt = select(IngenuityFlight.flight).where(IngenuityFlight.sol == sol)
    return await session.scalar(stmt, execution_options=FRESH)
