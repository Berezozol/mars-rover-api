"""Fill photos.local_time_s / width / height of stored photos from ``extra`` (stage 18B-2).

Offline: no source is contacted. Sols are walked upwards in batches; each batch is one short
transaction, so a stop (Ctrl+C, crash) loses at most the running batch and a rerun continues
where the work remains. The update is idempotent (see ``meta_backfill_batch``).
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories import photos as photos_repo

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SOLS = 50


@dataclass
class RoverMetaResult:
    rover_id: str
    batches: int = 0
    looked_at: int = 0
    updated: int = 0
    seconds: float = 0.0


@dataclass
class MetaBackfillResult:
    rovers: list[RoverMetaResult] = field(default_factory=list)

    @property
    def updated(self) -> int:
        return sum(r.updated for r in self.rovers)


class MetaBackfillService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def run(
        self,
        rovers: Sequence[str],
        *,
        from_sol: int = 0,
        to_sol: int | None = None,
        batch_sols: int = DEFAULT_BATCH_SOLS,
    ) -> MetaBackfillResult:
        if batch_sols < 1:
            raise ValueError("batch_sols must be at least 1")
        result = MetaBackfillResult()
        for rover_id in rovers:
            result.rovers.append(await self._run_rover(rover_id, from_sol, to_sol, batch_sols))
        return result

    async def _run_rover(
        self, rover_id: str, from_sol: int, to_sol: int | None, batch_sols: int
    ) -> RoverMetaResult:
        out = RoverMetaResult(rover_id)
        started = time.monotonic()
        async with self._factory() as session:
            bounds = await photos_repo.sol_bounds(session, rover_id)
        if bounds is None:
            return out
        first, last = (
            max(from_sol, bounds[0]),
            bounds[1] if to_sol is None else min(to_sol, bounds[1]),
        )
        for start in range(first, last + 1, batch_sols):
            end = min(start + batch_sols - 1, last)
            async with self._factory() as session:
                looked, updated = await photos_repo.meta_backfill_batch(
                    session, rover_id, start, end
                )
                await session.commit()
            out.batches += 1
            out.looked_at += looked
            out.updated += updated
            logger.info(
                "meta backfill %s sols %d-%d of %d: looked=%d updated=%d (total updated %d)",
                rover_id, start, end, last, looked, updated, out.updated,
            )  # fmt: skip
        out.seconds = time.monotonic() - started
        return out
