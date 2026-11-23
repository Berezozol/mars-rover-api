"""Daily and hourly summaries computed from raw files (stage 17, AC-5, AC-7 ... AC-11, Ls, flags).

`combine_sol` has no fixed signature in the contract, so the arithmetic is checked through the
public path: synthetic raw files -> `weather parse` -> `weather_daily` / `weather_hourly`
columns (SI units, as stored). Every test builds one or two sols with exactly known values.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests._weather import (
    Factory,
    ats_csv,
    build_meda_sol,
    copy_fixture_sols,
    daily_row,
    fixture_one,
    full_rems_sol,
    hourly_air,
    hourly_rows,
    hourly_scalar,
    lmst_text,
    ps_csv,
    reference_ls,
    rhs_csv,
    rmd_hourly,
    rmd_tab,
    run_weather,
    ws_csv,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_db")]


@pytest.fixture
def raw(weather_env: Callable[..., Path]) -> Path:
    return weather_env()


async def parse(rover: str = "perseverance") -> int:
    return await run_weather("parse", "--rover", rover)


# --- AC-7 / AC-8: mean of hourly means -----------------------------------------------------------


async def test_mean_is_the_mean_of_hourly_means_and_min_max_are_raw_extremes_ac7(
    raw: Path, clean_db: Factory
) -> None:
    """Hours 0..23, 60 values each, temperature of hour h is 200 + h K (spec: h K, in range)."""
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, hourly_air(7, range(24), per_hour=60))})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    assert row["air_mean_k"] == pytest.approx(200.0 + 11.5)
    assert row["air_min_k"] == pytest.approx(200.0)
    assert row["air_max_k"] == pytest.approx(223.0)
    assert row["hours_covered"] == 24
    assert row["coverage"] == pytest.approx(1.0, abs=0.01)
    assert row["n_air"] == 24 * 60
    hours = await hourly_rows(clean_db, 7)
    assert [h["hour"] for h in hours] == list(range(24))
    assert [h["air_k"] for h in hours] == pytest.approx([200.0 + h for h in range(24)])
    assert [h["coverage"] for h in hours] == pytest.approx([1.0] * 24, abs=0.01)


async def test_min_and_max_are_raw_extremes_not_hourly_means_ac7(
    raw: Path, clean_db: Factory
) -> None:
    rows = [(0.0, 200.0, 200.0, 200.0), (60.0, 210.0, 210.0, 210.0), (3600.0, 230.0, 230.0, 230.0)]
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, rows)})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    assert (row["air_min_k"], row["air_max_k"]) == (200.0, 230.0)
    assert row["air_mean_k"] == pytest.approx((205.0 + 230.0) / 2)  # hour 0 mean 205, hour 1 230


async def test_coverage_is_the_share_of_the_144_ten_minute_bins_with_air_data_ac7(
    raw: Path, clean_db: Factory
) -> None:
    """Rows at 0, 10 and 20 minutes of every hour: bins 0, 1, 2 of 6 -> 72 of 144 bins."""
    rows = [
        (h * 3600.0 + m * 60.0 + 1.0, 210.0, 210.0, 210.0) for h in range(24) for m in (0, 10, 20)
    ]
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, rows)})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    assert row["coverage"] == pytest.approx(0.5, abs=0.01)
    assert row["hours_covered"] == 24
    assert [h["coverage"] for h in await hourly_rows(clean_db, 7)] == pytest.approx(
        [0.5] * 24, abs=0.01
    )


async def test_coverage_counts_bins_by_the_144_denominator_ac7(
    raw: Path, clean_db: Factory
) -> None:
    rows = [(i * 600.0 + 1.0, 210.0, 210.0, 210.0) for i in range(36)]  # 36 bins = 6 hours
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, rows)})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    assert row["coverage"] == pytest.approx(36 / 144, abs=0.005)  # 0.25, not 36/148 = 0.243
    assert row["hours_covered"] == 6


async def test_mean_is_not_weighted_by_the_number_of_rows_ac8(raw: Path, clean_db: Factory) -> None:
    dense = [(3 * 3600 + i * 0.36, 200.0, 200.0, 200.0) for i in range(10_000)]
    sparse = [(15 * 3600 + i * 60.0, 220.0, 220.0, 220.0) for i in range(10)]
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, dense + sparse)})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    assert row["air_mean_k"] == pytest.approx(210.0)  # (200 + 220) / 2, not 200.02
    assert (row["air_min_k"], row["air_max_k"]) == (200.0, 220.0)
    assert row["n_air"] == 10_010
    assert row["hours_covered"] == 2


async def test_mean_of_pressure_and_humidity_is_also_a_mean_of_hourly_means_ac8(
    raw: Path, clean_db: Factory
) -> None:
    pressure = [(3 * 3600.0 + i, 700.0) for i in range(300)] + [(15 * 3600.0, 760.0)]
    humidity = [(3 * 3600.0 + i, 2.0) for i in range(300)] + [(15 * 3600.0, 8.0)]
    build_meda_sol(raw, 7, {"DER_PS": ps_csv(7, pressure), "DER_RHS": rhs_csv(7, humidity)})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    assert row["pressure_mean_pa"] == pytest.approx(730.0)
    assert row["humidity_mean_pct"] == pytest.approx(5.0)


# --- AC-9: partial coverage ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "flagged"),
    [(6, True), (17, True), (18, False), (24, False)],
    ids=["6-hours", "17-hours", "18-hours-boundary", "24-hours"],
)
async def test_partial_coverage_flag_below_18_hours_but_the_mean_is_kept_ac9(
    raw: Path, clean_db: Factory, hours: int, flagged: bool
) -> None:
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, hourly_air(7, range(hours), per_hour=6))})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    assert row["hours_covered"] == hours
    assert row["air_mean_k"] == pytest.approx(200.0 + (hours - 1) / 2)
    partial = [f for f in row["flags"] if f["code"] == "partial_coverage"]
    assert len(partial) == int(flagged)
    if flagged:
        assert partial[0]["parameter"] is None
        assert partial[0].get("count") is None


async def test_the_partial_threshold_follows_weather_full_hours_ac9(
    weather_env: Callable[..., Path], clean_db: Factory
) -> None:
    raw = weather_env(WEATHER_FULL_HOURS="12")
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, hourly_air(7, range(14), per_hour=6))})

    assert await parse() == 0

    assert (await daily_row(clean_db, 7))["flags"] == []


async def test_night_only_real_sol_1230_is_flagged_and_keeps_its_mean_ac9(
    raw: Path, clean_db: Factory
) -> None:
    copy_fixture_sols(raw, meda=[1230])

    assert await parse() == 0

    row = await daily_row(clean_db, 1230)
    assert row["hours_covered"] == 8
    assert row["air_mean_k"] is not None and 180 < row["air_mean_k"] < 200
    assert any(f["code"] == "partial_coverage" and f["parameter"] is None for f in row["flags"])


# --- AC-11: wind ---------------------------------------------------------------------------------


def wind_rows(
    sol: int, values: list[tuple[float, float]], hour: int = 5
) -> list[tuple[float, float, float, float, float, int, int]]:
    """Both booms stable with the same speed and direction; spread over one hour."""
    step = 3600.0 / (len(values) + 1)
    return [(hour * 3600 + (i + 1) * step, v, d, v, d, 1, 1) for i, (v, d) in enumerate(values)]


async def test_gust_is_the_99th_percentile_with_linear_interpolation_ac11(
    raw: Path, clean_db: Factory
) -> None:
    values = [(3.0, 90.0)] * 990 + [(50.0, 90.0)] * 10
    build_meda_sol(raw, 100, {"CAL_WS": ws_csv(100, wind_rows(100, values))})

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert row["wind_gust_ms"] == pytest.approx(3.0 + 0.01 * 47.0, abs=1e-6)  # 3.47, not 50
    assert row["wind_mean_ms"] == pytest.approx((990 * 3.0 + 10 * 50.0) / 1000)  # one hour only
    assert row["n_wind"] == 1000
    assert row["wind_dir_mode_deg"] == pytest.approx(90.0)


@pytest.mark.parametrize(
    ("degrees", "centre"),
    [
        (0.0, 0.0),
        (359.0, 0.0),
        (5.0, 0.0),
        (348.8, 0.0),
        (348.7, 337.5),
        (11.0, 0.0),
        (12.0, 22.5),
        (100.0, 90.0),
        (112.5, 112.5),
        (180.0, 180.0),
        (270.0, 270.0),
    ],
    ids=lambda v: str(v),
)
async def test_direction_mode_is_the_centre_of_the_16_point_sector_ac3(
    raw: Path, clean_db: Factory, degrees: float, centre: float
) -> None:
    rows = wind_rows(100, [(4.0, degrees)] * 20)
    build_meda_sol(raw, 100, {"CAL_WS": ws_csv(100, rows)})

    assert await parse() == 0

    assert (await daily_row(clean_db, 100))["wind_dir_mode_deg"] == pytest.approx(centre)


async def test_direction_mode_is_the_sector_with_the_most_values_ac11(
    raw: Path, clean_db: Factory
) -> None:
    values = [(4.0, 0.0)] * 5 + [(4.0, 359.0)] * 5 + [(4.0, 180.0)] * 9  # north sector has 10
    build_meda_sol(raw, 100, {"CAL_WS": ws_csv(100, wind_rows(100, values))})

    assert await parse() == 0

    assert (await daily_row(clean_db, 100))["wind_dir_mode_deg"] == pytest.approx(0.0)


async def test_wind_mean_is_a_mean_of_hourly_means_ac11(raw: Path, clean_db: Factory) -> None:
    rows = wind_rows(100, [(2.0, 90.0)] * 100, hour=3) + wind_rows(100, [(8.0, 90.0)], hour=15)
    build_meda_sol(raw, 100, {"CAL_WS": ws_csv(100, rows)})

    assert await parse() == 0

    assert (await daily_row(clean_db, 100))["wind_mean_ms"] == pytest.approx(5.0)


async def test_wind_hourly_rows_have_speed_and_direction_ac11(raw: Path, clean_db: Factory) -> None:
    rows = wind_rows(100, [(2.0, 90.0)] * 10, hour=3)
    build_meda_sol(raw, 100, {"CAL_WS": ws_csv(100, rows)})

    assert await parse() == 0

    hours = await hourly_rows(clean_db, 100)
    assert hours[3]["wind_ms"] == pytest.approx(2.0)
    assert hours[3]["wind_dir_deg"] == pytest.approx(90.0)
    assert hours[4]["wind_ms"] is None and hours[4]["wind_dir_deg"] is None


async def test_real_sol_100_has_wind_and_real_sol_400_has_none_ac11(
    raw: Path, clean_db: Factory
) -> None:
    copy_fixture_sols(raw, meda=[100, 400])

    assert await parse() == 0

    with_wind = await daily_row(clean_db, 100)
    without = await daily_row(clean_db, 400)
    assert with_wind["wind_mean_ms"] is not None
    assert 0 < with_wind["wind_gust_ms"] <= 60
    assert with_wind["wind_dir_mode_deg"] % 22.5 == pytest.approx(0.0)
    assert without["wind_mean_ms"] is None and without["wind_gust_ms"] is None
    assert without["wind_dir_mode_deg"] is None and without["n_wind"] == 0
    assert "CAL_WS" not in without["source_version"]


# --- flags from the raw rows ---------------------------------------------------------------------


async def test_humidity_over_100_is_kept_in_the_values_and_flagged_with_a_count_ac2(
    raw: Path, clean_db: Factory
) -> None:
    rows = hourly_scalar(range(24), lambda h: 5.0, per_hour=6)
    rows += [(1.0, 104.5), (2.0, 150.0), (3.0, 100.0)]
    build_meda_sol(raw, 7, {"DER_RHS": rhs_csv(7, rows)})

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    over = [f for f in row["flags"] if f["code"] == "humidity_over_100"]
    assert over == [{"code": "humidity_over_100", "parameter": "humidity_pct", "count": 2}]
    assert row["humidity_max_pct"] == pytest.approx(150.0)
    assert row["n_humidity"] == len(rows)


async def test_outliers_are_flagged_out_of_range_per_parameter_and_not_counted_in_values_ac2(
    raw: Path, clean_db: Factory
) -> None:
    ats = hourly_air(7, range(24), per_hour=6) + [(5.0, 200.0, 999.9, 200.0)] * 3
    ps = hourly_scalar(range(24), lambda h: 740.0, per_hour=6) + [(5.0, 50.0)]
    rhs = hourly_scalar(range(24), lambda h: 5.0, per_hour=6) + [(5.0, 2680332.0)] * 2
    build_meda_sol(
        raw, 7, {"CAL_ATS": ats_csv(7, ats), "DER_PS": ps_csv(7, ps), "DER_RHS": rhs_csv(7, rhs)}
    )

    assert await parse() == 0

    row = await daily_row(clean_db, 7)
    out = {f["parameter"]: f["count"] for f in row["flags"] if f["code"] == "out_of_range"}
    assert out == {"air_temp_c": 3, "pressure_pa": 1, "humidity_pct": 2}
    assert row["air_max_k"] == pytest.approx(223.0)
    assert row["pressure_min_pa"] == pytest.approx(740.0)
    assert row["humidity_max_pct"] == pytest.approx(5.0)
    assert row["n_air"] == 24 * 6 and row["n_pressure"] == 24 * 6 + 0


async def test_flags_are_ordered_by_the_dictionary_then_by_parameter_ac35(
    raw: Path, clean_db: Factory
) -> None:
    ats = hourly_air(7, range(6), per_hour=6) + [(5.0, 200.0, 999.9, 200.0)]  # partial + oor
    ps = hourly_scalar(range(6), lambda h: 740.0, per_hour=6) + [(5.0, 50.0)]
    rhs = hourly_scalar(range(6), lambda h: 5.0, per_hour=6) + [(5.0, 120.0), (6.0, 2680332.0)]
    build_meda_sol(
        raw, 7, {"CAL_ATS": ats_csv(7, ats), "DER_PS": ps_csv(7, ps), "DER_RHS": rhs_csv(7, rhs)}
    )

    assert await parse() == 0

    flags = (await daily_row(clean_db, 7))["flags"]
    assert [(f["code"], f["parameter"]) for f in flags] == [
        ("partial_coverage", None),
        ("humidity_over_100", "humidity_pct"),
        ("out_of_range", "air_temp_c"),
        ("out_of_range", "humidity_pct"),
        ("out_of_range", "pressure_pa"),
    ]


async def test_wind_speed_over_60_is_out_of_range_ac2(raw: Path, clean_db: Factory) -> None:
    rows = wind_rows(100, [(4.0, 90.0)] * 10) + [(5 * 3600.0 + 1, 70.0, 90.0, None, None, 1, 0)]
    build_meda_sol(raw, 100, {"CAL_WS": ws_csv(100, rows)})

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert row["wind_gust_ms"] < 10
    assert {"code": "out_of_range", "parameter": "wind", "count": 1} in row["flags"]


# --- AC-5 on the real REMS sol 30, Ls and dates --------------------------------------------------


async def test_real_rems_sol_30_flags_the_ground_glitch_and_leaves_the_data_clean_ac5(
    raw: Path, clean_db: Factory
) -> None:
    copy_fixture_sols(raw, rems=[30])
    lines = fixture_one(30, "RMD").read_text(encoding="ascii").splitlines()
    cells = [[c.strip().strip('"').strip() for c in line.split(",")] for line in lines]
    glitches = sum(1 for c in cells if float(c[7]) != -999.0 and not 120 <= float(c[7]) <= 330)
    assert glitches >= 2

    assert await parse("curiosity") == 0

    row = await daily_row(clean_db, 30, "curiosity")
    assert {"code": "out_of_range", "parameter": "ground_temp_c", "count": glitches} in row["flags"]
    assert row["ground_min_k"] >= 120.0
    assert row["pressure_mean_pa"] is not None and 300 < row["pressure_mean_pa"] < 1200
    assert row["uv_max_wm2"] is not None
    assert row["n_wind"] == 0 and row["wind_mean_ms"] is None


async def test_real_rems_sol_9_is_read_from_the_pa_version_with_a_label_date_and_ls_ac5(
    raw: Path, clean_db: Factory
) -> None:
    from datetime import UTC, date, datetime

    copy_fixture_sols(raw, rems=[9])

    assert await parse("curiosity") == 0

    row = await daily_row(clean_db, 9, "curiosity")
    assert row["source_version"] == {"RMD": "PA"}
    assert row["earth_date"] == date(2012, 8, 15)
    expected = reference_ls(datetime(2012, 8, 15, 8, 37, 32, tzinfo=UTC))
    assert row["ls"] == pytest.approx(expected, abs=0.05)
    assert row["hours_covered"] == 12  # the real file covers LMST hours 12..23
    assert any(f["code"] == "partial_coverage" for f in row["flags"])


async def test_real_meda_sol_100_has_the_label_date_and_ls_and_the_synthetic_glitch_flags(
    raw: Path, clean_db: Factory
) -> None:
    from datetime import date

    copy_fixture_sols(raw, meda=[100])

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert row["earth_date"] == date(2021, 5, 31)  # start_date_time of the label, UTC
    assert row["ls"] == pytest.approx(52.734, abs=0.01)
    assert row["hours_covered"] == 24
    assert row["source_version"] == {
        "CAL_ATS": "P01",
        "CAL_WS": "P03",
        "DER_PS": "P01",
        "DER_RHS": "P02",
        "DER_TIRS": "P03",
    }
    out = {f["parameter"]: f["count"] for f in row["flags"] if f["code"] == "out_of_range"}
    assert out.get("air_temp_c") == 1
    assert out.get("pressure_pa") == 1
    assert out.get("humidity_pct") == 1
    assert {"code": "humidity_over_100", "parameter": "humidity_pct", "count": 1} in row["flags"]
    assert 150 < row["air_min_k"] < row["air_mean_k"] < row["air_max_k"] < 300


async def test_real_meda_sol_1852_flags_the_humidity_over_100_values_ac35(
    raw: Path, clean_db: Factory
) -> None:
    copy_fixture_sols(raw, meda=[1852])

    assert await parse() == 0

    row = await daily_row(clean_db, 1852)
    over = [f for f in row["flags"] if f["code"] == "humidity_over_100"]
    assert len(over) == 1 and over[0]["count"] >= 1000
    assert over[0]["parameter"] == "humidity_pct"
    assert row["humidity_max_pct"] == pytest.approx(183.76)
    assert not [f for f in row["flags"] if f["code"] == "out_of_range"]


async def test_hour_boundaries_use_lmst_and_the_last_second_belongs_to_hour_23(
    raw: Path, clean_db: Factory
) -> None:
    rows = [
        (0.0, 201.0, 201.0, 201.0),
        (3599.9, 202.0, 202.0, 202.0),
        (3600.0, 203.0, 203.0, 203.0),
        (86399.9, 204.0, 204.0, 204.0),
    ]
    build_meda_sol(raw, 7, {"CAL_ATS": ats_csv(7, rows)})

    assert await parse() == 0

    hours = await hourly_rows(clean_db, 7)
    assert hours[0]["air_k"] == pytest.approx(201.5)
    assert hours[1]["air_k"] == pytest.approx(203.0)
    assert hours[23]["air_k"] == pytest.approx(204.0)
    assert lmst_text(7, 86399.9).endswith("23:59:59.900")


# --- Curiosity (REMS) aggregates -----------------------------------------------------------------


async def test_rems_daily_has_ground_uv_and_no_wind_and_hourly_means_ac7(
    raw: Path, clean_db: Factory
) -> None:
    lines = rmd_hourly(
        9,
        range(24),
        per_hour=6,
        air=lambda h: 200.0 + h,
        ground=lambda h: 210.0 + h,
        uv=lambda h: float(h),
        pressure=lambda h: 700.0 + h,
        humidity=lambda h: 1.0 + h,
    )
    from tests._weather import build_rems_sol

    build_rems_sol(raw, 9, rmd_tab(lines))

    assert await parse("curiosity") == 0

    row = await daily_row(clean_db, 9, "curiosity")
    assert row["air_mean_k"] == pytest.approx(211.5)
    assert row["ground_mean_k"] == pytest.approx(221.5)
    assert (row["ground_min_k"], row["ground_max_k"]) == (210.0, 233.0)
    assert row["uv_mean_wm2"] == pytest.approx(11.5)
    assert row["uv_max_wm2"] == pytest.approx(23.0)
    assert row["pressure_mean_pa"] == pytest.approx(711.5)
    assert row["humidity_mean_pct"] == pytest.approx(12.5)
    assert row["hours_covered"] == 24
    assert row["coverage"] == pytest.approx(1.0, abs=0.01)
    assert row["flags"] == []
    hours = await hourly_rows(clean_db, 9, "curiosity")
    assert hours[5]["ground_k"] == pytest.approx(215.0)
    assert hours[5]["uv_wm2"] == pytest.approx(5.0)


async def test_rems_unit_helper_builds_full_sols(raw: Path, clean_db: Factory) -> None:
    full_rems_sol(raw, 12)

    assert await parse("curiosity") == 0

    assert (await daily_row(clean_db, 12, "curiosity"))["hours_covered"] == 24


# --- part 17G: the Perseverance ground estimate from DER_TIRS ------------------------------------


def tirs_sol(raw: Path, sol: int, rows: list[Any], air_hours: range = range(24)) -> None:
    from tests._weather import tirs_csv

    build_meda_sol(
        raw,
        sol,
        {
            "CAL_ATS": ats_csv(sol, hourly_air(sol, air_hours, per_hour=6)),
            "DER_TIRS": tirs_csv(sol, rows),
        },
    )


async def test_ground_estimate_is_a_mean_of_hourly_means_with_raw_extremes_17g(
    raw: Path, clean_db: Factory
) -> None:
    from tests._weather import hourly_ground

    tirs_sol(raw, 100, hourly_ground(range(24), lambda h: 200.0 + h))

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert row["ground_min_k"] == pytest.approx(200.0, abs=0.05)
    assert row["ground_max_k"] == pytest.approx(223.0, abs=0.05)
    assert row["ground_mean_k"] == pytest.approx(211.5, abs=0.05)
    assert row["n_ground"] == 24 * 6
    assert row["source_version"]["DER_TIRS"] == "P01"
    hours = await hourly_rows(clean_db, 100)
    assert [h["ground_k"] for h in hours] == pytest.approx([200.0 + h for h in range(24)], abs=0.05)
    assert row["flags"] == []


@pytest.mark.parametrize(
    ("hours", "kept"),
    [(24, True), (18, True), (17, False), (6, False)],
    ids=["24-hours", "18-hours-boundary", "17-hours", "6-hours"],
)
async def test_ground_needs_the_full_hours_of_filtered_samples_and_has_no_flag_otherwise_17g(
    raw: Path, clean_db: Factory, hours: int, kept: bool
) -> None:
    from tests._weather import hourly_ground

    tirs_sol(raw, 100, hourly_ground(range(hours), lambda h: 220.0))

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert (row["ground_mean_k"] is not None) is kept
    assert (row["ground_min_k"] is not None) is kept
    assert (row["n_ground"] > 0) is kept
    assert all(f["parameter"] != "ground_temp_c" for f in row["flags"])
    assert row["air_mean_k"] is not None and row["hours_covered"] == 24  # air is independent


async def test_only_filtered_rows_count_towards_the_hours_17g(raw: Path, clean_db: Factory) -> None:
    """24 hours of rows, but in 10 of them the rover is tilted: 14 hours remain < 18."""
    from tests._weather import flux_of

    rows = [(h * 3600.0 + k * 600, flux_of(220.0), 0 if h >= 14 else 1, 1) for h in range(24)
            for k in range(6)]  # fmt: skip
    tirs_sol(raw, 100, rows)

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert row["ground_mean_k"] is None and row["n_ground"] == 0


async def test_sol_1_has_no_ground_estimate_17g(raw: Path, clean_db: Factory) -> None:
    from tests._weather import hourly_ground

    tirs_sol(raw, 1, hourly_ground(range(24), lambda h: 220.0))

    assert await parse() == 0

    row = await daily_row(clean_db, 1)
    assert row["ground_mean_k"] is None and row["ground_min_k"] is None


async def test_ground_outliers_are_flagged_on_ground_temp_c_and_dropped_17g(
    raw: Path, clean_db: Factory
) -> None:
    from tests._weather import flux_of, hourly_ground

    rows = hourly_ground(range(24), lambda h: 220.0)
    rows += [(5.0, flux_of(100.0), 1, 1), (6.0, flux_of(100.0), 1, 1), (7.0, flux_of(350.0), 1, 1)]
    rows += [(8.0, flux_of(100.0), 0, 1)]  # filtered by the tilt flag first: not an outlier
    tirs_sol(raw, 100, rows)

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert {"code": "out_of_range", "parameter": "ground_temp_c", "count": 3} in row["flags"]
    assert row["ground_min_k"] == pytest.approx(220.0, abs=0.05)
    assert row["ground_max_k"] == pytest.approx(220.0, abs=0.05)


async def test_flags_with_ground_follow_the_dictionary_order_17g(
    raw: Path, clean_db: Factory
) -> None:
    from tests._weather import flux_of, hourly_ground

    rows = hourly_ground(range(24), lambda h: 220.0) + [(5.0, flux_of(100.0), 1, 1)]
    tirs_sol(raw, 100, rows + [])
    build_meda_sol(
        raw,
        100,
        {
            "DER_PS": ps_csv(
                100, hourly_scalar(range(24), lambda h: 740.0, per_hour=6) + [(5.0, 50.0)]
            )
        },
    )

    assert await parse() == 0

    flags = (await daily_row(clean_db, 100))["flags"]
    assert [(f["code"], f["parameter"]) for f in flags] == [
        ("out_of_range", "ground_temp_c"),
        ("out_of_range", "pressure_pa"),
    ]


async def test_real_meda_sol_100_has_a_ground_estimate_and_the_synthetic_outlier_17g(
    raw: Path, clean_db: Factory
) -> None:
    import csv

    copy_fixture_sols(raw, meda=[100])
    sigma = 5.670374419e-8
    by_hour: dict[int, list[float]] = {}
    with fixture_one(100, "DER_TIRS").open(newline="", encoding="utf-8") as handle:
        for r in csv.DictReader(handle):
            if r["ROVER_LOW_TILT"] != "1" or r["WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV"] == "0":
                continue
            kelvin = (float(r["UPWARD_LW_IRRADIANCE"]) / sigma) ** 0.25
            if 120 <= kelvin <= 330:
                by_hour.setdefault(int(r["LMST"][6:8]), []).append(kelvin)
    assert len(by_hour) >= 18
    means = [sum(v) / len(v) for v in by_hour.values()]

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert row["ground_min_k"] == pytest.approx(min(min(v) for v in by_hour.values()), abs=0.01)
    assert row["ground_max_k"] == pytest.approx(max(max(v) for v in by_hour.values()), abs=0.01)
    assert row["ground_mean_k"] == pytest.approx(sum(means) / len(means), abs=0.01)
    assert 150 < row["ground_min_k"] < row["ground_mean_k"] < row["ground_max_k"] < 330
    assert row["ground_max_k"] - row["ground_min_k"] > 50  # a Martian day: tens of kelvin
    assert {"code": "out_of_range", "parameter": "ground_temp_c", "count": 1} in row["flags"]


# --- part 17G, revision 3: the day/night anchors of the ground estimate --------------------------


GROUND_ANCHOR_CASES = [
    (list(range(24)), True),
    # 20 hours, only hour 5 of {4, 5, 6} present
    ([h for h in range(24) if h not in (4, 6, 0, 1)], False),
    # 20 hours, only hour 12 of {11, 12, 13} present
    ([h for h in range(24) if h not in (11, 13, 22, 23)], False),
    # exactly 18 hours with 2 of {4, 5, 6} and 2 of {11, 12, 13}
    ([h for h in range(24) if h not in (6, 13, 0, 1, 2, 3)], True),
]


@pytest.mark.parametrize(
    ("hours", "kept"),
    GROUND_ANCHOR_CASES,
    ids=["24-hours", "one-night-hour", "one-noon-hour", "18-hours-two-and-two"],
)
async def test_ground_needs_two_hours_of_both_anchor_windows_17g(
    raw: Path, clean_db: Factory, hours: list[int], kept: bool
) -> None:
    from tests._weather import hourly_ground

    tirs_sol(raw, 100, hourly_ground(hours, lambda h: 220.0))

    assert await parse() == 0

    row = await daily_row(clean_db, 100)
    assert (row["ground_mean_k"] is not None) is kept
    assert (row["ground_min_k"] is not None) is kept
    assert (row["n_ground"] > 0) is kept
    assert all(f["parameter"] != "ground_temp_c" for f in row["flags"])
    assert row["air_mean_k"] is not None and row["hours_covered"] == 24
    ground_hours = [h["ground_k"] for h in await hourly_rows(clean_db, 100)]
    assert (any(v is not None for v in ground_hours)) is kept
