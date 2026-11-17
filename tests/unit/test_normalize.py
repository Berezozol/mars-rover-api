"""Contract tests for app.sources.normalize (clean / to_int / parse_datetime_utc / compact)."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.sources import normalize

pytestmark = pytest.mark.unit


# --- clean -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty"),
        pytest.param("   \t\n", None, id="whitespace-only"),
        pytest.param("UNK", None, id="unk"),
        pytest.param("unk", "unk", id="unk-lowercase-kept"),
        pytest.param("Unk", "Unk", id="unk-mixed-case-kept"),
        pytest.param("  NAVCAM_LEFT  ", "NAVCAM_LEFT", id="string-stripped"),
        pytest.param(" UNK ", None, id="unk-padded"),
        pytest.param("UNKNOWN", "UNKNOWN", id="unk-prefix-kept"),
        pytest.param(0, 0, id="zero-int-kept"),
        pytest.param(0.0, 0.0, id="zero-float-kept"),
        pytest.param(False, False, id="false-kept"),
        pytest.param(47, 47, id="int-kept"),
        pytest.param([1, 2], [1, 2], id="list-unchanged"),
        pytest.param({"a": 1}, {"a": 1}, id="dict-unchanged"),
    ],
)
def test_clean_value_returns_expected(value: Any, expected: Any) -> None:
    result = normalize.clean(value)
    assert result == expected
    assert type(result) is type(expected)


# --- to_int ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(5, 5, id="int"),
        pytest.param(0, 0, id="zero"),
        pytest.param(-3, -3, id="negative-int"),
        pytest.param("12", 12, id="numeric-string"),
        pytest.param(" 12 ", 12, id="numeric-string-padded"),
        pytest.param("4404", 4404, id="m2020-drive-string"),
        pytest.param("0", 0, id="string-zero"),
        pytest.param("-7", -7, id="negative-string"),
        pytest.param(3.0, 3, id="integral-float"),
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty"),
        pytest.param("UNK", None, id="unk"),
        pytest.param("   ", None, id="whitespace"),
    ],
)
def test_to_int_valid_or_empty_returns_int_or_none(
    value: Any, expected: int | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    result = normalize.to_int(value, field="drive")
    assert result == expected
    assert result is None or type(result) is int
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("12a", id="garbage-suffix"),
        pytest.param("abc", id="letters"),
        pytest.param(3.5, id="non-integral-float"),
        pytest.param([1], id="list"),
    ],
)
def test_to_int_unparsable_returns_none_and_warns_with_field(
    value: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    assert normalize.to_int(value, field="drive_field_x") is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "an unparsable number must be logged as a warning"
    assert any("drive_field_x" in r.getMessage() for r in warnings)


@pytest.mark.parametrize("value", [True, False], ids=["true", "false"])
def test_to_int_bool_returns_none(value: bool) -> None:
    assert normalize.to_int(value, field="site") is None


# --- parse_datetime_utc ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            "2026-11-06T12:16:25.848",
            datetime(2026, 11, 6, 12, 16, 25, 848000, tzinfo=UTC),
            id="naive-m2020-is-utc",
        ),
        pytest.param(
            "2024-10-08T06:50:23.000Z",
            datetime(2024, 10, 8, 6, 50, 23, tzinfo=UTC),
            id="z-suffix-msl",
        ),
        pytest.param(
            "2026-11-06T15:59:53Z",
            datetime(2026, 11, 6, 15, 59, 53, tzinfo=UTC),
            id="z-suffix-no-fraction",
        ),
        pytest.param(
            "2024-10-08T08:50:23+02:00",
            datetime(2024, 10, 8, 6, 50, 23, tzinfo=UTC),
            id="positive-offset-converted",
        ),
        pytest.param(
            "2024-10-07T22:50:23-08:00",
            datetime(2024, 10, 8, 6, 50, 23, tzinfo=UTC),
            id="negative-offset-crosses-date",
        ),
        pytest.param(
            "2023-12-13T23:59:59.900",
            datetime(2023, 12, 13, 23, 59, 59, 900000, tzinfo=UTC),
            id="naive-end-of-day",
        ),
    ],
)
def test_parse_datetime_utc_valid_returns_aware_utc(value: str, expected: datetime) -> None:
    result = normalize.parse_datetime_utc(value, field="date_taken")
    assert result == expected
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "value",
    [pytest.param(None, id="none"), pytest.param("", id="empty"), pytest.param("UNK", id="unk")],
)
def test_parse_datetime_utc_empty_returns_none(value: Any) -> None:
    assert normalize.parse_datetime_utc(value, field="date_taken") is None


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("not-a-date", id="garbage"),
        pytest.param("2024-13-45T99:00:00Z", id="out-of-range"),
        pytest.param("Sol-02031M11:30:55.785", id="mars-time"),
    ],
)
def test_parse_datetime_utc_unparsable_returns_none_and_warns(
    value: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    assert normalize.parse_datetime_utc(value, field="date_taken_field_x") is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("date_taken_field_x" in r.getMessage() for r in warnings)


# --- compact -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            {"a": None, "b": "UNK", "c": "", "d": "  ", "keep": "x"},
            {"keep": "x"},
            id="drops-empty-values",
        ),
        pytest.param({"n": 0, "f": False, "z": 0.0}, {"n": 0, "f": False, "z": 0.0}, id="falsy"),
        pytest.param(
            {"nested": {"x": None, "y": "UNK"}, "keep": 1},
            {"keep": 1},
            id="nested-dict-becomes-empty-dropped",
        ),
        pytest.param(
            {"nested": {"x": None, "y": "v", "deeper": {"z": "UNK"}}},
            {"nested": {"y": "v"}},
            id="recursive",
        ),
        pytest.param({"l": [], "d": {}}, {}, id="empty-containers-dropped"),
        pytest.param({"l": [1, "a"]}, {"l": [1, "a"]}, id="non-empty-list-kept"),
        pytest.param(
            {"l": [None, "UNK", " a "], "k": 1},
            {"l": [None, None, "a"], "k": 1},
            id="list-elements-cleaned-but-kept",
        ),
        pytest.param({"a": None, "b": {"c": "UNK"}}, {}, id="all-empty"),
        pytest.param({}, {}, id="empty-dict"),
    ],
)
def test_compact_dict_drops_empty_keys(value: Any, expected: Any) -> None:
    assert normalize.compact(value) == expected
