"""`DER_TIRS` reader: ground temperature estimate of Perseverance (stage 17, part 17G).

Method of docs/reports/stage-17-meda-ground.md: `T = (UPWARD_LW_IRRADIANCE / sigma) ** 0.25`;
only rows with `ROVER_LOW_TILT` = 1 and the wheel not in the downward field of view
(`WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV` is not 0); bounds 120-330 K are checked on those rows
(out of range -> counted under the ground parameter); sol 1 gives nothing.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tests._weather import (
    counter_oor,
    fixture_one,
    flux_of,
    lmst_seconds,
    pick,
    read_samples,
    tirs_csv,
)

pytestmark = pytest.mark.unit

SIGMA = 5.670374419e-8


def write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "tirs.csv"
    path.write_bytes(data)
    return path


def ground(samples: list[tuple[float, str, float]]) -> list[tuple[float, float]]:
    return pick(samples, "ground")


@pytest.mark.parametrize("kelvin", [150.0, 200.0, 268.7, 300.0])
def test_temperature_is_the_fourth_root_of_flux_over_sigma_ac49(
    tmp_path: Path, kelvin: float
) -> None:
    flux = SIGMA * kelvin**4
    path = write(tmp_path, tirs_csv(100, [(10.0, flux, 1, 1)]))

    samples, _ = read_samples("tirs", path, 100)

    ((seconds, value),) = ground(samples)
    assert seconds == pytest.approx(10.0)
    assert value == pytest.approx(kelvin, abs=0.01)


def test_worked_example_97_57_w_m2_is_about_203_7_k_ac49(tmp_path: Path) -> None:
    """The first row of the real sol 100 file: 97.57 W/m2."""
    path = write(tmp_path, tirs_csv(100, [(0.56, 97.57, 1, 1)]))

    samples, _ = read_samples("tirs", path, 100)

    assert ground(samples)[0][1] == pytest.approx((97.57 / SIGMA) ** 0.25)
    assert ground(samples)[0][1] == pytest.approx(203.7, abs=0.1)


@pytest.mark.parametrize(
    ("tilt", "wheel", "kept"),
    [
        (1, 1, True),
        (1, "", True),  # an empty wheel flag counts as "not seen"
        (0, 1, False),  # rover tilted
        ("", 1, False),  # tilt unknown
        (1, 0, False),  # the wheel is in the field of view
        (0, 0, False),
    ],
    ids=["good", "wheel-empty", "tilted", "tilt-empty", "wheel-seen", "both-bad"],
)
def test_rows_need_a_level_rover_and_no_wheel_in_view_ac49(
    tmp_path: Path, tilt: int | str, wheel: int | str, kept: bool
) -> None:
    path = write(tmp_path, tirs_csv(100, [(10.0, flux_of(250.0), tilt, wheel)]))

    samples, counters = read_samples("tirs", path, 100)

    assert len(ground(samples)) == int(kept)
    assert counter_oor(counters, "ground") == 0


def test_filtered_rows_are_not_checked_against_the_bounds_ac49(tmp_path: Path) -> None:
    """A silly flux on a tilted row is dropped by the filter first and is not an outlier."""
    path = write(tmp_path, tirs_csv(100, [(10.0, 1.0, 0, 1), (11.0, 1.0, 1, 0)]))

    samples, counters = read_samples("tirs", path, 100)

    assert ground(samples) == []
    assert counter_oor(counters, "ground") == 0


@pytest.mark.parametrize(
    ("kelvin", "counted"),
    [(119.9, 1), (330.1, 1), (120.5, 0), (329.9, 0)],
    ids=["below-120", "above-330", "near-lower-edge", "near-upper-edge"],
)
def test_bounds_120_to_330_k_count_out_of_range_ac49(
    tmp_path: Path, kelvin: float, counted: int
) -> None:
    path = write(tmp_path, tirs_csv(100, [(10.0, flux_of(kelvin), 1, 1)]))

    samples, counters = read_samples("tirs", path, 100)

    assert counter_oor(counters, "ground") == counted
    assert len(ground(samples)) == 1 - counted


def test_sol_1_gives_no_ground_values_ac49(tmp_path: Path) -> None:
    path = write(tmp_path, tirs_csv(1, [(10.0, flux_of(250.0), 1, 1)]))

    samples, _ = read_samples("tirs", path, 1)

    assert ground(samples) == []


def test_gaps_and_zero_flux_are_skipped_without_counting(tmp_path: Path) -> None:
    rows = [(1.0, None, 1, 1), (2.0, "", 1, 1), (3.0, 0.0, 1, 1), (4.0, flux_of(250.0), 1, 1)]
    path = write(tmp_path, tirs_csv(100, rows))

    samples, counters = read_samples("tirs", path, 100)

    assert [s for s, _ in ground(samples)] == [4.0]
    assert counter_oor(counters, "ground") == 0


def test_other_products_params_are_not_produced_by_tirs(tmp_path: Path) -> None:
    path = write(tmp_path, tirs_csv(100, [(10.0, flux_of(250.0), 1, 1)]))

    samples, _ = read_samples("tirs", path, 100)

    assert {p for _, p, _ in samples} == {samples[0][1]}


def test_real_fixture_matches_an_independent_reading_ac49() -> None:
    path = fixture_one(100, "DER_TIRS")
    expected = []
    outliers = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (
                row["ROVER_LOW_TILT"] != "1"
                or row["WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV"] == "0"
            ):
                continue
            kelvin = (float(row["UPWARD_LW_IRRADIANCE"]) / SIGMA) ** 0.25
            if 120 <= kelvin <= 330:
                expected.append((lmst_seconds(row["LMST"]), kelvin))
            else:
                outliers += 1

    samples, counters = read_samples("tirs", path, 100)

    got = ground(samples)
    assert outliers == 1  # the synthetic 1.0 W/m2 row (fixtures README)
    assert counter_oor(counters, "ground") == 1
    assert [s for s, _ in got] == pytest.approx([s for s, _ in expected])
    assert [v for _, v in got] == pytest.approx([v for _, v in expected])
    assert 150 < min(v for _, v in got) < 230 < 260 < max(v for _, v in got) < 330


@pytest.mark.parametrize("nl", ["\r\n", "\n"], ids=["crlf", "lf"])
def test_line_endings_do_not_matter(tmp_path: Path, nl: str) -> None:
    path = write(tmp_path, tirs_csv(100, [(10.0, flux_of(250.0), 1, 1)], nl=nl))

    samples, _ = read_samples("tirs", path, 100)

    assert len(ground(samples)) == 1
