"""Data freshness of the synced rovers (docs/API.md, "GET /health/sync")."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories import photos as photos_repo
from app.repositories import sync_runs as runs_repo
from app.sources.registry import SYNCABLE_ROVERS

# Rovers with a sync, in the order of the response.
FRESHNESS_ROVERS: tuple[str, ...] = tuple(sorted(SYNCABLE_ROVERS))


@dataclass(frozen=True)
class RoverFreshness:
    rover: str
    last_success_at: datetime | None
    newest_photo_date: date | None
    sync_stale: bool
    photos_stale: bool

    @property
    def stale(self) -> bool:
        return self.sync_stale or self.photos_stale


@dataclass(frozen=True)
class SyncFreshness:
    rovers: list[RoverFreshness]

    @property
    def stale(self) -> bool:
        return any(rover.stale for rover in self.rovers)

    @property
    def status(self) -> str:
        return "stale" if self.stale else "ok"


async def get_sync_freshness(
    session: AsyncSession, settings: Settings, now: datetime
) -> SyncFreshness:
    """Freshness of every synced rover at ``now`` (an aware datetime)."""
    rovers = [
        await _rover_freshness(session, settings, rover_id, now) for rover_id in FRESHNESS_ROVERS
    ]
    return SyncFreshness(rovers)


async def _rover_freshness(
    session: AsyncSession, settings: Settings, rover_id: str, now: datetime
) -> RoverFreshness:
    last_success = await runs_repo.last_success_finished_at(session, rover_id)
    newest = await photos_repo.newest_earth_date(session, rover_id)
    return RoverFreshness(
        rover=rover_id,
        last_success_at=last_success,
        newest_photo_date=newest,
        sync_stale=last_success is None
        or now - last_success > timedelta(hours=settings.sync_stale_hours),
        photos_stale=newest is None
        or now.astimezone(UTC).date() - newest > timedelta(days=settings.photos_stale_days),
    )


class FreshnessCache:
    """Keeps the last freshness result for ``ttl`` seconds (0 = never cached).

    Shields the database from frequent /health/sync polling; failures are not cached.
    """

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self.clock = clock
        self._value: SyncFreshness | None = None
        self._expires_at = 0.0

    async def get(self, load: Callable[[], Awaitable[SyncFreshness]]) -> SyncFreshness:
        now = self.clock()
        if self._value is not None and now < self._expires_at:
            return self._value
        value = await load()
        if self.ttl > 0:
            self._value, self._expires_at = value, now + self.ttl
        return value

    def clear(self) -> None:
        self._value = None
