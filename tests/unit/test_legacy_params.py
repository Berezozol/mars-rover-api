"""parse_legacy_date and legacy_page_window (stage 4 contract, LEGACY-API.md deviations)."""

import time
from datetime import date

import pytest

from app.core.legacy_params import (
    LegacyParamError,
    legacy_page_window,
    parse_legacy_date,
    parse_legacy_int,
)

pytestmark = pytest.mark.unit


# --- parse_legacy_date: YYYY-M-D, leading zeros optional ---------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2015-6-3", date(2015, 6, 3)),
        ("2015-06-03", date(2015, 6, 3)),
        ("2015-6-03", date(2015, 6, 3)),
        ("2015-06-3", date(2015, 6, 3)),
        ("2012-8-7", date(2012, 8, 7)),
        ("2014-12-05", date(2014, 12, 5)),
        ("2016-2-29", date(2016, 2, 29)),
    ],
)
def test_parse_legacy_date_valid(value: str, expected: date) -> None:
    assert parse_legacy_date(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "abc",
        "2015-13-01",
        "2015-0-10",
        "2015-02-30",
        "2015-2-29",
        "2015-06-00",
        "2015/06/03",
        "20150603",
        "2015-06",
        "2015-6-3-1",
        "2015-06-03x",
        "2015-06-03T00:00:00",
        "2015--06-03",
        "2015-six-03",
    ],
)
def test_parse_legacy_date_invalid_returns_none(value: str) -> None:
    assert parse_legacy_date(value) is None


# --- legacy_page_window -> (limit, offset) | None ------------------------------------------


@pytest.mark.parametrize(
    ("page", "per_page", "expected"),
    [
        (None, None, None),
        (None, "30", None),
        ("1", None, (25, 0)),
        ("2", None, (25, 25)),
        ("9", None, (25, 200)),
        ("1", "30", (30, 0)),
        ("2", "30", (30, 30)),
        ("3", "10", (10, 20)),
        ("1", "200", (200, 0)),
        ("1", "201", (200, 0)),
        ("2", "1000", (200, 200)),
        ("0", None, (25, 0)),
        ("-3", None, (25, 0)),
        ("0", "30", (30, 0)),
        ("1", "0", (25, 0)),
        ("2", "0", (25, 25)),
        ("2", "-5", (25, 25)),
    ],
    ids=[
        "no-page",
        "per-page-without-page",
        "page-1",
        "page-2",
        "page-9",
        "per-page-30",
        "per-page-30-page-2",
        "per-page-10-page-3",
        "per-page-200",
        "per-page-201-capped",
        "per-page-1000-capped-page-2",
        "page-0-is-1",
        "negative-page-is-1",
        "page-0-per-page-30",
        "per-page-0-is-25",
        "per-page-0-page-2",
        "negative-per-page-is-25",
    ],
)
def test_legacy_page_window(
    page: str | None, per_page: str | None, expected: tuple[int, int] | None
) -> None:
    assert legacy_page_window(page, per_page) == expected


@pytest.mark.parametrize(
    ("page", "per_page", "param"),
    [
        ("abc", None, "page"),
        ("1.5", None, "page"),
        ("1e2", "30", "page"),
        ("1", "abc", "per_page"),
        ("1", "2.5", "per_page"),
    ],
    ids=["page-text", "page-float", "page-exp", "per-page-text", "per-page-float"],
)
def test_legacy_page_window_non_integer_raises(page: str, per_page: str | None, param: str) -> None:
    with pytest.raises(LegacyParamError) as exc_info:
        legacy_page_window(page, per_page)

    # LegacyBadRequest: the message becomes the v1 {"errors": ...} text.
    assert exc_info.value.status_code == 400
    assert exc_info.value.message == f"Invalid {param}"


# --- huge integers and leading zeros (review round 1, MAJOR 2) -----------------------------

HUGE = "9" * 5000  # longer than Python's default int-from-str digit limit (4300)
INT32_MAX = 2**31 - 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0),
        ("7", 7),
        ("0007", 7),
        ("-0007", -7),
        ("-0", 0),
        ("000", 0),
        ("2147483648", 2**31),
    ],
)
def test_parse_legacy_int_plain_values(value: str, expected: int) -> None:
    assert parse_legacy_int(value, "sol") == expected


def test_parse_legacy_int_huge_positive_does_not_raise() -> None:
    # The exact value may be saturated; it must stay a huge positive integer.
    assert parse_legacy_int(HUGE, "sol") > INT32_MAX


def test_parse_legacy_int_huge_negative_does_not_raise() -> None:
    assert parse_legacy_int("-" + HUGE, "sol") < -INT32_MAX


@pytest.mark.parametrize(
    "value", ["", "abc", "1.0", "1e3", "0x10", "1_000", "+1", " 1", "1 ", "--1", "1-", "１"]
)
def test_parse_legacy_int_not_strict_integer_raises(value: str) -> None:
    with pytest.raises(LegacyParamError) as exc_info:
        parse_legacy_int(value, "sol")

    assert exc_info.value.message == "Invalid sol"


@pytest.mark.parametrize("per_page", [None, "30", HUGE])
def test_legacy_page_window_huge_page_is_far_past_the_end(per_page: str | None) -> None:
    window = legacy_page_window(HUGE, per_page)

    assert window is not None
    limit, offset = window
    assert 1 <= limit <= 200
    assert offset > INT32_MAX


@pytest.mark.parametrize("per_page", [HUGE, str(2**63)])
def test_legacy_page_window_huge_per_page_is_capped(per_page: str) -> None:
    assert legacy_page_window("1", per_page) == (200, 0)
    assert legacy_page_window("2", per_page) == (200, 200)


@pytest.mark.parametrize(
    ("page", "per_page", "expected"),
    [
        ("-" + HUGE, None, (25, 0)),
        ("1", "-" + HUGE, (25, 0)),
        ("0002", "0030", (30, 30)),
        ("0001", None, (25, 0)),
    ],
    ids=["huge-negative-page", "huge-negative-per-page", "leading-zeros", "page-0001"],
)
def test_legacy_page_window_huge_negative_and_leading_zeros(
    page: str, per_page: str | None, expected: tuple[int, int]
) -> None:
    assert legacy_page_window(page, per_page) == expected


@pytest.mark.parametrize("per_page", ["abc", "", "1.5"])
def test_legacy_page_window_per_page_ignored_without_page(per_page: str) -> None:
    assert legacy_page_window(None, per_page) is None


def test_legacy_page_window_empty_page_raises() -> None:
    with pytest.raises(LegacyParamError) as exc_info:
        legacy_page_window("", None)

    assert exc_info.value.message == "Invalid page"


# --- no catastrophic backtracking (review round 2) -----------------------------------------

LONG_ZEROS = "0" * 50000
# Generous bound: a linear parse of 50k characters takes well under a millisecond.
MAX_SECONDS = 0.1


@pytest.mark.parametrize(
    "value", [LONG_ZEROS + "x", "-" + LONG_ZEROS + "x"], ids=["zeros-x", "minus-zeros-x"]
)
def test_parse_legacy_int_long_invalid_input_fails_fast(value: str) -> None:
    started = time.perf_counter()
    with pytest.raises(LegacyParamError) as exc_info:
        parse_legacy_int(value, "sol")
    elapsed = time.perf_counter() - started

    assert exc_info.value.message == "Invalid sol"
    assert elapsed < MAX_SECONDS


def test_parse_legacy_int_long_valid_zeros_is_fast() -> None:
    started = time.perf_counter()
    result = parse_legacy_int(LONG_ZEROS, "sol")
    elapsed = time.perf_counter() - started

    assert result == 0
    assert elapsed < MAX_SECONDS
