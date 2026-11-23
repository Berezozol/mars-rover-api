"""REMS `RMD` reader of `app/sources/weather.py` (stage 17, AC-4, AC-5, AC-6).

Column numbers (0-based) from DATA-SOURCES.md: wind 3, 5, 6; ground 7 (+ mask 10); air 15
(+ mask 16); UV_ABC 20; humidity 30; pressure 37 (+ mask 39). Mask bytes are counted in the
unpadded string.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._weather import (
    counter_oor,
    fixture_one,
    pick,
    read_samples,
    rmd_row,
    rmd_tab,
)

pytestmark = pytest.mark.unit

SOL = 9


def write(tmp_path: Path, lines: list[str], nl: str = "\r\n") -> Path:
    path = tmp_path / "rmd.tab"
    path.write_bytes(rmd_tab(lines, nl=nl))
    return path


def err() -> type[Exception]:
    from app.sources.weather import WeatherFormatError

    return WeatherFormatError


# --- AC-4: masks and filters ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mask", "kept"),
    [
        ("101XX1111", True),
        ("000000011", True),  # bytes 0..6 do not matter (byte 0 = "ASIC > -50 C" drops the night)
        ("101XX1101", False),  # byte 7 = 0
        ("101XX1110", False),  # byte 8 = 0
        ("101XX111X", False),  # byte 8 unknown
        ("101XX11X1", False),  # byte 7 unknown
    ],
    ids=["good", "good-byte0-zero", "byte7-bad", "byte8-bad", "byte8-X", "byte7-X"],
)
def test_air_temperature_needs_bytes_7_and_8_of_the_mask_ac4(
    tmp_path: Path, mask: str, kept: bool
) -> None:
    path = write(tmp_path, [rmd_row(SOL, 0.0, air=210.0, air_mask=mask)])

    samples, _ = read_samples("rmd", path, SOL)

    assert pick(samples, "air") == ([(0.0, 210.0)] if kept else [])


@pytest.mark.parametrize(
    ("mask", "pressure", "kept"),
    [("11", 750.0, True), ("01", 750.0, True), ("10", 750.0, False), ("XX", 750.0, False)],
    ids=["good", "byte0-ignored", "byte1-bad", "unknown"],
)
def test_pressure_needs_byte_1_of_the_mask_ac4(
    tmp_path: Path, mask: str, pressure: float, kept: bool
) -> None:
    path = write(tmp_path, [rmd_row(SOL, 0.0, pressure=pressure, ps_mask=mask)])

    samples, _ = read_samples("rmd", path, SOL)

    assert pick(samples, "press") == ([(0.0, pressure)] if kept else [])


def test_pressure_gap_marker_is_dropped_without_counting_ac4(tmp_path: Path) -> None:
    path = write(tmp_path, [rmd_row(SOL, 0.0, pressure=None)])

    samples, counters = read_samples("rmd", path, SOL)

    assert pick(samples, "press") == []
    assert counter_oor(counters, "press") == 0


@pytest.mark.parametrize(
    ("mask", "kept"),
    [("1111", True), ("1100", True), ("0111", False), ("1011", False), ("XX11", False)],
    ids=["good", "later-bytes-ignored", "byte0-bad", "byte1-bad", "unknown"],
)
def test_ground_temperature_needs_bytes_0_and_1_of_the_gts_mask_ac4(
    tmp_path: Path, mask: str, kept: bool
) -> None:
    path = write(tmp_path, [rmd_row(SOL, 0.0, ground=220.0, gts_mask=mask)])

    samples, _ = read_samples("rmd", path, SOL)

    assert pick(samples, "ground") == ([(0.0, 220.0)] if kept else [])


def test_uv_is_taken_when_not_negative_ac4(tmp_path: Path) -> None:
    lines = [
        rmd_row(SOL, 0.0, uv=0.0),
        rmd_row(SOL, 1.0, uv=12.5),
        rmd_row(SOL, 2.0, uv=-1.0),
        rmd_row(SOL, 3.0, uv=None),
    ]

    samples, _ = read_samples("rmd", write(tmp_path, lines), SOL)

    assert pick(samples, "uv") == [(0.0, 0.0), (1.0, 12.5)]


def test_humidity_has_no_mask_and_gap_markers_are_dropped_ac4(tmp_path: Path) -> None:
    lines = [rmd_row(SOL, 0.0, humidity=3.5), rmd_row(SOL, 1.0, humidity=None)]

    samples, _ = read_samples("rmd", write(tmp_path, lines), SOL)

    assert pick(samples, "humid") == [(0.0, 3.5)]


def test_wind_columns_are_not_read_ac4(tmp_path: Path) -> None:
    lines = [rmd_row(SOL, float(i), wind=(3.0, 0.1, 45.0)) for i in range(3)]

    samples, _ = read_samples("rmd", write(tmp_path, lines), SOL)

    assert [p for _, p, _ in samples if "wind" in p.lower()] == []
    assert len(pick(samples, "air")) == 3  # the rows themselves are read


def test_all_gap_markers_row_gives_no_samples_ac4(tmp_path: Path) -> None:
    line = rmd_row(SOL, 0.0, air=None, pressure=None, ground=None, uv=None, humidity=None)

    samples, counters = read_samples("rmd", write(tmp_path, [line]), SOL)

    assert samples == []
    assert counter_oor(counters, "air") == 0


def test_quoted_fields_and_lmst_are_parsed_ac4(tmp_path: Path) -> None:
    line = rmd_row(SOL, 45296.759, air=205.5)  # 12:34:56.759

    samples, _ = read_samples("rmd", write(tmp_path, [line]), SOL)

    ((seconds, value),) = pick(samples, "air")
    assert seconds == pytest.approx(45296.759)
    assert value == 205.5


def test_row_with_39_fields_is_a_format_error_with_the_line_number_ac4(tmp_path: Path) -> None:
    lines = [rmd_row(SOL, 0.0), rmd_row(SOL, 1.0), rmd_row(SOL, 2.0, drop_fields=1)]

    with pytest.raises(err()) as caught:
        read_samples("rmd", write(tmp_path, lines), SOL)

    assert "3" in str(caught.value)


@pytest.mark.parametrize("nl", ["\r\n", "\n"], ids=["crlf", "lf"])
def test_line_ending_does_not_change_the_samples(tmp_path: Path, nl: str) -> None:
    lines = [rmd_row(SOL, 0.0, air=200.0), rmd_row(SOL, 1.0, air=201.0)]

    samples, _ = read_samples("rmd", write(tmp_path, lines, nl=nl), SOL)

    assert pick(samples, "air") == [(0.0, 200.0), (1.0, 201.0)]


def test_empty_file_gives_no_samples(tmp_path: Path) -> None:
    path = tmp_path / "rmd.tab"
    path.write_bytes(b"")

    try:
        samples, _ = read_samples("rmd", path, SOL)
    except err():
        return
    assert samples == []


# --- AC-5: glitch of sol 30 ----------------------------------------------------------------------


def test_ground_glitch_with_a_bad_mask_is_not_in_the_data_but_counted_ac5(tmp_path: Path) -> None:
    lines = [
        rmd_row(30, 0.0, ground=180.0),
        rmd_row(30, 1.0, ground=92.6, gts_mask="0111"),  # synthetic copy of the real glitch
        rmd_row(30, 2.0, ground=181.0),
    ]

    samples, counters = read_samples("rmd", write(tmp_path, lines), 30)

    assert [v for _, v in pick(samples, "ground")] == [180.0, 181.0]
    assert counter_oor(counters, "ground") == 1  # bounds are checked before the mask


def test_ground_glitch_with_a_good_mask_is_dropped_too_ac5(tmp_path: Path) -> None:
    lines = [rmd_row(30, 0.0, ground=92.6, gts_mask="1111")]

    samples, counters = read_samples("rmd", write(tmp_path, lines), 30)

    assert pick(samples, "ground") == []
    assert counter_oor(counters, "ground") == 1


@pytest.mark.parametrize(
    ("value", "counted"),
    [(120.0, 0), (330.0, 0), (119.99, 1), (330.01, 1)],
    ids=["120-valid", "330-valid", "below", "above"],
)
def test_ground_bounds_are_120_to_330_k_ac5(tmp_path: Path, value: float, counted: int) -> None:
    path = write(tmp_path, [rmd_row(30, 0.0, ground=value)])

    samples, counters = read_samples("rmd", path, 30)

    assert counter_oor(counters, "ground") == counted
    assert len(pick(samples, "ground")) == 1 - counted


def test_real_fixture_sol_30_counts_the_glitches_and_keeps_the_rest_ac5() -> None:
    path = fixture_one(30, "RMD")
    lines = path.read_text(encoding="ascii").splitlines()
    cells = [[c.strip().strip('"').strip() for c in line.split(",")] for line in lines]
    bad = [float(c[7]) for c in cells if float(c[7]) != -999.0 and not 120 <= float(c[7]) <= 330]
    assert bad, "the fixture must keep the real glitch rows"

    samples, counters = read_samples("rmd", path, 30)

    assert counter_oor(counters, "ground") == len(bad)
    assert all(120 <= v <= 330 for _, v in pick(samples, "ground"))


def test_real_fixture_sol_9_reads_air_and_pressure_without_errors() -> None:
    samples, counters = read_samples("rmd", fixture_one(9, "RMD"), 9)

    air = [v for _, v in pick(samples, "air")]
    pressure = [v for _, v in pick(samples, "press")]
    assert air and min(air) >= 150 and max(air) <= 300
    assert pressure and min(pressure) >= 300 and max(pressure) <= 1200
    assert counters.foreign_sol_rows == 0


# --- AC-6: foreign sol rows ----------------------------------------------------------------------


def test_rows_of_another_sol_are_dropped_and_counted_ac6(tmp_path: Path) -> None:
    lines = [rmd_row(SOL, float(i)) for i in range(6)] + [rmd_row(SOL + 1, 0.0)] * 2

    samples, counters = read_samples("rmd", write(tmp_path, lines), SOL)

    assert len(pick(samples, "air")) == 6
    assert counters.foreign_sol_rows == 2


def test_file_with_more_than_half_foreign_rows_is_a_format_error_rems_ac6(tmp_path: Path) -> None:
    lines = [rmd_row(SOL, float(i)) for i in range(4)] + [rmd_row(SOL + 1, 0.0)] * 5

    with pytest.raises(err()):
        read_samples("rmd", write(tmp_path, lines), SOL)
