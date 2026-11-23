"""MEDA readers of `app/sources/weather.py` (stage 17, AC-1, AC-2, AC-3, AC-6, boundary cases).

Synthetic files are built by `tests/_weather.py`; the real thinned fixtures
(`tests/fixtures/upstream/weather/`) are checked against an independent reading of the same
CSV with the standard `csv` module. No database, no network.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tests._weather import (
    ats_csv,
    counter_oor,
    fixture_one,
    hourly_air,
    lmst_seconds,
    median3,
    pick,
    ps_csv,
    read_samples,
    rhs_csv,
    ws_csv,
)

pytestmark = pytest.mark.unit

SOL = 100


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def fmt_error() -> type[Exception]:
    from app.sources.weather import WeatherFormatError

    return WeatherFormatError


# --- AC-1: CAL_ATS -------------------------------------------------------------------------------


def test_ats_air_temperature_is_the_median_of_t1_t2_t3_ac1(tmp_path: Path) -> None:
    rows = [(0.0, 200.0, 210.0, 205.0), (1.0, 230.5, 220.0, 225.0), (3600.5, 180.0, 181.0, 179.0)]
    path = write(tmp_path, "ats.csv", ats_csv(SOL, rows))

    samples, _ = read_samples("ats", path, SOL)

    air = pick(samples, "air")
    assert [(round(s, 3), v) for s, v in air] == [(0.0, 205.0), (1.0, 225.0), (3600.5, 180.0)]


def test_ats_row_with_t2_out_of_range_is_dropped_and_counted_ac1(tmp_path: Path) -> None:
    rows = [(0.0, 200.0, 200.0, 200.0), (1.0, 200.0, 999.9, 200.0), (2.0, 201.0, 201.0, 201.0)]
    path = write(tmp_path, "ats.csv", ats_csv(SOL, rows))

    samples, counters = read_samples("ats", path, SOL)

    assert [v for _, v in pick(samples, "air")] == [200.0, 201.0]
    assert counter_oor(counters, "air") == 1


@pytest.mark.parametrize(
    ("bad", "expected_count"),
    [(149.99, 1), (300.01, 1), (150.0, 0), (300.0, 0)],
    ids=["below-150", "above-300", "150-is-valid", "300-is-valid"],
)
def test_ats_air_bounds_are_150_to_300_k_inclusive_ac1(
    tmp_path: Path, bad: float, expected_count: int
) -> None:
    path = write(tmp_path, "ats.csv", ats_csv(SOL, [(0.0, 200.0, bad, 200.0)]))

    samples, counters = read_samples("ats", path, SOL)

    assert counter_oor(counters, "air") == expected_count
    assert len(pick(samples, "air")) == 1 - expected_count


def test_ats_t4_and_t5_are_not_read_ac1(tmp_path: Path) -> None:
    """T4/T5 (heated by the rover) are far out of range here: neither counted nor used."""
    rows = [(0.0, 200.0, 201.0, 202.0, 999.0, 999.0)]
    path = write(tmp_path, "ats.csv", ats_csv(SOL, rows))

    samples, counters = read_samples("ats", path, SOL)

    assert pick(samples, "air") == [(0.0, 201.0)]
    assert counter_oor(counters, "air") == 0


def test_ats_row_without_all_three_mast_sensors_gives_no_value_ac1(tmp_path: Path) -> None:
    rows = [(0.0, 200.0, 201.0, None), (1.0, 200.0, 201.0, 202.0)]
    path = write(tmp_path, "ats.csv", ats_csv(SOL, rows))

    samples, counters = read_samples("ats", path, SOL)

    assert [s for s, _ in pick(samples, "air")] == [1.0]
    assert counter_oor(counters, "air") == 0


def test_ats_real_fixture_matches_an_independent_reading_ac1() -> None:
    """Sol 100 fixture: every complete row gives the median; the synthetic glitch row is counted."""
    path = fixture_one(SOL, "CAL_ATS")
    expected = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            t = [float(row[f"ATS_LOCAL_TEMP{i}"]) for i in (1, 2, 3)]
            if all(150 <= x <= 300 for x in t):
                expected.append((lmst_seconds(row["LMST"]), median3(*t)))

    samples, counters = read_samples("ats", path, SOL)

    got = pick(samples, "air")
    assert len(got) == len(expected)
    assert [s for s, _ in got] == pytest.approx([s for s, _ in expected])
    assert [v for _, v in got] == pytest.approx([v for _, v in expected])
    assert counter_oor(counters, "air") == 1  # the appended T2 = 999.9 row (fixtures/README)


def test_ats_hour_of_the_last_second_of_the_day_is_23_ac1(tmp_path: Path) -> None:
    path = write(tmp_path, "ats.csv", ats_csv(SOL, [(86399.9, 200.0, 200.0, 200.0)]))

    samples, _ = read_samples("ats", path, SOL)

    ((seconds, _value),) = pick(samples, "air")
    assert seconds == pytest.approx(86399.9)
    assert int(seconds // 3600) == 23


# --- AC-2: DER_PS and DER_RHS --------------------------------------------------------------------


def test_ps_gaps_are_dropped_silently_and_50_pa_is_out_of_range_ac2(tmp_path: Path) -> None:
    rows = [
        (0.0, 740.5),
        (1.0, "999999999"),
        (2.0, None),
        (3.0, 50.0),
        (4.0, 741.0),
    ]
    path = write(tmp_path, "ps.csv", ps_csv(SOL, rows))

    samples, counters = read_samples("ps", path, SOL)

    assert [v for _, v in pick(samples, "press")] == [740.5, 741.0]
    assert counter_oor(counters, "press") == 1  # only the 50 Pa row; gaps are not counted


@pytest.mark.parametrize(
    ("value", "kept"),
    [(300.0, True), (1200.0, True), (299.99, False), (1200.01, False)],
    ids=["300-valid", "1200-valid", "below-300", "above-1200"],
)
def test_ps_bounds_are_300_to_1200_pa_ac2(tmp_path: Path, value: float, kept: bool) -> None:
    path = write(tmp_path, "ps.csv", ps_csv(SOL, [(0.0, value)]))

    samples, counters = read_samples("ps", path, SOL)

    assert len(pick(samples, "press")) == int(kept)
    assert counter_oor(counters, "press") == int(not kept)


def test_rhs_over_100_is_kept_and_counted_and_the_outlier_is_out_of_range_ac2(
    tmp_path: Path,
) -> None:
    rows = [
        (0.0, 3.5),
        (1.0, "999999999"),
        (2.0, None),
        (3.0, 2680332.0),
        (4.0, 104.5),
        (5.0, 100.0),
        (6.0, 200.0),
        (7.0, 200.01),
    ]
    path = write(tmp_path, "rhs.csv", rhs_csv(SOL, rows))

    samples, counters = read_samples("rhs", path, SOL)

    assert [v for _, v in pick(samples, "humid")] == [3.5, 104.5, 100.0, 200.0]
    assert counters.humidity_over_100 == 2  # 104.5 and 200.0; 100.0 is not over 100
    assert counter_oor(counters, "humid") == 2  # 2 680 332 and 200.01


def test_rhs_negative_humidity_is_out_of_range_ac2(tmp_path: Path) -> None:
    path = write(tmp_path, "rhs.csv", rhs_csv(SOL, [(0.0, -0.5), (1.0, 0.0)]))

    samples, counters = read_samples("rhs", path, SOL)

    assert [v for _, v in pick(samples, "humid")] == [0.0]
    assert counter_oor(counters, "humid") == 1


def test_rhs_and_ps_real_fixtures_show_the_synthetic_glitches_ac2() -> None:
    rhs_samples, rhs_counters = read_samples("rhs", fixture_one(SOL, "DER_RHS"), SOL)
    ps_samples, ps_counters = read_samples("ps", fixture_one(SOL, "DER_PS"), SOL)

    assert counter_oor(rhs_counters, "humid") == 1  # 2 680 332 %
    assert rhs_counters.humidity_over_100 >= 1  # 104.5 % (appended) plus real values over 100
    assert 104.5 in [v for _, v in pick(rhs_samples, "humid")]
    assert 2680332.0 not in [v for _, v in pick(rhs_samples, "humid")]
    assert counter_oor(ps_counters, "press") == 1  # 50 Pa
    assert 50.0 not in [v for _, v in pick(ps_samples, "press")]


def test_rhs_sol_1852_real_fixture_has_values_over_100_but_none_out_of_range_ac2() -> None:
    samples, counters = read_samples("rhs", fixture_one(1852, "DER_RHS"), 1852)

    values = [v for _, v in pick(samples, "humid")]
    over = [v for v in values if v > 100]
    assert counters.humidity_over_100 == len(over) >= 1000
    assert max(values) == pytest.approx(183.76)
    assert counter_oor(counters, "humid") == 0


# --- AC-3: CAL_WS --------------------------------------------------------------------------------


def test_ws_speed_is_the_stable_boom_value_ac3(tmp_path: Path) -> None:
    rows = [(10.0, 3.0, 90.0, 9.9, 270.0, 1, 0)]
    path = write(tmp_path, "ws.csv", ws_csv(SOL, rows))

    samples, _ = read_samples("ws", path, SOL)

    ((_, speed),) = pick(samples, "speed")
    assert speed == pytest.approx(3.0)


def test_ws_speed_of_two_stable_booms_is_their_mean_ac3(tmp_path: Path) -> None:
    path = write(tmp_path, "ws.csv", ws_csv(SOL, [(10.0, 3.0, 90.0, 4.0, 90.0, 1, 1)]))

    samples, _ = read_samples("ws", path, SOL)

    ((_, speed),) = pick(samples, "speed")
    assert speed == pytest.approx(3.5)


def test_ws_row_without_stable_booms_or_without_speed_is_ignored_ac3(tmp_path: Path) -> None:
    rows = [
        (1.0, 3.0, 90.0, 4.0, 90.0, 0, 0),  # speeds present, no stable boom
        (2.0, None, None, None, None, 1, 1),  # stable flags, empty speed
        (3.0, 5.0, 45.0, None, None, 1, 0),  # the only good row
    ]
    path = write(tmp_path, "ws.csv", ws_csv(SOL, rows))

    samples, _ = read_samples("ws", path, SOL)

    assert [(s, v) for s, v in pick(samples, "speed")] == [(3.0, 5.0)]


@pytest.mark.parametrize(
    ("dirs", "expected"),
    [
        ((0.0, 0.0), 0.0),
        ((359.0, 359.0), 359.0),
        ((10.0, 350.0), 0.0),
        ((90.0, 90.0), 90.0),
    ],
    ids=["north", "359", "across-north", "east"],
)
def test_ws_direction_of_two_stable_booms_is_the_sum_of_unit_vectors_ac3(
    tmp_path: Path, dirs: tuple[float, float], expected: float
) -> None:
    rows = [(10.0, 3.0, dirs[0], 3.0, dirs[1], 1, 1)]
    path = write(tmp_path, "ws.csv", ws_csv(SOL, rows))

    samples, _ = read_samples("ws", path, SOL)

    ((_, direction),) = pick(samples, "dir")
    assert (direction - expected + 180) % 360 - 180 == pytest.approx(0.0, abs=0.01)


def test_ws_real_fixture_gives_only_stable_boom_rows_ac3() -> None:
    path = fixture_one(SOL, "CAL_WS")
    stable_rows = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ok1 = row["BOOM1_ALG_STATE_STABLE"] == "1" and row["BOOM1_HORIZONTAL_WIND_SPEED"] != ""
            ok2 = row["BOOM2_ALG_STATE_STABLE"] == "1" and row["BOOM2_HORIZONTAL_WIND_SPEED"] != ""
            stable_rows += ok1 or ok2

    samples, _ = read_samples("ws", path, SOL)

    assert len(pick(samples, "speed")) == stable_rows > 0


def test_ws_without_any_stable_boom_gives_no_samples(tmp_path: Path) -> None:
    rows = [(float(s), 3.0, 90.0, 3.0, 90.0, 0, 0) for s in range(5)]
    path = write(tmp_path, "ws.csv", ws_csv(SOL, rows))

    samples, _ = read_samples("ws", path, SOL)

    assert pick(samples, "speed") == []
    assert pick(samples, "dir") == []


# --- AC-6: rows of another sol -------------------------------------------------------------------


def _ats_with_foreign(foreign: int, own: int) -> bytes:
    own_rows = hourly_air(SOL, [3], per_hour=own)
    other = ats_csv(SOL + 1, hourly_air(SOL + 1, [4], per_hour=foreign)).decode().splitlines()[1:]
    text = ats_csv(SOL, own_rows).decode()
    return (text + "\r\n".join(other) + ("\r\n" if other else "")).encode()


def test_rows_with_a_foreign_sol_are_dropped_and_counted_ac6(tmp_path: Path) -> None:
    path = write(tmp_path, "ats.csv", _ats_with_foreign(foreign=3, own=10))

    samples, counters = read_samples("ats", path, SOL)

    assert len(pick(samples, "air")) == 10
    assert counters.foreign_sol_rows == 3


def test_file_with_exactly_half_foreign_rows_is_still_read_ac6(tmp_path: Path) -> None:
    path = write(tmp_path, "ats.csv", _ats_with_foreign(foreign=10, own=10))

    samples, counters = read_samples("ats", path, SOL)

    assert len(pick(samples, "air")) == 10
    assert counters.foreign_sol_rows == 10


def test_file_with_more_than_half_foreign_rows_is_a_format_error_ac6(tmp_path: Path) -> None:
    path = write(tmp_path, "ats.csv", _ats_with_foreign(foreign=11, own=10))

    with pytest.raises(fmt_error()):
        read_samples("ats", path, SOL)


# --- boundary cases ------------------------------------------------------------------------------


@pytest.mark.parametrize("nl", ["\r\n", "\n"], ids=["crlf", "lf"])
@pytest.mark.parametrize("bom", [False, True], ids=["no-bom", "bom"])
def test_line_endings_and_a_bom_do_not_change_the_samples(
    tmp_path: Path, nl: str, bom: bool
) -> None:
    rows = [(0.0, 200.0, 201.0, 202.0), (60.0, 203.0, 204.0, 205.0)]
    path = write(tmp_path, "ats.csv", ats_csv(SOL, rows, nl=nl, bom=bom))

    samples, _ = read_samples("ats", path, SOL)

    assert pick(samples, "air") == [(0.0, 201.0), (60.0, 204.0)]


def test_two_hertz_and_one_hertz_rows_in_one_file_are_all_read(tmp_path: Path) -> None:
    seconds = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
    rows = [(s, 200.0, 200.0, 200.0) for s in seconds]
    path = write(tmp_path, "ats.csv", ats_csv(SOL, rows))

    samples, _ = read_samples("ats", path, SOL)

    assert [s for s, _ in pick(samples, "air")] == seconds


def test_ats_without_a_temperature_column_is_a_format_error(tmp_path: Path) -> None:
    data = ats_csv(SOL, [(0.0, 200.0, 200.0, 200.0)]).decode().replace("ATS_LOCAL_TEMP2", "X")
    path = write(tmp_path, "ats.csv", data.encode())

    with pytest.raises(fmt_error()):
        read_samples("ats", path, SOL)


def test_empty_file_does_not_crash_the_reader(tmp_path: Path) -> None:
    """0 bytes: either no samples or a WeatherFormatError, never another exception."""
    path = write(tmp_path, "ats.csv", b"")

    try:
        samples, _ = read_samples("ats", path, SOL)
    except fmt_error():
        return
    assert pick(samples, "air") == []


def test_header_only_file_gives_no_samples(tmp_path: Path) -> None:
    path = write(tmp_path, "ps.csv", ps_csv(SOL, []))

    samples, _ = read_samples("ps", path, SOL)

    assert samples == []
