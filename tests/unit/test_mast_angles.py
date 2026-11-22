"""Stage 19 (18B remainder), AC-1..AC-3: `photo_meta.mast_angles` over the `extra` of a photo.

Contract: docs/proposals/stage-19-18b-rest.md (sections 1, 7) and docs/API.md (Photo, `mast`).
Curiosity reads `extended.mast_az` / `extended.mast_el`, Perseverance `extended.mastAz` /
`extended.mastEl` (number strings, possibly in exponent notation); the azimuth is normalised to
[0, 360) and both angles are rounded to 0.01. Pure functions: no database, no network.
"""

import dataclasses
import math
from typing import Any

import pytest

from app.sources.photo_meta import MastAngles, mast_angles

pytestmark = pytest.mark.unit

KEYS = {"curiosity": ("mast_az", "mast_el"), "perseverance": ("mastAz", "mastEl")}
MAST_ROVERS = list(KEYS)


def extra_of(rover: str, azimuth: Any, elevation: Any) -> dict[str, Any]:
    az_key, el_key = KEYS[rover]
    return {"extended": {az_key: azimuth, el_key: elevation}}


def angles(rover: str, azimuth: Any, elevation: Any) -> tuple[float, float] | None:
    result = mast_angles(rover, extra_of(rover, azimuth, elevation))
    return None if result is None else (result.azimuth, result.elevation)


def is_positive_zero_or_nonzero(value: float) -> bool:
    return value != 0.0 or math.copysign(1.0, value) == 1.0


# --- AC-1 ----------------------------------------------------------------------------------


def test_ac1_curiosity_strings_are_parsed_and_rounded_to_hundredths() -> None:
    extra = {"extended": {"mast_az": "328.438", "mast_el": "-44.4084"}}

    assert mast_angles("curiosity", extra) == MastAngles(azimuth=328.44, elevation=-44.41)


def test_ac1_mast_angles_is_a_frozen_dataclass_of_two_floats() -> None:
    result = mast_angles("curiosity", extra_of("curiosity", "1.5", "2.5"))

    assert result is not None
    assert [f.name for f in dataclasses.fields(result)] == ["azimuth", "elevation"]
    assert isinstance(result.azimuth, float) and isinstance(result.elevation, float)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.azimuth = 1.0  # type: ignore[misc]


def test_ac1_curiosity_does_not_read_the_perseverance_keys() -> None:
    assert mast_angles("curiosity", extra_of("perseverance", "10", "10")) is None


def test_ac1_perseverance_does_not_read_the_curiosity_keys() -> None:
    assert mast_angles("perseverance", extra_of("curiosity", "10", "10")) is None


# --- AC-2 ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("azimuth", "elevation", "expected"),
    [
        ("-50.1149", "16.9037", (309.89, 16.9)),
        ("360", "0", (0.0, 0.0)),
        ("-2.93157e-05", "0", (0.0, 0.0)),
        ("180.2", "-0.004", (180.2, 0.0)),
        ("-180", "90", (180.0, 90.0)),
        ("-360", "-90", (0.0, -90.0)),
        ("0", "0", (0.0, 0.0)),
        ("359.999", "1", (0.0, 1.0)),
        ("1.5e2", "-1.0E1", (150.0, -10.0)),
        ("-0.001", "0.001", (0.0, 0.0)),
        (" 12.5 ", "3", (12.5, 3.0)),
    ],
    ids=[
        "negative-azimuth-wraps",
        "360-becomes-0",
        "tiny-negative-exponent-is-0-not-360",
        "over-180-stays",
        "minus-180-is-180",
        "minus-360-is-0",
        "zero-zero",
        "rounds-up-to-360-then-0",
        "exponent-notation",
        "tiny-negative-decimal-is-0",
        "surrounding-spaces",
    ],
)
@pytest.mark.parametrize("rover", MAST_ROVERS)
def test_ac2_azimuth_is_normalised_and_values_are_rounded(
    rover: str, azimuth: str, elevation: str, expected: tuple[float, float]
) -> None:
    got = angles(rover, azimuth, elevation)

    assert got == expected
    assert got is not None
    az, el = got
    assert 0.0 <= az < 360.0
    assert is_positive_zero_or_nonzero(az)  # never -0.0, never 360.0
    assert is_positive_zero_or_nonzero(el)


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize("elevation", ["-0.0", "-0.001", "-0.004"])
def test_ac2_negative_zero_elevation_is_positive_zero(rover: str, elevation: str) -> None:
    got = mast_angles(rover, extra_of(rover, "10", elevation))

    assert got is not None
    assert got.elevation == 0.0 and math.copysign(1.0, got.elevation) == 1.0


@pytest.mark.parametrize("rover", MAST_ROVERS)
def test_ac2_numbers_are_accepted_as_well_as_strings(rover: str) -> None:
    assert angles(rover, -50.1149, 16) == (309.89, 16.0)
    assert angles(rover, 360, -90) == (0.0, -90.0)


def test_ac2_results_are_floats_even_for_integer_input() -> None:
    result = mast_angles("curiosity", extra_of("curiosity", 5, 7))

    assert result is not None
    assert type(result.azimuth) is float and type(result.elevation) is float


# --- AC-3 ----------------------------------------------------------------------------------
# Bounds are inclusive: elevation in [-90, 90], azimuth in [-360, 360].


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize(
    ("azimuth", "elevation", "expected"),
    [("-360", "-90", (0.0, -90.0)), ("360", "90", (0.0, 90.0))],
    ids=["lower-bounds", "upper-bounds"],
)
def test_ac3_bounds_are_inclusive(
    rover: str, azimuth: str, elevation: str, expected: tuple[float, float]
) -> None:
    assert angles(rover, azimuth, elevation) == expected


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize(
    "bad",
    ["UNK", "", "  ", "abc", "1,5", "1 2", "0x10", None, True, False, [], {}, "nan", "inf", "-inf"],
    ids=lambda v: f"{type(v).__name__}-{v!r}",
)
def test_ac3_unusable_azimuth_gives_none(rover: str, bad: Any) -> None:
    assert angles(rover, bad, "10") is None


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize(
    "bad",
    ["UNK", "", "abc", None, True, False, [], {}, "nan", "inf", "-inf"],
    ids=lambda v: f"{type(v).__name__}-{v!r}",
)
def test_ac3_unusable_elevation_gives_none(rover: str, bad: Any) -> None:
    assert angles(rover, "10", bad) is None


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "minus-inf"])
def test_ac3_non_finite_float_gives_none(rover: str, bad: float) -> None:
    assert angles(rover, bad, "10") is None
    assert angles(rover, "10", bad) is None


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize(
    ("azimuth", "elevation"),
    [
        ("10", "90.01"),
        ("10", "-90.01"),
        ("10", "180"),
        ("360.01", "10"),
        ("-360.01", "10"),
        ("720", "10"),
        ("1e400", "10"),
    ],
    ids=[
        "el-90.01",
        "el-minus-90.01",
        "el-180",
        "az-360.01",
        "az-minus-360.01",
        "az-720",
        "overflow",
    ],
)
def test_ac3_out_of_range_gives_none(rover: str, azimuth: str, elevation: str) -> None:
    assert angles(rover, azimuth, elevation) is None


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize(
    "drop", ["az", "el", "both"], ids=["no-azimuth", "no-elevation", "no-angles"]
)
def test_ac3_missing_field_gives_none(rover: str, drop: str) -> None:
    az_key, el_key = KEYS[rover]
    extended = {az_key: "10", el_key: "10"}
    if drop in ("az", "both"):
        del extended[az_key]
    if drop in ("el", "both"):
        del extended[el_key]

    assert mast_angles(rover, {"extended": extended}) is None


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"extended": None},
        {"extended": "UNK"},
        {"extended": []},
        {"extended": {}},
        {"mast_az": "1", "mast_el": "1", "mastAz": "1", "mastEl": "1"},  # not under `extended`
    ],
    ids=[
        "empty",
        "extended-null",
        "extended-string",
        "extended-list",
        "extended-empty",
        "top-level",
    ],
)
def test_ac3_missing_or_malformed_extended_gives_none(rover: str, extra: dict[str, Any]) -> None:
    assert mast_angles(rover, extra) is None


@pytest.mark.parametrize("rover", ["spirit", "opportunity", "ingenuity"])
def test_ac3_other_rovers_give_none_even_with_angle_keys_in_extra(rover: str) -> None:
    for az_key, el_key in KEYS.values():
        assert mast_angles(rover, {"extended": {az_key: "10", el_key: "10"}}) is None
    assert mast_angles(rover, {"extended": {"mastAz": "UNK"}}) is None
    assert mast_angles(rover, {}) is None


def test_ac3_extra_is_not_modified() -> None:
    extra = extra_of("perseverance", "-50.1149", "16.9037")
    before = {"extended": dict(extra["extended"])}

    mast_angles("perseverance", extra)

    assert extra == before


@pytest.mark.parametrize("rover", MAST_ROVERS)
@pytest.mark.parametrize("huge", [10**400, -(10**400), str(10**400)], ids=["int", "neg-int", "str"])
def test_ac3_huge_integer_gives_none_without_raising(rover: str, huge: Any) -> None:
    assert angles(rover, huge, "10") is None
    assert angles(rover, "10", huge) is None
