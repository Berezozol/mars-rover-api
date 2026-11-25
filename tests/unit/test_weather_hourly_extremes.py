"""Hourly min/max and gust of the weather summaries (stage 22 phase B)

Covers AC-15 (hourly keys and rules), AC-16 (null hours), AC-17 (daily = extremes of hourly),
AC-18 (per-rover keys, no wind outside the window) and AC-21 (single reading, gust, all discarded)..

Spec: docs/proposals/sol-conditions.md section 2 (AC-17, AC-21) and section 7 (`HourAggregate`
fields). `combine_sol` is fed with samples read from the recorded fixtures (real MEDA and REMS
files and the synthetic `synthetic/single_reading` set), without a database.

Rules under test (API.md `/weather/{sol}`): `_min`/`_max` of an hour are the lowest and highest
valid value of the hour (humidity above 100 % is valid, `out_of_range` values are not); the daily
`min`/`max` are the extremes of the hourly ones; the gust of an hour is the 99th percentile of
its wind speeds; an hour without valid values has `None` in the mean, the min and the max.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.services.weather_sync import HourAggregate, SolAggregate, combine_sol
from app.sources.weather import (
    AIR,
    GROUND,
    HUMIDITY,
    PRESSURE,
    UV,
    WIND_DIR,
    WIND_SPEED,
    LabelInfo,
    WeatherSample,
)
from tests._weather import (
    FIXTURES,
    counter_oor,
    fixture_one,
    read_samples,
    rhs_csv,
)

pytestmark = pytest.mark.unit

SINGLE = FIXTURES / "synthetic" / "single_reading"
LABEL = [LabelInfo(start_time=datetime(2021, 5, 31, 12, 0, tzinfo=UTC), ls=None)]
H = 3600.0

# (parameter of the samples, attribute of SolAggregate, prefix of the HourAggregate fields)
PARAMETERS = [
    (AIR, "air", "air"),
    (PRESSURE, "pressure", "pressure"),
    (HUMIDITY, "humidity", "humidity"),
    (GROUND, "ground", "ground"),
    (UV, "uv", "uv"),
]


def combine(rows: list[tuple[float, str, float]], sol: int = 7, **kwargs: Any) -> SolAggregate:
    aggregate = combine_sol([WeatherSample(*row) for row in rows], LABEL, sol=sol, **kwargs)
    assert aggregate is not None
    return aggregate


def hour_values(aggregate: SolAggregate, prefix: str, suffix: str) -> list[float | None]:
    return [getattr(hour, f"{prefix}{suffix}") for hour in aggregate.hours]


def triple(hour: HourAggregate, prefix: str) -> tuple[float | None, float | None, float | None]:
    return (
        getattr(hour, prefix),
        getattr(hour, f"{prefix}_min"),
        getattr(hour, f"{prefix}_max"),
    )


# --- AC-17: the daily min/max are the extremes of the hourly ones --------------------------------


def meda_rows(sol: int, products: tuple[str, ...]) -> list[tuple[float, str, float]]:
    rows: list[tuple[float, str, float]] = []
    kinds = {"CAL_ATS": "ats", "DER_PS": "ps", "DER_RHS": "rhs", "CAL_WS": "ws"}
    for product in products:
        samples, _ = read_samples(kinds[product], fixture_one(sol, product), sol)
        rows.extend(samples)
    return rows


def rems_rows(sol: int) -> list[tuple[float, str, float]]:
    samples, _ = read_samples("rmd", fixture_one(sol, "RMD"), sol)
    return samples


FIXTURE_CASES = {
    "meda-100-all-products": lambda: meda_rows(100, ("CAL_ATS", "DER_PS", "DER_RHS", "CAL_WS")),
    "meda-400-no-wind": lambda: meda_rows(400, ("CAL_ATS", "DER_PS", "DER_RHS")),
    "meda-1230-night-only": lambda: meda_rows(1230, ("CAL_ATS", "DER_PS", "DER_RHS")),
    "meda-1852-humidity-over-100": lambda: meda_rows(1852, ("CAL_ATS", "DER_PS", "DER_RHS")),
    "rems-30": lambda: rems_rows(30),
    "rems-9-afternoon-only": lambda: rems_rows(9),
}


@pytest.mark.parametrize("case", list(FIXTURE_CASES), ids=list(FIXTURE_CASES))
def test_daily_extremes_equal_the_extremes_of_the_hourly_ones_on_fixtures_ac17(case: str) -> None:
    aggregate = combine(FIXTURE_CASES[case](), sol=100)

    checked = 0
    for _, attribute, prefix in PARAMETERS:
        stat = getattr(aggregate, attribute)
        lows = [v for v in hour_values(aggregate, prefix, "_min") if v is not None]
        highs = [v for v in hour_values(aggregate, prefix, "_max") if v is not None]
        if stat is None:
            assert lows == [] and highs == [], prefix
            continue
        checked += 1
        assert stat.min == min(lows), prefix  # before rounding: exactly equal
        assert stat.max == max(highs), prefix
    assert checked >= 3


@pytest.mark.parametrize("case", list(FIXTURE_CASES), ids=list(FIXTURE_CASES))
def test_hourly_mean_is_between_the_hourly_min_and_max_on_fixtures_ac17(case: str) -> None:
    aggregate = combine(FIXTURE_CASES[case](), sol=100)

    with_data = 0
    for hour in aggregate.hours:
        for _, _, prefix in PARAMETERS:
            mean, low, high = triple(hour, prefix)
            assert (mean is None) == (low is None) == (high is None), (hour.hour, prefix)
            if mean is not None:
                with_data += 1
                # the mean is sum / count: allow one ulp of float noise when all values are equal
                eps = 1e-9 * max(1.0, abs(mean))
                assert low - eps <= mean <= high + eps, (hour.hour, prefix)
    assert with_data >= 20


def test_hourly_extremes_are_exact_not_the_extremes_of_the_means_ac17() -> None:
    rows = [
        (2 * H + s, AIR, v) for s, v in [(10, 205.0), (20, 190.123456), (30, 230.5), (40, 200.0)]
    ]

    hour = combine(rows).hours[2]

    assert hour.air_min == 190.123456
    assert hour.air_max == 230.5
    assert hour.air == pytest.approx((205.0 + 190.123456 + 230.5 + 200.0) / 4)


def test_daily_extremes_come_from_different_hours_ac17() -> None:
    rows = [
        (1 * H, AIR, 250.0),
        (1 * H + 5, AIR, 240.0),
        (20 * H, AIR, 180.0),
        (20 * H + 5, AIR, 190.0),
    ]

    aggregate = combine(rows)

    assert (aggregate.air.min, aggregate.air.max) == (180.0, 250.0)
    assert (aggregate.hours[1].air_min, aggregate.hours[1].air_max) == (240.0, 250.0)
    assert (aggregate.hours[20].air_min, aggregate.hours[20].air_max) == (180.0, 190.0)


# --- the validity rules: humidity above 100 %, out_of_range ---------------------------------------


def test_humidity_above_100_percent_is_included_in_the_hourly_extremes_ac15() -> None:
    rows = [(2 * H + s, HUMIDITY, v) for s, v in [(1, 90.0), (2, 150.0), (3, 200.0), (4, 100.0)]]

    hour = combine(rows).hours[2]

    assert (hour.humidity_min, hour.humidity_max) == (90.0, 200.0)
    assert hour.humidity == pytest.approx(135.0)


def test_real_sol_1852_hourly_humidity_max_reaches_the_daily_max_above_100_ac15() -> None:
    aggregate = combine(meda_rows(1852, ("CAL_ATS", "DER_PS", "DER_RHS")), sol=1852)

    highs = [v for v in hour_values(aggregate, "humidity", "_max") if v is not None]
    assert max(highs) == pytest.approx(183.76)
    assert aggregate.humidity.max == max(highs)
    assert max(highs) > 100.0
    assert all(v <= 200.0 for v in highs)


def test_out_of_range_readings_are_excluded_from_the_hourly_extremes_ac15(tmp_path: Path) -> None:
    """The reader drops 2680332 % (a sensor fault) and counts it; 120 % stays."""
    path = tmp_path / "rhs.csv"
    path.write_bytes(rhs_csv(7, [(2 * H + 1, 50.0), (2 * H + 2, 2680332.0), (2 * H + 3, 120.0)]))
    samples, counters = read_samples("rhs", path, 7)

    hour = combine(samples).hours[2]

    assert counter_oor(counters, "humid") == 1
    assert (hour.humidity_min, hour.humidity_max) == (50.0, 120.0)
    assert hour.humidity == pytest.approx(85.0)


def test_real_meda_sol_100_synthetic_outliers_do_not_reach_any_hourly_extreme_ac15() -> None:
    aggregate = combine(meda_rows(100, ("CAL_ATS", "DER_PS", "DER_RHS")), sol=100)

    assert max(v for v in hour_values(aggregate, "air", "_max") if v is not None) < 300.0
    assert min(v for v in hour_values(aggregate, "pressure", "_min") if v is not None) >= 300.0
    assert max(v for v in hour_values(aggregate, "humidity", "_max") if v is not None) <= 200.0
    # the fixture row at 12:00:04 (104.5 %) is inside hour 12 and is kept
    assert aggregate.hours[12].humidity_max >= 104.5


# --- AC-16 / null hours ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("param", "attribute", "prefix"), PARAMETERS, ids=[p[2] for p in PARAMETERS]
)
def test_hour_without_a_parameter_has_none_in_mean_min_and_max_ac16(
    param: str, attribute: str, prefix: str
) -> None:
    rows = [(4 * H + 10, param, 210.0), (9 * H + 10, param, 212.0)]

    aggregate = combine(rows)

    for hour in aggregate.hours:
        if hour.hour in (4, 9):
            assert triple(hour, prefix) == (
                pytest.approx(210.0 if hour.hour == 4 else 212.0),
                210.0 if hour.hour == 4 else 212.0,
                210.0 if hour.hour == 4 else 212.0,
            )
        else:
            assert triple(hour, prefix) == (None, None, None), hour.hour


def test_hour_without_wind_has_no_speed_direction_or_gust_ac16() -> None:
    rows = [(4 * H + 10, WIND_SPEED, 3.0), (4 * H + 20, WIND_DIR, 90.0), (5 * H, AIR, 210.0)]

    aggregate = combine(rows)

    assert aggregate.hours[4].wind_gust == 3.0
    assert aggregate.hours[4].wind_speed == 3.0
    for hour in aggregate.hours:
        if hour.hour != 4:
            assert (hour.wind_speed, hour.wind_gust, hour.wind_dir) == (None, None, None)


def test_real_sol_1230_hours_after_the_night_session_are_null_in_all_three_keys_ac16() -> None:
    aggregate = combine(meda_rows(1230, ("CAL_ATS",)), sol=1230)

    quiet = [h for h in aggregate.hours if h.hour >= 8]
    assert len(quiet) == 16
    assert all(triple(h, "air") == (None, None, None) and h.coverage == 0 for h in quiet)
    assert all(h.air_min is not None and h.air_max is not None for h in aggregate.hours[:8])


# --- AC-21: an hour with a single reading; all readings of an hour discarded ----------------------


def synthetic(kind: str, name: str) -> tuple[list[tuple[float, str, float]], Any]:
    return read_samples(kind, SINGLE / f"sol_0007_{name}.csv", 7)


def single_reading_aggregate() -> tuple[SolAggregate, Any, Any]:
    ats, ats_counters = synthetic("ats", "CAL_ATS")
    ps, ps_counters = synthetic("ps", "DER_PS")
    ws, _ = synthetic("ws", "CAL_WS")
    return combine([*ats, *ps, *ws]), ats_counters, ps_counters


def test_hour_with_one_reading_has_min_equal_to_max_equal_to_the_mean_ac21() -> None:
    aggregate, _, _ = single_reading_aggregate()

    hour = aggregate.hours[3]
    assert triple(hour, "air") == (210.0, 210.0, 210.0)
    assert triple(hour, "pressure") == (740.5, 740.5, 740.5)


def test_hour_with_two_readings_has_distinct_min_and_max_ac21() -> None:
    aggregate, _, _ = single_reading_aggregate()

    hour = aggregate.hours[4]
    assert (hour.air_min, hour.air, hour.air_max) == (205.0, 206.0, 207.0)
    assert (hour.pressure_min, hour.pressure_max) == (741.0, 743.0)


def test_gust_of_a_single_speed_is_that_speed_ac21() -> None:
    aggregate, _, _ = single_reading_aggregate()

    hour = aggregate.hours[3]
    assert hour.wind_speed == 4.0
    assert hour.wind_gust == 4.0


def test_gust_is_the_99th_percentile_of_the_speeds_of_the_hour_ac21() -> None:
    aggregate, _, _ = single_reading_aggregate()

    # speeds 2.0 and 6.0: position 0.99 * (2 - 1) -> 2 + 0.99 * 4
    assert aggregate.hours[4].wind_gust == pytest.approx(5.96)
    assert aggregate.hours[4].wind_speed == pytest.approx(4.0)


def test_gust_interpolates_linearly_inside_an_hour_of_many_speeds_ac21() -> None:
    speeds = [float(v) for v in range(1, 102)]  # 1..101: position 0.99 * 100 = 99 -> value 100
    rows = [(10 * H + i, WIND_SPEED, v) for i, v in enumerate(reversed(speeds))]
    rows += [(11 * H + i, WIND_SPEED, v) for i, v in enumerate([2.0, 4.0, 10.0])]  # 4 + 0.98 * 6

    aggregate = combine(rows)

    assert aggregate.hours[10].wind_gust == pytest.approx(100.0)
    assert aggregate.hours[11].wind_gust == pytest.approx(9.88)


def test_gust_of_an_hour_ignores_the_speeds_of_other_hours_ac21() -> None:
    rows = [(1 * H + i, WIND_SPEED, 2.0) for i in range(100)]
    rows += [(2 * H + i, WIND_SPEED, 40.0) for i in range(100)]

    aggregate = combine(rows)

    assert aggregate.hours[1].wind_gust == pytest.approx(2.0)
    assert aggregate.hours[2].wind_gust == pytest.approx(40.0)


def test_hour_with_all_readings_discarded_has_null_mean_min_max_and_zero_coverage_ac21() -> None:
    aggregate, ats_counters, ps_counters = single_reading_aggregate()

    hour = aggregate.hours[5]
    assert counter_oor(ats_counters, "air") == 2  # both 999.9 K rows were dropped by the reader
    assert counter_oor(ps_counters, "pressure") == 2
    assert triple(hour, "air") == (None, None, None)
    assert triple(hour, "pressure") == (None, None, None)
    assert hour.coverage == 0


def test_discarded_readings_do_not_move_the_daily_extremes_ac21() -> None:
    aggregate, _, _ = single_reading_aggregate()

    assert (aggregate.air.min, aggregate.air.max) == (205.0, 224.0)
    assert (aggregate.pressure.min, aggregate.pressure.max) == (740.5, 743.0)
    assert aggregate.hours_covered == 3  # hours 3, 4 and 6; hour 5 has nothing valid
    assert [h.hour for h in aggregate.hours if h.air is not None] == [3, 4, 6]


# --- per-rover keys at the aggregate level --------------------------------------------------------


def test_rems_hours_have_ground_and_uv_extremes_and_no_wind_ac18() -> None:
    aggregate = combine(rems_rows(30), sol=30)

    assert any(h.uv_min is not None and h.uv_max is not None for h in aggregate.hours)
    assert any(h.ground_min is not None and h.ground_max is not None for h in aggregate.hours)
    assert all(
        (h.wind_speed, h.wind_dir, h.wind_gust) == (None, None, None) for h in aggregate.hours
    )
    assert aggregate.wind is None


def test_meda_hours_have_no_uv_ac18() -> None:
    aggregate = combine(meda_rows(100, ("CAL_ATS", "DER_PS", "DER_RHS", "CAL_WS")), sol=100)

    assert all((h.uv, h.uv_min, h.uv_max) == (None, None, None) for h in aggregate.hours)
    assert any(h.wind_gust is not None for h in aggregate.hours)


def test_meda_sol_outside_the_wind_window_has_no_gust_in_any_hour_ac18() -> None:
    aggregate = combine(meda_rows(400, ("CAL_ATS", "DER_PS", "DER_RHS")), sol=400)

    assert all(
        (h.wind_speed, h.wind_gust, h.wind_dir) == (None, None, None) for h in aggregate.hours
    )
