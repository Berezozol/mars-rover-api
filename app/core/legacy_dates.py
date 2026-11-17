"""Legacy (v1) earth_date: derived from the sol and the landing date, not the capture date.

See docs/LEGACY-API.md, "earth_date".
"""

import math
from datetime import date, timedelta

SECONDS_PER_SOL = 88775.244
_SECONDS_PER_DAY = 86400
_DAYS_PER_SOL = SECONDS_PER_SOL / _SECONDS_PER_DAY


def legacy_earth_date(landing_date: date, sol: int) -> date:
    """landing_date + floor(sol * 88775.244 / 86400) days."""
    return landing_date + timedelta(days=math.floor(sol * SECONDS_PER_SOL / _SECONDS_PER_DAY))


def legacy_sol_for_date(landing_date: date, earth_date: date) -> int | None:
    """Sol whose legacy earth_date equals ``earth_date``; None if before landing or skipped."""
    days = (earth_date - landing_date).days
    if days < 0:
        return None
    sol = math.ceil(days / _DAYS_PER_SOL)
    if legacy_earth_date(landing_date, sol) == earth_date:
        return sol
    return None
