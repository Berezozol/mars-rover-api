"""MSL RAD RDR adapter: `RadParser`, `parse_checksums`, `parse_timing_errors`, `parse_qc`.

Stage 21 (docs/proposals/stage-21-rad.md, AC-1 ... AC-5, AC-8 (parser part), AC-9, AC-10).
Real trimmed files live in tests/fixtures/upstream/rad/; edge cases use synthetic files built
by tests/_rad.py in the real layout.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.sources.rad import (
    RadFormatError,
    RadParser,
    parse_checksums,
    parse_qc,
    parse_timing_errors,
)
from tests._rad import (
    F_1649_B,
    F_4843,
    F_SOL10,
    attr,
    fixture,
    md5,
    obs_block,
    rad_file,
    twin_block,
)

pytestmark = pytest.mark.unit


def parse(data: bytes, chunk: int | None = None) -> Any:
    parser = RadParser()
    if chunk is None:
        parser.feed(data)
    else:
        for start in range(0, len(data), chunk):
            parser.feed(data[start : start + chunk])
    return parser.result()


def obs_tuple(obs: Any) -> tuple[Any, ...]:
    return (
        attr(obs, "index"),
        attr(obs, "start_utc_raw"),
        attr(obs, "qc_raw", "qc"),
        attr(obs, "dose_b"),
        attr(obs, "dose_e"),
        attr(obs, "live_s"),
        attr(obs, "bu_count"),
        attr(obs, "bu_read"),
    )


def iso(value: Any) -> str:
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return text.replace(" ", "T")


def counters(summary: Any) -> tuple[int, int, int, int]:
    """(used, dropped_qc, dropped_low_rate, dropped_invalid) of a `SolSummary`."""
    nested = getattr(summary, "observations", None)
    src = nested if nested is not None else summary
    return (
        attr(src, "used", "n_used"),
        attr(src, "dropped_qc", "n_dropped_qc"),
        attr(src, "dropped_low_rate", "n_dropped_low_rate"),
        attr(src, "dropped_invalid", "n_dropped_invalid"),
    )


# --- AC-1 / AC-2 / AC-3: whole files -------------------------------------------------------------


def test_parser_recent_format_returns_header_and_dose_observations_ac1() -> None:
    """AC-1: header, dose observations with live time and BU count, PHA-only twins skipped."""
    data = fixture(F_4843)

    result = parse(data)

    assert attr(result, "start_sol") == 4843
    assert iso(attr(result, "start_time")).startswith("2026-03-21T17:18:00")  # day 080
    assert iso(attr(result, "creation_time")).startswith("2026-06-29T17:52:08")  # day 180
    assert attr(result, "rdr_version") == 0
    assert [obs_tuple(o) for o in attr(result, "observations")] == [
        (0, "2026-080 17:18:00", "00000000", 6.84128, 6.58689, 930, 6454, 6454),
        (2, "2026-080 17:35:06", "00000000", 6.78671, 6.53178, 930, 6487, 6487),
        (22, "2026-080 20:25:48", "00000001", 2.06362, 1.26871, 270, 374, 374),
        (174, "2026-081 17:46:10", "00000000", 6.97246, 6.43259, 930, 6509, 6509),
    ]


def test_parser_recent_format_digest_size_and_tail_cover_all_bytes_ac1() -> None:
    """AC-1: md5, size and the last 64 KiB are taken from every byte of the body."""
    data = fixture(F_4843)
    assert len(data) > 65536  # the fixture is built so that the tail is a proper suffix

    result = parse(data)

    assert attr(result, "md5") == md5(data)
    assert attr(result, "size") == len(data)
    assert attr(result, "tail") == data[-65536:]
    assert attr(result, "blocks") == 7  # 4 dose blocks + 3 PHA-only twins


def test_parser_chunked_input_gives_the_same_result_for_any_chunk_size_ac2() -> None:
    """AC-2: chunks of 7 bytes and 64 KiB (CRLF falls on a seam) change nothing."""
    data = fixture(F_4843)
    whole = parse(data)

    for chunk in (7, 65536):
        part = parse(data, chunk)
        assert [obs_tuple(o) for o in attr(part, "observations")] == [
            obs_tuple(o) for o in attr(whole, "observations")
        ], chunk
        assert attr(part, "md5") == attr(whole, "md5")
        assert attr(part, "tail") == attr(whole, "tail")
        assert attr(part, "start_sol") == 4843


def test_parser_one_byte_chunks_on_a_crlf_file_give_the_same_result_ac2() -> None:
    """AC-2: one byte at a time, so that CR and LF are always fed separately."""
    data = rad_file(
        4843,
        [obs_block(0, dose_b=6.1, dose_e=5.9, live=930, count=6000), twin_block(1)],
    )
    assert b"\r\n" in data

    one = parse(data, 1)
    whole = parse(data)

    assert [obs_tuple(o) for o in attr(one, "observations")] == [
        (0, "2026-080 17:18:00", "00000000", 6.1, 5.9, 930, 6000, 6000)
    ]
    assert attr(one, "md5") == attr(whole, "md5") == md5(data)


def test_parser_early_format_without_obs_duration_takes_live_time_from_counters_ac3() -> None:
    """AC-3: sol 10 (no OBS_DURATION, unquoted QC, START_SOL=0010)."""
    data = fixture(F_SOL10)
    assert b"OBS_DURATION" not in data and b'QC="' not in data

    result = parse(data)

    assert attr(result, "start_sol") == 10
    observations = attr(result, "observations")
    assert [attr(o, "index") for o in observations] == [0, 12, 13, 25]
    assert {attr(o, "live_s") for o in observations} == {960}
    assert [attr(o, "start_utc_raw") for o in observations][:2] == [
        "2012-228 20:41:19",
        "2013-123 21:52:00",  # garbage UTC kept as in the file
    ]
    assert [parse_qc(attr(o, "qc_raw", "qc")) for o in observations] == [0, 0, 0, 0]
    assert [(attr(o, "dose_b"), attr(o, "dose_e")) for o in observations] == [
        (8.40398, 8.17661),
        (8.72671, 8.60465),
        (0.21604, 0.25002),
        (9.77913, 8.26651),
    ]
    assert [attr(o, "bu_count") for o in observations] == [7817, 7817, 26, 7707]


def test_parser_sol_1649_copies_have_different_headers_and_the_same_body() -> None:
    """The duplicate pair of sol 1649: same observations, different `[FILE]` headers."""
    first = parse(fixture("RAD_RDR_2017_085_15_54_1649_V00.TXT"))
    second = parse(fixture(F_1649_B))

    assert [obs_tuple(o) for o in attr(first, "observations")] == [
        obs_tuple(o) for o in attr(second, "observations")
    ]
    assert attr(first, "start_sol") == attr(second, "start_sol") == 1649
    assert attr(first, "creation_time") < attr(second, "creation_time")
    assert attr(first, "start_time") < attr(second, "start_time")
    assert attr(first, "size") == attr(second, "size")
    assert attr(first, "tail") == attr(second, "tail")  # the probe compares exactly this
    assert attr(first, "md5") != attr(second, "md5")


# --- AC-4: QC ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00000000", 0),
        ("00000001", 1),
        ("00000101", 5),
        ("00000100", 4),
        ("00001000", 8),
        ("00001100", 12),
        ("0000001", None),
        ("000000001", None),
        ("ABCDEFGH", None),
        ("0000000x", None),
        ("", None),
    ],
    ids=[
        "zero",
        "bit0",
        "bit0-and-2",
        "bit2-dan",
        "bit3-solar-event",
        "bits-2-3",
        "seven-chars",
        "nine-chars",
        "letters",
        "one-letter",
        "empty",
    ],
)
def test_parse_qc_returns_the_bit_mask_or_none_ac4(raw: str, expected: int | None) -> None:
    assert parse_qc(raw) == expected


def combined(*blocks: str, sol: int = 4843) -> Any:
    from app.services.rad_sync import combine_sol

    result = parse(rad_file(sol, list(blocks)))
    return combine_sol(attr(result, "observations"), 0.5)


@pytest.mark.parametrize("qc", ["00000001", "00000101"], ids=["bit0", "bit0-and-2"])
def test_combine_drops_observations_with_the_quality_bit_ac4(qc: str) -> None:
    summary = combined(obs_block(0), obs_block(2, qc=qc, dose_b=0.5, dose_e=0.4))

    assert counters(summary) == (1, 1, 0, 0)
    assert attr(summary, "dose_b_max") == 6.0  # the dropped dose does not leak in
    assert attr(summary, "dose_b_min") == 6.0


@pytest.mark.parametrize(
    ("qc", "bits"),
    [("00000100", 4), ("00001000", 8), ("00001100", 12)],
    ids=["dan", "solar-event", "both"],
)
def test_combine_keeps_dan_and_solar_event_observations_and_records_the_bits_ac4(
    qc: str, bits: int
) -> None:
    summary = combined(obs_block(0), obs_block(2, qc=qc))

    assert counters(summary) == (2, 0, 0, 0)
    assert attr(summary, "qc_bits") == bits


def test_combine_qc_bits_are_the_or_over_used_observations_ac4() -> None:
    summary = combined(
        obs_block(0, qc="00000100"),
        obs_block(2, qc="00001000"),
        obs_block(4, qc="00000001"),  # dropped: its bits do not count
    )

    assert attr(summary, "qc_bits") == 12
    assert counters(summary) == (2, 1, 0, 0)


@pytest.mark.parametrize("qc", ["0000001", "ABCDEFGH", ""], ids=["short", "letters", "empty"])
def test_combine_drops_observations_with_a_malformed_qc_as_invalid_ac4(qc: str) -> None:
    summary = combined(obs_block(0), obs_block(2, qc=qc))

    assert counters(summary) == (1, 0, 0, 1)


def test_parser_reads_quoted_and_unquoted_qc_alike_ac4() -> None:
    quoted = parse(rad_file(4843, [obs_block(0, qc="00000101", quote_qc=True)]))
    bare = parse(rad_file(4843, [obs_block(0, qc="00000101", quote_qc=False)]))

    got = [parse_qc(attr(attr(r, "observations")[0], "qc_raw", "qc")) for r in (quoted, bare)]
    assert got == [5, 5]


# --- AC-5: invalid observations ------------------------------------------------------------------

INVALID = {
    "no-dose-e": {"dose_e": None},
    "dose-not-a-number": {"dose_b": "abc"},
    "dose-negative": {"dose_e": "-1.5"},
    "dose-nan": {"dose_b": "nan"},
    "dose-inf": {"dose_e": "inf"},
    "count-marker-1": {"count": "-1"},
    "count-marker-2": {"count": "-2", "read": "-2"},
    "duration-marker-1": {"live": "-1"},
    "duration-marker-2": {"live": "-2"},
    "live-zero": {"live": 0},
    "live-too-long": {"live": 7201},
    "no-entry-4": {"entry": None},
    "logic-not-bu": {"logic": "A1U&BU&!C2L"},
    "no-bu-row": {"live": None},
}


@pytest.mark.parametrize("overrides", INVALID.values(), ids=INVALID.keys())
def test_combine_drops_incomplete_or_unreadable_observations_as_invalid_ac5(
    overrides: dict[str, Any],
) -> None:
    summary = combined(obs_block(0), obs_block(2, **overrides))

    assert counters(summary) == (1, 0, 0, 1)
    assert attr(summary, "dose_b_mean") == 6.0


@pytest.mark.parametrize("live", [1, 7200], ids=["min", "max"])
def test_combine_accepts_the_live_time_limits_ac5(live: int) -> None:
    summary = combined(obs_block(0, live=live, count=live * 8))

    assert counters(summary) == (1, 0, 0, 0)
    assert attr(summary, "live_s") == live


def test_combine_returns_none_when_every_observation_is_invalid_ac5() -> None:
    assert combined(obs_block(0, dose_e=None), obs_block(2, live=0)) is None


def test_combine_ignores_blocks_without_dose_sections_ac5() -> None:
    """A PHA-only twin is neither used nor dropped."""
    summary = combined(obs_block(0), twin_block(1), obs_block(2), twin_block(3))

    assert counters(summary) == (2, 0, 0, 0)


def test_combine_count_differs_from_read_is_used_and_logged_ac5(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        summary = combined(obs_block(0, count=7200, read=7100))

    assert counters(summary) == (1, 0, 0, 0)
    assert attr(summary, "bu_count") == 7200  # `count`, not `read`
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


# --- AC-8: limits and format errors --------------------------------------------------------------


def test_parser_line_longer_than_the_limit_raises_format_error_ac8() -> None:
    parser = RadParser(max_line_bytes=1024)
    parser.feed(rad_file(4843, [obs_block(0)]))

    with pytest.raises(RadFormatError):
        parser.feed(b"[PHA: 99]\r\n" + b"1 " * 2000 + b"\r\n")
        parser.result()


def test_parser_default_limit_rejects_a_line_without_newline_over_64_kib_ac8() -> None:
    parser = RadParser()
    parser.feed(rad_file(4843, [obs_block(0)]))

    with pytest.raises(RadFormatError):
        parser.feed(b"9" * 70_000)  # never terminated: the buffer must not grow without bound
        parser.result()


def test_parser_start_sol_differs_from_the_name_is_checked_by_the_caller() -> None:
    """`START_SOL=0010` is read as the number 10 (leading zeros), not as text."""
    result = parse(rad_file(10, [obs_block(0)], start_sol_text="0010", nl="\n"))

    assert attr(result, "start_sol") == 10


def test_parser_file_without_dose_blocks_has_no_observations() -> None:
    result = parse(rad_file(4843, [twin_block(0), twin_block(1)]))

    assert list(attr(result, "observations")) == []
    assert attr(result, "blocks") == 2


def test_parser_empty_input_is_a_format_error_or_an_empty_result() -> None:
    parser = RadParser()
    try:
        result = parser.result()
    except RadFormatError:
        return
    assert list(attr(result, "observations")) == []


def test_parser_keeps_a_repeated_observation_key_for_the_merge_step() -> None:
    """Two blocks with the same `START_OBS_UTC` inside one file are both reported."""
    data = rad_file(4843, [obs_block(0), obs_block(2, utc="2026-080 17:18:00")])

    observations = attr(parse(data), "observations")

    assert [attr(o, "index") for o in observations] == [0, 2]


# --- AC-9: CHECKSUMS.TXT -------------------------------------------------------------------------


def checksums_text() -> str:
    return fixture("CHECKSUMS.TXT").decode("ascii")


def test_parse_checksums_returns_only_surface_daily_files_with_their_sol_ac9() -> None:
    entries = parse_checksums(checksums_text())

    pairs = [(attr(e, "sol"), attr(e, "path")) for e in entries]
    assert sorted(pairs) == sorted(
        [
            (10, "DATA/SOL_00000_00089/RAD_RDR_2012_228_20_41_0010_V00.TXT"),
            (222, "DATA/SOL_00180_00269/RAD_RDR_2013_081_04_32_0222_V00.TXT"),
            (222, "DATA/SOL_00180_00269/RAD_RDR_2013_123_21_52_0222_V00.TXT"),
            (1649, "DATA/SOL_01649_01772/RAD_RDR_2017_085_15_54_1649_V00.TXT"),
            (1649, "DATA/SOL_01649_01772/RAD_RDR_2017_085_16_28_1649_V00.TXT"),
            (4842, "DATA/SOL_04710_04843/RAD_RDR_2026_079_17_02_4842_V00.TXT"),
            (4843, "DATA/SOL_04710_04843/RAD_RDR_2026_080_17_18_4843_V00.TXT"),
        ]
    )


def test_parse_checksums_gives_md5_in_lower_case_and_paths_without_prefix_ac9() -> None:
    entries = parse_checksums(checksums_text())

    for entry in entries:
        assert len(attr(entry, "md5")) == 32 and attr(entry, "md5") == attr(entry, "md5").lower()
        assert not attr(entry, "path").startswith("./")
    by_path = {attr(e, "path"): attr(e, "md5") for e in entries}
    assert by_path["DATA/SOL_00180_00269/RAD_RDR_2013_081_04_32_0222_V00.TXT"] == (
        "2f163cee76c3eeb82e8825286680f873"
    )


@pytest.mark.parametrize(
    "dropped",
    [
        "DATA/SOL_00000_00089/RAD_RDR_2012_228_20_41_0010_V00.LBL",
        "DATA/CRUISE/D_LETCNT.FMT",
        "DATA/CRUISE/RAD_RDR_2011_341_17_42_C011_V00.TXT",
        "DOCUMENT/RAD_RDR_SIS.HTM",
        "AAREADME.TXT",
    ],
    ids=["lbl", "fmt", "cruise", "document", "readme"],
)
def test_parse_checksums_drops_other_files_ac9(dropped: str) -> None:
    assert dropped not in {attr(e, "path") for e in parse_checksums(checksums_text())}


def test_parse_checksums_skips_a_broken_line_with_a_warning_ac9(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        entries = parse_checksums(checksums_text())

    assert len(entries) == 7
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_parse_checksums_accepts_crlf_line_ends_ac9() -> None:
    text = checksums_text().replace("\n", "\r\n")

    assert len(parse_checksums(text)) == 7


def test_parse_checksums_empty_list_gives_no_entries_ac9() -> None:
    assert list(parse_checksums("")) == []


def test_parse_checksums_rejects_short_md5_as_a_broken_line_ac9() -> None:
    line = "abc123  ./DATA/SOL_00000_00089/RAD_RDR_2012_228_20_41_0010_V00.TXT\n"

    assert list(parse_checksums(line)) == []


# --- AC-10: TIMING_ERRORS.TXT --------------------------------------------------------------------


def test_parse_timing_errors_collects_file_names_of_both_error_kinds_ac10() -> None:
    names = parse_timing_errors(fixture("TIMING_ERRORS.TXT").decode("ascii"))

    assert names == {
        "RAD_RDR_2011_354_00_00_C024_V00.TXT",  # Timing Error
        "RAD_RDR_2012_002_00_03_C037_V00.TXT",  # Timing Error
        F_SOL10,  # Date Range Error
    }


def test_parse_timing_errors_lf_and_crlf_give_the_same_set_ac10() -> None:
    text = fixture("TIMING_ERRORS.TXT").decode("ascii")

    assert parse_timing_errors(text.replace("\r\n", "\n")) == parse_timing_errors(text)


def test_parse_timing_errors_repeated_file_is_one_name_ac10() -> None:
    block = (
        "***** Timing Error *****\n     Time found running backward\n"
        "     Found in File: RAD_RDR_2012_002_00_03_C037_V00.TXT\n     Observation: 2\n"
    )

    assert parse_timing_errors(block * 3) == {"RAD_RDR_2012_002_00_03_C037_V00.TXT"}


def test_parse_timing_errors_text_without_blocks_is_an_empty_set_ac10() -> None:
    assert parse_timing_errors("") == set()


# --- byte-exact CRLF fixtures (real PDS3 line ends) ------------------------------------------


@pytest.mark.parametrize("name", [F_4843, F_1649_B], ids=["sol4843", "sol1649"])
def test_parse_crlf_fixture_and_its_lf_copy_give_the_same_content(name: str) -> None:
    raw = fixture(name)
    assert b"\r\n" in raw, "fixtures must be stored byte-exact with CRLF"
    lf = raw.replace(b"\r\n", b"\n")
    assert lf != raw

    crlf_result = parse(raw)
    lf_result = parse(lf)

    assert attr(crlf_result, "start_sol") == attr(lf_result, "start_sol")
    assert attr(crlf_result, "blocks") == attr(lf_result, "blocks")
    assert [obs_tuple(o) for o in attr(crlf_result, "observations")] == [
        obs_tuple(o) for o in attr(lf_result, "observations")
    ]
    assert attr(crlf_result, "observations")  # not vacuous


@pytest.mark.parametrize("name", [F_4843, F_1649_B, F_SOL10], ids=["sol4843", "sol1649", "sol10"])
def test_parse_md5_and_size_are_those_of_the_raw_crlf_bytes(name: str) -> None:
    import hashlib

    raw = fixture(name)

    result = parse(raw, chunk=4096)

    assert attr(result, "md5") == hashlib.md5(raw).hexdigest()  # noqa: S324
    assert attr(result, "size") == len(raw)
    lf = parse(raw.replace(b"\r\n", b"\n"))
    assert attr(lf, "md5") != attr(result, "md5")  # line ends are part of the checksum
