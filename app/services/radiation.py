"""Read logic of ``GET /api/v2/rovers/{rover}/radiation[/{sol}]`` (docs/API.md, stages 21-22).

The daily summaries are stored in uGy/h as the source gives them; the API converts on read:
doses to uGy/day (x24), the rate of detector B and the coverage from the stored counts and
live time. The hours of a sol stay in uGy/h; the 24 hours are made here from the stored rows
(only the hours with observations are stored).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidParam, NotFoundError, SolNotFound
from app.core.mars_time import HOUR_LIVE_S
from app.db.models import RadDaily, RadHourly
from app.repositories import rad as rad_repo
from app.services.rover_lookup import find_rover

RAD_ROVER = "curiosity"
# Length of a sol in seconds: ``coverage`` is the live time over it.
SOL_SECONDS = 88_775.244
RAD_CREDIT = "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node, MSL-M-RAD-3-RDR-V1.0"
RAD_CITATION = (
    "J. Peterson, S. Rafkin, C. Zeitlin, B. Ehresmann, E. Weigle, S. Jeffers, and "
    "D.M. Hassler, MSL MARS RADIATION ASSESSMENT DETECTOR RDR V1.0, MSL-M-RAD-3-RDR-V1.0, "
    "NASA Planetary Data System, 2013."
)


@dataclass(frozen=True)
class RadiationData:
    rover: str
    updated_at: datetime | None
    source_max_sol: int | None
    source_release: date | None
    items: Sequence[Row[Any]]


@dataclass(frozen=True)
class RadiationSolData:
    """One sol: its summary row and its 24 hours (``None``: not calculated or refused)."""

    rover: str
    row: RadDaily
    hours: list[dict[str, Any]] | None


class RadiationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(self, rover: str, sol_min: int | None, sol_max: int | None) -> None:
        """The checks of ``list`` without the query (so a cached response can be served)."""
        found = await find_rover(self._session, rover)
        if found.id != RAD_ROVER:
            raise NotFoundError(f"Rover {found.id} has no radiation data")
        if sol_min is not None and sol_max is not None and sol_min > sol_max:
            raise InvalidParam("sol_min", "sol_min must be less than or equal to sol_max")

    async def fingerprint(self) -> tuple[Any, ...]:
        """Changes when the stored summaries change; the key of cached responses."""
        return await rad_repo.daily_fingerprint(self._session)

    async def list(
        self, rover: str, sol_min: int | None = None, sol_max: int | None = None
    ) -> RadiationData:
        """Summaries of Curiosity by sol, optionally within ``[sol_min, sol_max]``.

        Another existing rover -> ``NotFoundError``, an unknown one -> ``RoverNotFound``,
        ``sol_min`` > ``sol_max`` -> ``InvalidParam``. No data yet: no items and null dates.
        """
        await self.check(rover, sol_min, sol_max)
        items = await rad_repo.list_daily(self._session, sol_min, sol_max)
        max_sol, updated_at = await rad_repo.daily_extent(self._session)
        if max_sol is None:
            return RadiationData(RAD_ROVER, None, None, None, items)
        dataset = await rad_repo.get_dataset(self._session)
        release = _http_date(dataset.checksums_last_modified) if dataset else None
        return RadiationData(RAD_ROVER, updated_at, max_sol, release, items)

    async def get_sol(self, rover: str, sol: int) -> RadiationSolData:
        """One sol of Curiosity with its hours; a sol without a summary -> ``SolNotFound``."""
        await self.check(rover, None, None)
        row = await rad_repo.get_daily(self._session, sol)
        if row is None:
            raise SolNotFound()
        stored = await rad_repo.list_hourly(self._session, sol)
        return RadiationSolData(RAD_ROVER, row, _hours(row, stored))


def _hours(row: RadDaily, stored: Sequence[RadHourly]) -> list[dict[str, Any]] | None:
    """The 24 hours of a sol; ``None`` when the hours are not calculated (``hourly_revision`` 0)
    or were refused (``hourly_reject``). Hours without observations are empty."""
    if row.hourly_revision == 0 or row.hourly_reject is not None:
        return None
    by_hour = {item.hour: item for item in stored}
    return [_hour(hour, by_hour.get(hour)) for hour in range(24)]


def _hour(hour: int, item: RadHourly | None) -> dict[str, Any]:
    if item is None:
        return {
            "hour": hour,
            "coverage": 0.0,
            "observations": 0,
            "dose_e_ugy_h": None,
            "dose_b_ugy_h": None,
            "count_rate_b_per_s": None,
        }
    return {
        "hour": hour,
        "coverage": round(min(1.0, item.live_s / HOUR_LIVE_S), 2),
        "observations": item.n_obs,
        "dose_e_ugy_h": round(item.dose_e_mean, 2),
        "dose_b_ugy_h": round(item.dose_b_mean, 2),
        "count_rate_b_per_s": round(item.bu_count / item.live_s, 3),
    }


def _http_date(value: str | None) -> date | None:
    """The UTC date of an HTTP ``Last-Modified`` value; ``None`` if absent or unreadable."""
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return (moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment).astimezone(UTC).date()
