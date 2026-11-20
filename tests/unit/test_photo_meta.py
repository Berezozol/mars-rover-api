"""Contract tests for app.sources.photo_meta: frame_size and local_time read from `extra`.

Contract: docs/proposals/api-v2-ui-improvements.md section 6 and docs/API.md (Photo, PhotoDetail).
Pure functions over the `extra` JSON of a stored photo; no database, no network.
"""

import copy
import dataclasses
from typing import Any

import pytest

from app.sources.photo_meta import LocalTime, frame_size, local_time

pytestmark = pytest.mark.unit

MER_ROVERS = ["spirit", "opportunity"]


def _m20(dimension: Any = "(1648,1200)") -> dict[str, Any]:
    return {"extended": {"dimension": dimension, "scaleFactor": "4"}}


def _mer(samples: Any = 1024, lines: Any = 800) -> dict[str, Any]:
    return {"line_samples": samples, "lines": lines, "local_true_solar_time": "12:45:26"}


# --- frame_size: recorded formats ----------------------------------------------------------


@pytest.mark.parametrize(
    ("dimension", "expected"),
    [
        ("(1648,1200)", (1648, 1200)),
        ("(1280,960)", (1280, 960)),
        ("(1,1)", (1, 1)),
        ("(999999,999999)", (999999, 999999)),
    ],
    ids=["mcz", "navcam-scaled", "minimum", "six-digits"],
)
def test_frame_size_perseverance_parses_dimension_as_width_height(
    dimension: str, expected: tuple[int, int]
) -> None:
    assert frame_size("perseverance", _m20(dimension)) == expected


@pytest.mark.parametrize("rover", MER_ROVERS)
def test_frame_size_mer_reads_line_samples_as_width_and_lines_as_height(rover: str) -> None:
    assert frame_size(rover, _mer(samples=1024, lines=800)) == (1024, 800)


@pytest.mark.parametrize(
    "extra",
    [
        {},
        _m20(),
        _mer(),
        {"extended": {"dimension": "(1648,1200)", "lmst": "Sol-00005M11:25:43.222"}},
    ],
    ids=["empty", "m20-dimension", "mer-size", "lmst-and-dimension"],
)
def test_frame_size_curiosity_is_always_none(extra: dict[str, Any]) -> None:
    assert frame_size("curiosity", extra) is None


# --- frame_size: garbage -> None -----------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"extended": None},
        {"extended": "UNK"},
        {"extended": {}},
        _m20("UNK"),
        _m20(""),
        _m20(None),
        _m20(1648),
        _m20("(0,5)"),
        _m20("(5,0)"),
        _m20("(0,0)"),
        _m20("(a,b)"),
        _m20("(1648,)"),
        _m20("(,1200)"),
        _m20("(1648,1200"),
        _m20("1648,1200"),
        _m20("(1648;1200)"),
        _m20("(-5,10)"),
        _m20("(1.5,2)"),
        _m20("(1234567,10)"),
        _m20("(1648,1200,3)"),
        _m20("(1648,1200) trailing"),
    ],
    ids=[
        "no-extra",
        "extended-null",
        "extended-string",
        "extended-empty",
        "unk",
        "empty-string",
        "null",
        "number",
        "zero-width",
        "zero-height",
        "zero-both",
        "letters",
        "missing-height",
        "missing-width",
        "no-closing-bracket",
        "no-brackets",
        "wrong-separator",
        "negative",
        "decimal",
        "seven-digits",
        "three-values",
        "trailing-text",
    ],
)
def test_frame_size_perseverance_unparsable_dimension_returns_none(extra: dict[str, Any]) -> None:
    assert frame_size("perseverance", extra) is None


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"line_samples": 1024},
        {"lines": 800},
        _mer(samples=0),
        _mer(lines=0),
        _mer(samples=-1),
        _mer(lines=-800),
        _mer(samples="UNK"),
        _mer(lines=None),
        _mer(samples=None, lines=None),
        _mer(samples=1.5),
        _mer(lines=[800]),
        _mer(samples={"v": 1}),
    ],
    ids=[
        "no-extra",
        "only-width",
        "only-height",
        "zero-width",
        "zero-height",
        "negative-width",
        "negative-height",
        "unk-width",
        "null-height",
        "both-null",
        "fractional-width",
        "list-height",
        "object-width",
    ],
)
@pytest.mark.parametrize("rover", MER_ROVERS)
def test_frame_size_mer_invalid_or_partial_size_returns_none(
    rover: str, extra: dict[str, Any]
) -> None:
    """Width and height are never null separately: one bad value drops both."""
    assert frame_size(rover, extra) is None


def test_frame_size_does_not_read_other_missions_keys() -> None:
    assert frame_size("perseverance", _mer()) is None
    assert frame_size("spirit", _m20()) is None


# --- local_time: recorded formats ----------------------------------------------------------


@pytest.mark.parametrize(
    ("rover", "extra", "time", "kind"),
    [
        ("curiosity", {"extended": {"lmst": "Sol-05073M11:25:43.222"}}, "11:25:43", "lmst"),
        ("perseverance", {"date_taken_mars": "Sol-02024M13:07:38.713"}, "13:07:38", "lmst"),
        ("spirit", {"local_true_solar_time": "12:45:26"}, "12:45:26", "ltst"),
        ("opportunity", {"local_true_solar_time": "01:02:03"}, "01:02:03", "ltst"),
    ],
    ids=["curiosity-lmst", "perseverance-lmst", "spirit-ltst", "opportunity-ltst"],
)
def test_local_time_parses_time_of_day_and_kind(
    rover: str, extra: dict[str, Any], time: str, kind: str
) -> None:
    result = local_time(rover, extra)

    assert result == LocalTime(time=time, kind=kind)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Sol-00001M00:00:00.000", "00:00:00"),
        ("Sol-00001M23:59:59.999", "23:59:59"),  # fraction is dropped, never rounded
        ("Sol-1M09:05:01", "09:05:01"),  # sol number of any width
        ("Sol-123456M10:20:30.5", "10:20:30"),
        ("11:25:43", "11:25:43"),  # prefix is optional
        ("11:25:43.222", "11:25:43"),
    ],
    ids=["midnight", "last-second-no-rounding", "short-sol", "long-sol", "no-prefix", "fraction"],
)
def test_local_time_formats_of_the_value(value: str, expected: str) -> None:
    result = local_time("curiosity", {"extended": {"lmst": value}})

    assert result is not None
    assert result.time == expected
    assert result.kind == "lmst"


def test_local_time_mer_kind_is_ltst_not_lmst() -> None:
    for rover in MER_ROVERS:
        result = local_time(rover, _mer())
        assert result is not None
        assert result.kind == "ltst"


def test_local_time_result_is_immutable_value() -> None:
    result = local_time("curiosity", {"extended": {"lmst": "Sol-00001M10:00:00"}})

    assert result is not None
    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.time = "11:00:00"  # type: ignore[misc]
    assert result == LocalTime("10:00:00", "lmst")
    assert hash(result) == hash(LocalTime("10:00:00", "lmst"))


# --- local_time: garbage -> None -----------------------------------------------------------

BAD_TIMES: list[Any] = [
    "UNK",
    "",
    "   ",
    None,
    1125,
    11.25,
    ["11:25:43"],
    {"time": "11:25:43"},
    "25:00:00",
    "24:00:00",
    "12:60:00",
    "12:00:60",
    "99:99:99",
    "Sol-1M9:5:1",
    "Sol-00001M",
    "Sol-M11:25:43",
    "11:25",
    "11-25-43",
    "abc",
    "Sol-00001X11:25:43",
]
BAD_TIME_IDS = [
    "unk",
    "empty",
    "blank",
    "null",
    "int",
    "float",
    "list",
    "object",
    "hour-25",
    "hour-24",
    "minute-60",
    "second-60",
    "all-99",
    "single-digit-parts",
    "sol-without-time",
    "sol-without-number",
    "no-seconds",
    "dashes",
    "letters",
    "wrong-sol-marker",
]


@pytest.mark.parametrize("value", BAD_TIMES, ids=BAD_TIME_IDS)
def test_local_time_curiosity_garbage_returns_none(value: Any) -> None:
    assert local_time("curiosity", {"extended": {"lmst": value}}) is None


@pytest.mark.parametrize("value", BAD_TIMES, ids=BAD_TIME_IDS)
def test_local_time_perseverance_garbage_returns_none(value: Any) -> None:
    assert local_time("perseverance", {"date_taken_mars": value}) is None


@pytest.mark.parametrize("value", BAD_TIMES, ids=BAD_TIME_IDS)
@pytest.mark.parametrize("rover", MER_ROVERS)
def test_local_time_mer_garbage_returns_none(rover: str, value: Any) -> None:
    assert local_time(rover, {"local_true_solar_time": value}) is None


@pytest.mark.parametrize("rover", ["curiosity", "perseverance", *MER_ROVERS])
@pytest.mark.parametrize(
    "extra",
    [{}, {"extended": None}, {"extended": "UNK"}, {"extended": []}, {"extended": {}}],
    ids=["no-extra", "extended-null", "extended-string", "extended-list", "extended-empty"],
)
def test_local_time_missing_data_returns_none(rover: str, extra: dict[str, Any]) -> None:
    assert local_time(rover, extra) is None


@pytest.mark.parametrize(
    ("rover", "extra"),
    [
        ("curiosity", {"date_taken_mars": "Sol-1M10:00:00", "local_true_solar_time": "10:00:00"}),
        ("perseverance", {"extended": {"lmst": "Sol-00001M10:00:00"}}),
        ("perseverance", {"local_true_solar_time": "10:00:00"}),
        ("spirit", {"extended": {"lmst": "Sol-00001M10:00:00"}, "date_taken_mars": "10:00:00"}),
    ],
    ids=["curiosity", "perseverance-lmst-key", "perseverance-ltst-key", "mer"],
)
def test_local_time_ignores_keys_of_other_missions(rover: str, extra: dict[str, Any]) -> None:
    assert local_time(rover, extra) is None


# --- purity ------------------------------------------------------------------------------


def test_functions_do_not_modify_extra() -> None:
    extra = {
        "extended": {"dimension": "(1648,1200)", "lmst": "Sol-00001M10:00:00"},
        "date_taken_mars": "Sol-00001M10:00:00",
        "line_samples": 4,
        "lines": 5,
        "local_true_solar_time": "10:00:00",
    }
    before = copy.deepcopy(extra)

    for rover in ("curiosity", "perseverance", "spirit", "opportunity"):
        frame_size(rover, extra)
        local_time(rover, extra)

    assert extra == before


def test_frame_size_returns_a_tuple_of_ints() -> None:
    result = frame_size("perseverance", _m20())

    assert isinstance(result, tuple)
    assert all(type(part) is int for part in result)
