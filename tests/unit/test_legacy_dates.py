"""Legacy (v1) earth_date <-> sol conversion, docs/LEGACY-API.md "earth_date"."""

import math
from datetime import date, timedelta
from itertools import pairwise

import pytest

from app.core.legacy_dates import SECONDS_PER_SOL, legacy_earth_date, legacy_sol_for_date

pytestmark = pytest.mark.unit

CURIOSITY_LANDING = date(2012, 8, 6)

LANDINGS = {
    "curiosity": date(2012, 8, 6),
    "perseverance": date(2021, 2, 18),
    "opportunity": date(2004, 1, 25),
    "spirit": date(2004, 1, 4),
}

MAX_SOL = 5000

# Test vectors from the original project's tests (Curiosity).
VECTORS = [
    (0, date(2012, 8, 6)),
    (1, date(2012, 8, 7)),
    (30, date(2012, 9, 5)),
    (100, date(2012, 11, 16)),
    (829, date(2014, 12, 5)),
]


def _reference_date(landing: date, sol: int) -> date:
    """The documented formula, same float order of operations as the original."""
    return landing + timedelta(days=math.floor(sol * 88775.244 / 86400))


def test_seconds_per_sol_matches_original_constant() -> None:
    assert SECONDS_PER_SOL == 88775.244


@pytest.mark.parametrize(("sol", "expected"), VECTORS, ids=[f"sol{s}" for s, _ in VECTORS])
def test_legacy_earth_date_vector_returns_expected_date(sol: int, expected: date) -> None:
    assert legacy_earth_date(CURIOSITY_LANDING, sol) == expected


@pytest.mark.parametrize(("sol", "expected"), VECTORS, ids=[f"sol{s}" for s, _ in VECTORS])
def test_legacy_sol_for_date_vector_returns_expected_sol(sol: int, expected: date) -> None:
    assert legacy_sol_for_date(CURIOSITY_LANDING, expected) == sol


@pytest.mark.parametrize("rover", LANDINGS, ids=list(LANDINGS))
def test_legacy_earth_date_all_sols_match_documented_formula(rover: str) -> None:
    landing = LANDINGS[rover]
    mismatches = [
        sol
        for sol in range(MAX_SOL + 1)
        if legacy_earth_date(landing, sol) != _reference_date(landing, sol)
    ]
    assert mismatches == []


@pytest.mark.parametrize("rover", LANDINGS, ids=list(LANDINGS))
def test_legacy_round_trip_sol_to_date_to_sol_returns_same_sol(rover: str) -> None:
    landing = LANDINGS[rover]
    failures = [
        sol
        for sol in range(MAX_SOL + 1)
        if legacy_sol_for_date(landing, legacy_earth_date(landing, sol)) != sol
    ]
    assert failures == []


@pytest.mark.parametrize("rover", LANDINGS, ids=list(LANDINGS))
def test_legacy_earth_date_distinct_sols_give_distinct_increasing_dates(rover: str) -> None:
    landing = LANDINGS[rover]
    dates = [legacy_earth_date(landing, sol) for sol in range(MAX_SOL + 1)]
    assert len(set(dates)) == len(dates)
    assert all(a < b for a, b in pairwise(dates))


@pytest.mark.parametrize("rover", LANDINGS, ids=list(LANDINGS))
def test_legacy_sol_for_date_skipped_dates_return_none(rover: str) -> None:
    landing = LANDINGS[rover]
    sol_by_date = {legacy_earth_date(landing, sol): sol for sol in range(MAX_SOL + 1)}
    last = legacy_earth_date(landing, MAX_SOL)

    skipped: list[date] = []
    day = landing
    while day <= last:
        if day in sol_by_date:
            assert legacy_sol_for_date(landing, day) == sol_by_date[day], day
        else:
            skipped.append(day)
        day += timedelta(days=1)

    # A sol is ~1.0275 days, so roughly one date in 37 has no sol.
    total_days = (last - landing).days + 1
    assert total_days - MAX_SOL - 1 == len(skipped)
    assert len(skipped) > 100
    assert [d for d in skipped if legacy_sol_for_date(landing, d) is not None] == []


@pytest.mark.parametrize("days_before", [1, 2, 365], ids=["1d", "2d", "1y"])
def test_legacy_sol_for_date_before_landing_returns_none(days_before: int) -> None:
    earth_date = CURIOSITY_LANDING - timedelta(days=days_before)
    assert legacy_sol_for_date(CURIOSITY_LANDING, earth_date) is None


def test_legacy_sol_for_date_landing_day_returns_sol_zero() -> None:
    assert legacy_sol_for_date(CURIOSITY_LANDING, CURIOSITY_LANDING) == 0


# Stage 8 vectors for Spirit and Opportunity (docs/proposals/stage-8-mer-import.md, section 3;
# the last rows match the old API's max_sol / max_date).
MER_VECTORS = [
    ("spirit", 1, date(2004, 1, 5)),
    ("spirit", 100, date(2004, 4, 15)),
    ("spirit", 2208, date(2010, 3, 21)),
    ("opportunity", 1, date(2004, 1, 26)),
    ("opportunity", 1000, date(2006, 11, 17)),
    ("opportunity", 5111, date(2018, 6, 11)),
]


@pytest.mark.parametrize(
    ("rover", "sol", "expected"), MER_VECTORS, ids=[f"{r}-sol{s}" for r, s, _ in MER_VECTORS]
)
def test_legacy_earth_date_mer_vector_returns_expected_date(
    rover: str, sol: int, expected: date
) -> None:
    assert legacy_earth_date(LANDINGS[rover], sol) == expected
    assert legacy_sol_for_date(LANDINGS[rover], expected) == sol
