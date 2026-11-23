"""`combine_sol` and the transport-level limits of the RAD source (stage 21, AC-6, AC-7).

`combine_sol(observations, low_rate_ratio) -> SolSummary | None` works on `RadObservation`
objects (no files, no database).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.rad_sync import combine_sol
from app.sources.rad import RadObservation
from tests._rad import attr
from tests.unit.test_rad_parser import counters

pytestmark = pytest.mark.unit


def mk(
    index: int,
    dose_b: float,
    live: float,
    count: int,
    *,
    dose_e: float | None = None,
    qc: str = "00000000",
) -> Any:
    return RadObservation(
        index=index,
        start_utc_raw="2026-080 17:18:00",
        start_utc=datetime(2026, 3, 21, 17, 18, tzinfo=UTC),
        qc_raw=qc,
        dose_b=dose_b,
        dose_e=dose_b * 0.9 if dose_e is None else dose_e,
        live_s=live,
        bu_count=count,
        bu_read=count,
    )


def test_combine_drops_an_observation_with_a_very_low_count_rate_ac6() -> None:
    """AC-6: rates 8.0, 8.1, 7.9 and 0.03 events/s (median 7.95): the last one is dropped."""
    observations = [
        mk(0, 6.0, 900, 7200),  # 8.0
        mk(2, 6.1, 900, 7290),  # 8.1
        mk(4, 5.9, 900, 7110),  # 7.9
        mk(6, 0.2, 900, 27),  # 0.03
    ]

    summary = combine_sol(observations, 0.5)

    assert counters(summary) == (3, 0, 1, 0)
    assert attr(summary, "dose_b_min") == 5.9  # the dropped 0.2 does not count
    assert attr(summary, "live_s") == 2700
    assert attr(summary, "bu_count") == 7200 + 7290 + 7110


def test_combine_a_single_valid_observation_is_never_dropped_for_its_rate_ac6() -> None:
    summary = combine_sol([mk(0, 0.2, 900, 27)], 0.5)

    assert counters(summary) == (1, 0, 0, 0)


def test_combine_rate_exactly_at_the_threshold_is_kept_ac6() -> None:
    """The rule is `< 0.5 x median`, so exactly half of the median stays."""
    observations = [mk(0, 6.0, 1000, 8000), mk(2, 6.0, 1000, 8000), mk(4, 3.0, 1000, 4000)]

    assert counters(combine_sol(observations, 0.5)) == (3, 0, 0, 0)


def test_combine_a_rate_burst_is_not_dropped_ac6() -> None:
    """The threshold is one-sided: a solar particle event raises the rate and stays."""
    observations = [mk(0, 6.0, 900, 7200), mk(2, 6.0, 900, 7200), mk(4, 60.0, 900, 72000)]

    summary = combine_sol(observations, 0.5)

    assert counters(summary) == (3, 0, 0, 0)
    assert attr(summary, "dose_b_max") == 60.0


def test_combine_median_uses_only_observations_that_passed_the_other_checks_ac6() -> None:
    """A QC-dropped observation does not drag the median."""
    observations = [
        mk(0, 6.0, 900, 7200),  # 8.0
        mk(2, 6.0, 900, 7200),  # 8.0
        mk(4, 6.0, 900, 100, qc="00000001"),  # QC drop, rate 0.11: not in the median
        mk(6, 6.0, 900, 3600),  # 4.0 = exactly half of 8.0
    ]

    assert counters(combine_sol(observations, 0.5)) == (3, 1, 0, 0)


def test_combine_ratio_argument_moves_the_threshold_ac6() -> None:
    observations = [mk(0, 6.0, 1000, 8000), mk(2, 6.0, 1000, 8000), mk(4, 6.0, 1000, 5000)]

    assert counters(combine_sol(observations, 0.5)) == (3, 0, 0, 0)
    assert counters(combine_sol(observations, 0.7)) == (2, 0, 1, 0)


def test_combine_weights_the_mean_by_live_time_and_sums_counts_ac7() -> None:
    """AC-7: (B 6.0, 900 s, 7200) and (B 8.0, 300 s, 2700)."""
    summary = combine_sol([mk(0, 6.0, 900, 7200), mk(2, 8.0, 300, 2700)], 0.5)

    assert attr(summary, "dose_b_mean") == pytest.approx(6.5)
    assert attr(summary, "dose_b_min") == 6.0
    assert attr(summary, "dose_b_max") == 8.0
    assert attr(summary, "live_s") == 1200
    assert attr(summary, "bu_count") == 9900
    assert counters(summary) == (2, 0, 0, 0)


def test_combine_e_dose_is_weighted_the_same_way_ac7() -> None:
    summary = combine_sol(
        [mk(0, 6.0, 900, 7200, dose_e=4.0), mk(2, 8.0, 300, 2700, dose_e=8.0)], 0.5
    )

    assert attr(summary, "dose_e_mean") == pytest.approx((4.0 * 900 + 8.0 * 300) / 1200)
    assert attr(summary, "dose_e_min") == 4.0
    assert attr(summary, "dose_e_max") == 8.0


def test_combine_min_and_max_are_per_observation_values_not_means_ac7() -> None:
    observations = [mk(i * 2, 5.0 + i, 900, 7200) for i in range(5)]

    summary = combine_sol(observations, 0.5)

    assert (attr(summary, "dose_b_min"), attr(summary, "dose_b_max")) == (5.0, 9.0)
    assert attr(summary, "dose_b_mean") == pytest.approx(7.0)


def test_combine_no_observations_gives_none() -> None:
    assert combine_sol([], 0.5) is None


def test_combine_all_dropped_gives_none() -> None:
    assert combine_sol([mk(0, 6.0, 900, 7200, qc="00000001")], 0.5) is None


def test_combine_is_independent_of_the_observation_order() -> None:
    observations = [mk(0, 6.0, 900, 7200), mk(2, 8.0, 300, 2700), mk(4, 7.0, 600, 5000)]

    forward = combine_sol(observations, 0.5)
    backward = combine_sol(list(reversed(observations)), 0.5)

    for name in ("dose_b_mean", "dose_b_min", "dose_b_max", "live_s", "bu_count"):
        assert attr(forward, name) == pytest.approx(attr(backward, name)), name
