"""Mars Sol Date and local mean solar time of Curiosity (stage 22, phase C, AC-34).

Vectors are computed by the architect with Mars24 (Allison & McEwen 2000), TT-UTC from the
module's leap-second table, longitude 137.4 deg E, Curiosity sol = floor(MSD + 137.4/360) - 49269.
Tolerances: MSD 2e-5 (about 2 s), LMST 2 s.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.mars_time import local_mean_time, mars_sol_date

pytestmark = pytest.mark.unit

LON_E = 137.4
SOL_OFFSET = 49269
LMST_DAY_S = 86_400.0
# One LMST second is 1.0274912517 SI seconds (a sol is 88 775.244 s = 86 400 LMST seconds).
LMST_SECOND = 1.0274912517


def hms(text: str) -> float:
    hours, minutes, seconds = (int(part) for part in text.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def at(*args: int, micro: int = 0) -> datetime:
    return datetime(*args, microsecond=micro, tzinfo=UTC)


# --- AC-34: reference vectors --------------------------------------------------------------------

MSD_VECTORS = [
    (at(2026, 3, 21, 17, 18, 0), 54111.622522),
    (at(2026, 3, 22, 17, 54, 10, micro=500_000), 54112.620215),
    (at(2012, 8, 16, 12, 0, 0), 49279.249644),
]

LMST_VECTORS = [
    (at(2012, 8, 6, 5, 17, 57), 0, "15:03:05"),  # the landing
    (at(2026, 3, 21, 17, 18, 0), 4843, "00:06:02"),
    (at(2026, 3, 22, 17, 54, 10, micro=500_000), 4844, "00:02:43"),
    (at(2012, 8, 16, 12, 0, 0), 10, "15:09:05"),
]


@pytest.mark.parametrize(
    ("moment", "msd"),
    MSD_VECTORS,
    ids=["sol-4843-start", "sol-4844-first-minutes", "sol-10-noon"],
)
def test_mars_sol_date_matches_the_mars24_vectors_ac34(moment: datetime, msd: float) -> None:
    """AC-34: MSD within 2e-5 of the Mars24 value."""
    assert mars_sol_date(moment) == pytest.approx(msd, abs=2e-5)


@pytest.mark.parametrize(
    ("moment", "sol", "lmst"),
    LMST_VECTORS,
    ids=["landing", "sol-4843-start", "sol-4844-first-minutes", "sol-10-noon"],
)
def test_local_mean_time_matches_the_mars24_vectors_ac34(
    moment: datetime, sol: int, lmst: str
) -> None:
    """AC-34: the local day number is the Curiosity sol, LMST within 2 s."""
    day, seconds = local_mean_time(moment, LON_E)

    assert day - SOL_OFFSET == sol
    assert seconds == pytest.approx(hms(lmst), abs=2.0)


def test_local_mean_time_2013_05_03_is_sol_263_ac34() -> None:
    day, _ = local_mean_time(at(2013, 5, 3, 12, 0, 0), LON_E)

    assert day - SOL_OFFSET == 263


def test_local_mean_time_day_number_is_an_integer_and_seconds_a_float_ac34() -> None:
    day, seconds = local_mean_time(at(2026, 3, 21, 17, 18, 0), LON_E)

    assert isinstance(day, int)
    assert isinstance(seconds, float)


def test_local_mean_time_accepts_any_timezone_of_the_same_instant_ac34() -> None:
    """The moment is an instant: the same one in UTC+03:00 gives the same answer."""
    utc = at(2026, 3, 21, 17, 18, 0)
    plus3 = utc.astimezone(timezone(timedelta(hours=3)))

    assert local_mean_time(plus3, LON_E) == local_mean_time(utc, LON_E)
    assert mars_sol_date(plus3) == mars_sol_date(utc)


# --- AC-34: properties ---------------------------------------------------------------------------


def test_local_mean_time_midnight_belongs_to_the_new_sol_and_starts_at_zero_ac34() -> None:
    """Edge: just before local midnight (23:59:59, old sol) and just after (00:00:01, new sol).

    Midnight is found from the 2026-03-21 17:18:00Z vector (LMST 00:06:02 of sol 4843).
    """
    reference = at(2026, 3, 21, 17, 18, 0)
    _, reference_lmst = local_mean_time(reference, LON_E)
    midnight = reference - timedelta(seconds=reference_lmst * LMST_SECOND)

    before_day, before = local_mean_time(midnight - timedelta(seconds=1), LON_E)
    after_day, after = local_mean_time(midnight + timedelta(seconds=1), LON_E)

    assert before_day - SOL_OFFSET == 4842
    assert before == pytest.approx(LMST_DAY_S - 1 / LMST_SECOND, abs=0.01)
    assert after_day - SOL_OFFSET == 4843
    assert after == pytest.approx(1 / LMST_SECOND, abs=0.01)


@pytest.mark.parametrize("offset_s", range(0, 24 * 3600, 5 * 3600 + 777), ids=lambda v: f"+{v}s")
def test_local_mean_time_seconds_stay_in_a_day_and_advance_with_si_seconds_ac34(
    offset_s: int,
) -> None:
    start = at(2026, 3, 21, 17, 18, 0) + timedelta(seconds=offset_s)

    _, first = local_mean_time(start, LON_E)
    _, second = local_mean_time(start + timedelta(seconds=100), LON_E)

    assert 0.0 <= first < LMST_DAY_S
    assert 0.0 <= second < LMST_DAY_S
    assert (second - first) % LMST_DAY_S == pytest.approx(100 / LMST_SECOND, abs=1e-3)


def test_local_mean_time_longitude_shifts_the_clock_4_minutes_per_degree_ac34() -> None:
    moment = at(2026, 3, 21, 17, 18, 0)

    _, east = local_mean_time(moment, LON_E)
    _, shifted = local_mean_time(moment, LON_E + 1.0)

    assert (shifted - east) % LMST_DAY_S == pytest.approx(LMST_DAY_S / 360.0, abs=1e-6)


def test_mars_sol_date_one_sol_later_is_msd_plus_one_ac34() -> None:
    moment = at(2026, 3, 21, 17, 18, 0)
    later = moment + timedelta(seconds=88_775.244)

    assert mars_sol_date(later) - mars_sol_date(moment) == pytest.approx(1.0, abs=1e-6)
