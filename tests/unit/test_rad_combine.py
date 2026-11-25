"""`combine_sol` and the transport-level limits of the RAD source (stage 21, AC-6, AC-7).

`combine_sol(observations, low_rate_ratio) -> SolSummary | None` works on `RadObservation`
objects (no files, no database).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.rad_sync import combine_sol
from app.sources.rad import RadObservation, RadParser
from tests._rad import F_1649_B, F_4843, F_SOL10, attr, fixture
from tests.unit.test_rad_parser import counters

pytestmark = pytest.mark.unit


def parse_fixture(name: str) -> Any:
    parser = RadParser()
    parser.feed(fixture(name))
    return attr(parser.result(), "observations")


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
    # The dose is plausible (>= 1.0 uGy/h): with 0.2 the dose filter of ADR-8 would drop it as
    # invalid. The low rate (0.03 /s) alone must not drop the only observation.
    summary = combine_sol([mk(0, 2.0, 900, 27)], 0.5)

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


# --- AC-58: the implausible dose filter (stage 22, ADR-8) ----------------------------------------


def test_combine_drops_a_dose_below_the_limit_as_invalid_ac58() -> None:
    """AC-58: QC=0, a normal rate and B = 0.5 uGy/h -> `dropped_invalid`, not in the day."""
    observations = [mk(0, 6.0, 900, 7200), mk(2, 6.2, 900, 7200), mk(4, 0.5, 900, 7200, dose_e=5.0)]

    summary = combine_sol(observations, 0.5)

    assert counters(summary) == (2, 0, 0, 1)
    assert attr(summary, "dose_b_min") == 6.0  # the dropped 0.5 does not count
    assert attr(summary, "live_s") == 1800
    assert attr(summary, "bu_count") == 14400
    assert attr(summary, "dose_b_mean") == pytest.approx(6.1)


def test_combine_drops_a_low_e_dose_as_invalid_too_ac58() -> None:
    """AC-58: B or E below the limit is enough."""
    observations = [mk(0, 6.0, 900, 7200), mk(2, 6.2, 900, 7200, dose_e=0.9)]

    summary = combine_sol(observations, 0.5)

    assert counters(summary) == (1, 0, 0, 1)
    assert attr(summary, "dose_e_min") == pytest.approx(6.0 * 0.9)


def test_combine_a_dose_of_exactly_the_limit_is_used_ac58() -> None:
    """AC-58: 1.0 uGy/h is not below the limit (B and E)."""
    observations = [mk(0, 6.0, 900, 7200), mk(2, 1.0, 900, 7200, dose_e=1.0)]

    summary = combine_sol(observations, 0.5)

    assert counters(summary) == (2, 0, 0, 0)
    assert attr(summary, "dose_b_min") == 1.0


@pytest.mark.parametrize(
    ("dose_b", "dose_e", "expected"),
    [
        (0.999999, 5.0, (1, 0, 0, 1)),
        (5.0, 0.999999, (1, 0, 0, 1)),
        (1.000001, 1.000001, (2, 0, 0, 0)),
        (0.0, 5.0, (1, 0, 0, 1)),
    ],
    ids=["b-just-below", "e-just-below", "both-just-above", "zero"],
)
def test_combine_dose_limit_boundary_ac58(
    dose_b: float, dose_e: float, expected: tuple[int, int, int, int]
) -> None:
    summary = combine_sol([mk(0, 6.0, 900, 7200), mk(2, dose_b, 900, 7200, dose_e=dose_e)], 0.5)

    assert counters(summary) == expected


def test_combine_the_rate_rule_comes_first_so_garbage_with_a_low_rate_stays_low_rate_ac58() -> None:
    """AC-58: sol 10 obs 13 (B 0.216, 0.03 /s) is `dropped_low_rate`, not `dropped_invalid`."""
    observations = [
        mk(0, 8.4, 960, 7817),
        mk(12, 8.7, 960, 7817),
        mk(13, 0.216, 960, 26, dose_e=0.25),
        mk(25, 9.8, 960, 7707),
    ]

    assert counters(combine_sol(observations, 0.5)) == (3, 0, 1, 0)


def test_combine_a_qc_failure_with_a_low_dose_stays_dropped_qc_ac58() -> None:
    """AC-58: QC=1 and B 0.19 -> `dropped_qc`, as before (sol 1649 obs 14 has that shape)."""
    observations = [mk(0, 6.0, 900, 7200), mk(2, 0.19, 900, 7200, dose_e=0.2, qc="00000001")]

    assert counters(combine_sol(observations, 0.5)) == (1, 1, 0, 0)


def test_combine_the_median_still_includes_the_low_dose_observation_ac58() -> None:
    """AC-58: the dose filter comes after the rate rule; the median is taken as before.

    Rates 8.0, 8.0, 3.0 (dose 0.5) and 3.5: with the low-dose observation in the median it is
    5.75 and the threshold 2.875, so nothing is dropped for its rate and 3.0 goes as invalid.
    A median without it (8.0) would make 3.5 a `dropped_low_rate`.
    """
    observations = [
        mk(0, 6.0, 1000, 8000),
        mk(2, 6.0, 1000, 8000),
        mk(4, 0.5, 1000, 3000, dose_e=5.0),
        mk(6, 6.0, 1000, 3500),
    ]

    summary = combine_sol(observations, 0.5)

    assert counters(summary) == (3, 0, 0, 1)
    assert attr(summary, "bu_count") == 8000 + 8000 + 3500


def test_combine_only_low_dose_observations_give_no_summary_ac58() -> None:
    assert combine_sol([mk(0, 0.5, 900, 7200, dose_e=0.4), mk(2, 0.6, 900, 7200)], 0.5) is None


def test_combine_the_dose_filter_adds_to_the_repeated_count_ac58() -> None:
    summary = combine_sol(
        [mk(0, 6.0, 900, 7200), mk(2, 0.5, 900, 7200, dose_e=5.0)], 0.5, repeated=2
    )

    assert attr(summary, "dropped_invalid", "n_dropped_invalid") == 3


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (F_4843, (3, 1, 0, 0)),
        (F_SOL10, (3, 0, 1, 0)),
        (F_1649_B, (3, 1, 0, 0)),
    ],
    ids=["sol-4843", "sol-10", "sol-1649"],
)
def test_combine_fixtures_keep_their_counters_under_the_dose_filter_ac58(
    name: str, expected: tuple[int, int, int, int]
) -> None:
    """AC-58: the real fixtures 10, 1649 and 4843 have no implausible dose that gets through."""
    summary = combine_sol(parse_fixture(name), 0.5)

    assert counters(summary) == expected


def test_combine_fixture_4843_daily_values_do_not_change_under_the_dose_filter_ac58() -> None:
    summary = combine_sol(parse_fixture(F_4843), 0.5)

    b = (6.84128, 6.78671, 6.97246)
    e = (6.58689, 6.53178, 6.43259)
    assert attr(summary, "dose_b_mean") == pytest.approx(sum(b) / 3)
    assert attr(summary, "dose_b_min") == min(b) and attr(summary, "dose_b_max") == max(b)
    assert attr(summary, "dose_e_mean") == pytest.approx(sum(e) / 3)
    assert attr(summary, "bu_count") == 6454 + 6487 + 6509
    assert attr(summary, "live_s") == pytest.approx(2790)


def test_combine_fixture_sol_10_daily_values_do_not_change_under_the_dose_filter_ac58() -> None:
    summary = combine_sol(parse_fixture(F_SOL10), 0.5)

    assert attr(summary, "dose_b_mean") == pytest.approx((8.40398 + 8.72671 + 9.77913) / 3)
    assert attr(summary, "dose_b_min") == 8.40398
    assert attr(summary, "bu_count") == 7817 + 7817 + 7707
