"""Parser of the NASA "Mars Rock Samples" page (app/sources/samples.py; stage 13, ADR-2/ADR-3).

Fixtures: tests/fixtures/upstream/samples/rock-samples-trimmed.html (the real page cut down to
the head and rows 1, 8, 17, 25, 26, 29, 30) and
tests/fixtures/upstream/synthetic/samples_quirks.html. Refusal variants are built in code from
the trimmed page (tests/_samples.py). No database, no network.

Acceptance (ADR-3): strict for headers, `Sample No.`, `Sol Sealed`, `Date Sealed` and a
non-empty name (any violation raises `SamplesFormatError`, nothing is returned); soft for
enumerations, height and the dossier link (value `null`, raw text kept where the contract
says so).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest

from app.sources.http import UpstreamError
from app.sources.samples import (
    SAMPLES_ROVER,
    ParsedSamples,
    SampleRecord,
    SamplesFormatError,
    parse_samples,
)
from tests._samples import (
    COL_CURRENT,
    COL_DATE,
    COL_FEATURE,
    COL_HEIGHT,
    COL_NAME,
    COL_NOTES,
    COL_ROCK,
    COL_SOL,
    COL_TYPE,
    EXPECTED,
    HEADERS,
    NUMBERS,
    SOURCE_MODIFIED,
    as_plain,
    build_page,
    page_with_cell,
    page_with_row,
    page_without_number,
    quirks_bytes,
    replace_in_page,
    sample_row,
    split_table,
    trimmed_bytes,
    trimmed_html,
)

pytestmark = pytest.mark.unit


def by_number(parsed: ParsedSamples) -> dict[int, dict[str, Any]]:
    plain = {record.number: as_plain(record) for record in parsed.items}
    assert len(plain) == len(parsed.items), "numbers are unique"
    return plain


# --- the trimmed real page ---------------------------------------------------------------------


def test_samples_rover_is_perseverance() -> None:
    assert SAMPLES_ROVER == "perseverance"


def test_parse_trimmed_page_returns_seven_rows_in_page_order() -> None:
    parsed = parse_samples(trimmed_bytes())

    assert isinstance(parsed, ParsedSamples)
    assert all(isinstance(item, SampleRecord) for item in parsed.items)
    assert dataclasses.is_dataclass(parsed.items[0])
    assert [item.number for item in parsed.items] == NUMBERS


@pytest.mark.parametrize("number", NUMBERS, ids=[f"sample-{n}" for n in NUMBERS])
def test_parse_trimmed_page_reference_values(number: int) -> None:
    parsed = by_number(parse_samples(trimmed_bytes()))

    assert parsed[number] == EXPECTED[number]


def test_parse_source_modified_is_the_page_meta_converted_to_utc() -> None:
    parsed = parse_samples(trimmed_bytes())

    assert parsed.source_modified_at == SOURCE_MODIFIED  # 12:45:46-04:00
    assert parsed.source_modified_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_parse_page_without_modified_meta_gives_null_source_modified() -> None:
    page = replace_in_page(
        '<meta property="article:modified_time" content="2026-06-20T12:45:46-04:00" />', ""
    )

    parsed = parse_samples(page)

    assert parsed.source_modified_at is None
    assert [item.number for item in parsed.items] == NUMBERS


def test_parse_height_in_table_keeps_row_1_all_na_values_null() -> None:
    first = by_number(parse_samples(trimmed_bytes()))[1]

    assert (first["height_cm"], first["height_note"], first["rock_type"]) == (None, None, None)


def test_parse_row_1_dossier_is_the_pds_link_not_the_mirror() -> None:
    first = by_number(parse_samples(trimmed_bytes()))[1]

    assert first["dossier_url"].startswith("https://pds-geosciences.wustl.edu/")
    assert "internal_resources" not in first["dossier_url"]


def test_parse_keeps_typographic_apostrophe_and_diacritics_as_on_the_page() -> None:
    parsed = by_number(parse_samples(trimmed_bytes()))

    assert "Ch’ał" in parsed[8]["sampling_location"]  # U+2019 stays, not "'"
    assert parsed[8]["name"] == 'Ha\'ahóni (aka "Hahonih")'  # aka is not split off


def test_parse_is_repeatable_for_the_same_body() -> None:
    assert parse_samples(trimmed_bytes()) == parse_samples(trimmed_bytes())


def test_parse_ignores_markup_changes_outside_the_table() -> None:
    noisy = replace_in_page(
        "<main>",
        '<main><script nonce="a1b2c3">var x = 1;</script><div class="ad">noise</div>',
    )

    assert parse_samples(noisy).items == parse_samples(trimmed_bytes()).items


# --- headers by name, not by position ----------------------------------------------------------


def test_parse_columns_are_matched_by_header_name_not_position() -> None:
    headers, rows = split_table()
    order = [3, 0, 10, 5, 1, 9, 4, 2, 8, 6, 7]  # a scrambled column order
    page = build_page([headers[i] for i in order], [[row[i] for i in order] for row in rows])

    assert by_number(parse_samples(page)) == EXPECTED


def test_parse_extra_unknown_column_is_tolerated() -> None:
    headers, rows = split_table()
    page = build_page([*headers, "Surprise"], [[*row, "x"] for row in rows])

    assert by_number(parse_samples(page)) == EXPECTED


@pytest.mark.parametrize(
    ("column", "variant"),
    [
        pytest.param(0, "SAMPLE NO.", id="upper-case"),
        pytest.param(1, "date sealed", id="lower-case"),
        pytest.param(9, "Sample Height cm  inch", id="br-as-spaces"),
        pytest.param(7, "Science Notes", id="no-nbsp"),
        pytest.param(3, "&nbsp;Sample Name&nbsp;", id="nbsp-around"),
    ],
)
def test_parse_header_normalisation_ignores_case_and_whitespace(column: int, variant: str) -> None:
    headers, rows = split_table()
    headers = [variant if i == column else h for i, h in enumerate(headers)]

    assert by_number(parse_samples(build_page(headers, rows))) == EXPECTED


# --- quirks of the synthetic page --------------------------------------------------------------


@pytest.fixture(scope="module")
def quirks() -> dict[int, dict[str, Any]]:
    parsed = parse_samples(quirks_bytes())
    assert parsed.source_modified_at == datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)
    return by_number(parsed)


def test_quirks_decomposed_diacritics_are_normalised_to_nfc(quirks: dict[int, dict]) -> None:
    assert quirks[1]["name"] == 'Atsá (aka "Atsah")'  # a + U+0301 -> U+00E1
    assert quirks[1]["sampling_location"] == "South Séítah"


def test_quirks_ap_date_sept_and_na_feature(quirks: dict[int, dict]) -> None:
    assert quirks[1]["sealed_on"] == "2021-09-06"
    assert quirks[1]["sol"] == 194
    assert quirks[1]["feature_name"] is None  # "n/a"


def test_quirks_nbsp_inside_a_value_becomes_a_space(quirks: dict[int, dict]) -> None:
    assert quirks[1]["current_location"] == "Perseverance Rover"
    assert quirks[1]["status"] == "on_rover"


def test_quirks_http_only_dossier_link_is_not_accepted(quirks: dict[int, dict]) -> None:
    assert quirks[1]["dossier_url"] is None


def test_quirks_unknown_status_and_type_are_null_with_the_raw_text_kept(
    quirks: dict[int, dict],
) -> None:
    row = quirks[2]

    assert row["status"] is None
    assert row["current_location"] == "Lost in Space"
    assert row["sample_type"] is None
    assert row["sample_type_label"] == "Exotic Core"


def test_quirks_javascript_link_is_skipped_and_the_first_https_link_wins(
    quirks: dict[int, dict],
) -> None:
    assert quirks[2]["dossier_url"] == "https://mirror.example.test/doc.pdf#page=2"


def test_quirks_ap_date_sep_abbreviation_and_empty_feature(quirks: dict[int, dict]) -> None:
    assert quirks[2]["sealed_on"] == "2021-09-07"
    assert quirks[2]["feature_name"] is None  # an empty cell
    assert quirks[2]["rock_type"] is None  # "n/a"


def test_quirks_estimate_without_a_pair_of_numbers_is_a_note(quirks: dict[int, dict]) -> None:
    assert quirks[2]["height_cm"] is None
    assert quirks[2]["height_note"] == "Est. 3 cm"


def test_quirks_empty_date_and_sol_are_null_and_whitespace_is_collapsed(
    quirks: dict[int, dict],
) -> None:
    assert (quirks[3]["sealed_on"], quirks[3]["sol"]) == (None, None)
    assert quirks[3]["name"] == "Spaced Out"
    assert quirks[3]["rock_type"] is None


def test_quirks_height_with_only_the_cm_number(quirks: dict[int, dict]) -> None:
    assert (quirks[3]["height_cm"], quirks[3]["height_note"]) == (3.5, None)
    assert quirks[3]["sample_type"] == "regolith"
    assert quirks[3]["status"] == "depot"


def test_quirks_full_month_name_and_sol_zero(quirks: dict[int, dict]) -> None:
    assert quirks[4]["sealed_on"] == "2022-02-03"
    assert quirks[4]["sol"] == 0


def test_quirks_data_link_is_not_a_dossier(quirks: dict[int, dict]) -> None:
    assert quirks[4]["dossier_url"] is None
    assert (quirks[4]["height_cm"], quirks[4]["height_note"]) == (None, None)


# --- dates -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        pytest.param("Jan. 5, 2022", "2022-01-05", id="jan"),
        pytest.param("Feb. 5, 2022", "2022-02-05", id="feb"),
        pytest.param("March 5, 2022", "2022-03-05", id="march"),
        pytest.param("April 5, 2022", "2022-04-05", id="april"),
        pytest.param("May 5, 2022", "2022-05-05", id="may"),
        pytest.param("June 5, 2022", "2022-06-05", id="june"),
        pytest.param("July 5, 2022", "2022-07-05", id="july"),
        pytest.param("Aug. 5, 2022", "2022-08-05", id="aug"),
        pytest.param("Sept. 5, 2022", "2022-09-05", id="sept"),
        pytest.param("Sep. 5, 2022", "2022-09-05", id="sep"),
        pytest.param("Oct. 5, 2022", "2022-10-05", id="oct"),
        pytest.param("Nov. 5, 2022", "2022-11-05", id="nov"),
        pytest.param("Dec. 5, 2022", "2022-12-05", id="dec"),
        pytest.param("January 5, 2022", "2022-01-05", id="full-january"),
        pytest.param("August 5, 2022", "2022-08-05", id="full-august"),
        pytest.param("September 5, 2022", "2022-09-05", id="full-september"),
        pytest.param("December 5, 2022", "2022-12-05", id="full-december"),
        pytest.param("Dec. 31, 2022", "2022-12-31", id="last-day"),
        pytest.param("Feb. 29, 2024", "2024-02-29", id="leap-day"),
        pytest.param("Aug. 6,&nbsp;2021", "2021-08-06", id="nbsp-before-year"),
        pytest.param("  Aug.   6, 2021 ", "2021-08-06", id="extra-spaces"),
    ],
)
def test_parse_ap_style_dates(text: str, iso: str) -> None:
    page = page_with_cell(1, COL_DATE, text)

    assert by_number(parse_samples(page))[1]["sealed_on"] == iso


@pytest.mark.parametrize("cell", ["TBD", "", "&nbsp;"], ids=["tbd", "empty", "nbsp"])
def test_parse_tbd_or_empty_sol_is_null_and_the_date_is_kept(cell: str) -> None:
    row = by_number(parse_samples(page_with_cell(8, COL_SOL, cell)))[8]

    assert row["sol"] is None
    assert row["sealed_on"] == "2022-03-07"


@pytest.mark.parametrize("cell", ["TBD", "", "&nbsp;"], ids=["tbd", "empty", "nbsp"])
def test_parse_tbd_date_with_a_known_sol_keeps_the_sol(cell: str) -> None:
    row = by_number(parse_samples(page_with_cell(8, COL_DATE, cell)))[8]

    assert row["sealed_on"] is None
    assert row["sol"] == 371


# --- height ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "height_cm", "note"),
    [
        pytest.param("5.98" + "&nbsp;" * 9 + "2.35", 5.98, None, id="cm-nbsp-inch"),
        pytest.param("6.2 2.44", 6.2, None, id="two-numbers-plain-space"),
        pytest.param("6.50&nbsp;&nbsp;2.56", 6.5, None, id="trailing-zero"),
        pytest.param("n/a &nbsp;&nbsp; n/a", None, None, id="na-na"),
        pytest.param("n/a", None, None, id="na"),
        pytest.param("", None, None, id="empty"),
        pytest.param("1.91 cm (0.75 inches)", 1.91, None, id="cm-unit-first"),
        pytest.param("3.5 cm", 3.5, None, id="cm-only"),
        pytest.param(
            "Est. 5-7 cm&nbsp;(2-2.8 in)", None, "Est. 5-7 cm (2-2.8 in)", id="estimate-range"
        ),
        pytest.param("about two fingers", None, "about two fingers", id="free-text"),
    ],
)
def test_parse_height_variants(cell: str, height_cm: float | None, note: str | None) -> None:
    row = by_number(parse_samples(page_with_cell(25, COL_HEIGHT, cell)))[25]

    assert row["height_cm"] == height_cm
    assert row["height_note"] == note


# --- enumerations ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "status"),
    [
        pytest.param("Perseverance Rover", "on_rover", id="on-rover"),
        pytest.param("Sample Depot", "depot", id="depot"),
        pytest.param("Lander", None, id="unknown"),
    ],
)
def test_parse_current_location_to_status(text: str, status: str | None) -> None:
    row = by_number(parse_samples(page_with_cell(25, COL_CURRENT, text)))[25]

    assert row["status"] == status
    assert row["current_location"] == text


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        pytest.param("Rock Core", "rock_core", id="rock-core"),
        pytest.param("Regolith", "regolith", id="regolith"),
        pytest.param("Atmospheric", "atmospheric", id="atmospheric"),
        pytest.param("Dust", None, id="unknown"),
    ],
)
def test_parse_sample_type(text: str, kind: str | None) -> None:
    row = by_number(parse_samples(page_with_cell(25, COL_TYPE, text)))[25]

    assert row["sample_type"] == kind
    assert row["sample_type_label"] == text


@pytest.mark.parametrize("cell", ["n/a", "", "&nbsp;"], ids=["na", "empty", "nbsp"])
@pytest.mark.parametrize(
    ("column", "field"),
    [(COL_FEATURE, "feature_name"), (COL_ROCK, "rock_type")],
    ids=["feature", "rock"],
)
def test_parse_na_or_empty_text_cell_is_null(column: int, field: str, cell: str) -> None:
    row = by_number(parse_samples(page_with_cell(25, column, cell)))[25]

    assert row[field] is None


# --- dossier link ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        pytest.param(
            '<a href="https://a.example.test/1.pdf">PDF</a><a href="https://b.example.test/2">x</a>',
            "https://a.example.test/1.pdf",
            id="first-https-wins",
        ),
        pytest.param('<a href="http://a.example.test/1.pdf">PDF</a>', None, id="http-only"),
        pytest.param('<a href="javascript:alert(1)">PDF</a>', None, id="javascript"),
        pytest.param('<a href="//a.example.test/1.pdf">PDF</a>', None, id="scheme-relative"),
        pytest.param('<a href="/relative.pdf">PDF</a>', None, id="relative"),
        pytest.param('<a href="mailto:a@example.test">PDF</a>', None, id="mailto"),
        pytest.param("PDF", None, id="text-without-link"),
        pytest.param("", None, id="empty"),
        pytest.param('<a href="">PDF</a>', None, id="empty-href"),
    ],
)
def test_parse_dossier_url_accepts_only_https_links(cell: str, expected: str | None) -> None:
    row = by_number(parse_samples(page_with_cell(25, COL_NOTES, cell)))[25]

    assert row["dossier_url"] == expected


# --- number limits -----------------------------------------------------------------------------


def test_parse_sample_number_32767_is_accepted() -> None:
    parsed = by_number(parse_samples(page_with_row(32767)))

    assert 32767 in parsed and len(parsed) == 8


def test_parse_sol_zero_is_accepted() -> None:
    assert by_number(parse_samples(page_with_row(31, sol="0")))[31]["sol"] == 0


# --- refusals (ADR-3) --------------------------------------------------------------------------


def renamed(header: str, new: str) -> bytes:
    return replace_in_page(f"<th>{header}</th>", f"<th>{new}</th>")


def two_tables() -> bytes:
    headers, rows = split_table()
    return build_page(
        headers,
        rows,
        extra_html="<table><thead><tr>"
        + "".join(f"<th>{h}</th>" for h in headers)
        + "</tr></thead><tbody></tbody></table>",
    )


def without_column(index: int) -> bytes:
    headers, rows = split_table()
    return build_page(
        [h for i, h in enumerate(headers) if i != index],
        [[c for i, c in enumerate(row) if i != index] for row in rows],
    )


def short_row() -> bytes:
    headers, rows = split_table()
    return build_page(headers, [rows[0][:-1], *rows[1:]])


def nested_table() -> bytes:
    return page_with_cell(1, COL_NAME, "Roubion<table><tr><td>inner</td></tr></table>")


REFUSALS: list[Any] = [
    pytest.param(short_row(), id="short-row"),
    pytest.param(nested_table(), id="nested-table"),
    pytest.param(page_with_row("9" * 5000), id="number-5000-digits"),
    pytest.param(page_with_cell(1, COL_SOL, "9" * 5000), id="sol-5000-digits"),
    pytest.param(page_with_cell(1, COL_SOL, "2147483648"), id="sol-above-int4"),
    pytest.param(renamed("Sol Sealed", "Sol"), id="header-renamed-sol"),
    pytest.param(renamed("Sample No.", "Number"), id="header-renamed-number"),
    pytest.param(renamed("Date Sealed", "Sealed"), id="header-renamed-date"),
    pytest.param(renamed("Sample Name", "Name"), id="header-renamed-name"),
    pytest.param(renamed("Feature Name", "Feature"), id="header-renamed-feature"),
    pytest.param(renamed("Sampling Location", "Location"), id="header-renamed-sampling"),
    pytest.param(renamed("Current Location", "Where"), id="header-renamed-current"),
    pytest.param(renamed("Sample Type", "Type"), id="header-renamed-type"),
    pytest.param(renamed("Rock Type", "Rock"), id="header-renamed-rock"),
    pytest.param(replace_in_page("Science Notes", "Notes"), id="header-renamed-notes"),
    pytest.param(replace_in_page("Sample Height<br>cm inch", "Height"), id="header-renamed-height"),
    pytest.param(without_column(COL_ROCK), id="header-column-dropped"),
    pytest.param(b"", id="empty-body"),
    pytest.param(b"{}", id="not-html"),
    pytest.param(b"<html><body><p>No table here</p></body></html>", id="no-table"),
    pytest.param(
        b"<html><body><table><tr><th>Name</th></tr><tr><td>x</td></tr></table></body></html>",
        id="table-without-sample-headers",
    ),
    pytest.param(two_tables(), id="two-samples-tables"),
    pytest.param(build_page(HEADERS, []), id="zero-rows"),
    pytest.param(page_with_row(8), id="duplicate-number"),
    pytest.param(page_with_row("abc"), id="number-not-a-number"),
    pytest.param(page_with_row("1.5"), id="number-decimal"),
    pytest.param(page_with_row("0"), id="number-zero"),
    pytest.param(page_with_row("-3"), id="number-negative"),
    pytest.param(page_with_row("32768"), id="number-above-smallint"),
    pytest.param(page_with_row(""), id="number-empty"),
    pytest.param(page_with_row("TBD"), id="number-tbd"),
    pytest.param(page_with_cell(1, COL_SOL, "abc"), id="sol-abc"),
    pytest.param(page_with_cell(1, COL_SOL, "-5"), id="sol-negative"),
    pytest.param(page_with_cell(1, COL_SOL, "164.5"), id="sol-decimal"),
    pytest.param(page_with_cell(1, COL_SOL, "TBA"), id="sol-other-placeholder"),
    pytest.param(page_with_cell(1, COL_DATE, "Foo 1, 2021"), id="date-unknown-month"),
    pytest.param(page_with_cell(1, COL_DATE, "Aug. 32, 2021"), id="date-day-32"),
    pytest.param(page_with_cell(1, COL_DATE, "Feb. 30, 2022"), id="date-feb-30"),
    pytest.param(page_with_cell(1, COL_DATE, "Aug. 6"), id="date-without-year"),
    pytest.param(page_with_cell(1, COL_DATE, "soon"), id="date-word"),
    pytest.param(page_with_cell(1, COL_NAME, ""), id="name-empty"),
    pytest.param(page_with_cell(1, COL_NAME, "&nbsp; "), id="name-blank"),
]


@pytest.mark.parametrize("body", REFUSALS)
def test_parse_refuses_the_page_with_a_format_error(body: bytes) -> None:
    with pytest.raises(SamplesFormatError):
        parse_samples(body)


def test_samples_format_error_is_an_upstream_error() -> None:
    assert issubclass(SamplesFormatError, UpstreamError)


def test_parse_refusal_of_one_bad_row_returns_nothing_not_the_good_rows() -> None:
    with pytest.raises(SamplesFormatError) as caught:
        parse_samples(page_with_cell(26, COL_SOL, "abc"))

    assert caught.value is not None  # all or nothing: no partial result is available


def test_parse_missing_number_is_not_a_parser_refusal() -> None:
    """A disappeared number is the sync service's rule (needs stored data), not the parser's."""
    parsed = parse_samples(page_without_number(8))

    assert [item.number for item in parsed.items] == [1, 17, 25, 26, 29, 30]


def test_parse_row_with_blank_optional_cells_is_accepted() -> None:
    row = sample_row(
        31, date="", sol="", feature="", place="", rock="", height="", kind="Rock Core"
    )
    headers, rows = split_table()

    parsed = by_number(parse_samples(build_page(headers, [*rows, row])))[31]

    assert (parsed["sealed_on"], parsed["sol"], parsed["feature_name"]) == (None, None, None)
    assert (parsed["sampling_location"], parsed["rock_type"]) == (None, None)
    assert (parsed["height_cm"], parsed["height_note"]) == (None, None)


def test_trimmed_fixture_is_a_page_with_exactly_one_table() -> None:
    assert trimmed_html().count("<table") == 1


# --- HTML5 implied end tags, script and style ---------------------------------------------------


def without_end_tags() -> bytes:
    """The same table written without </th>, </td>, </tr> (legal HTML5)."""
    headers, rows = split_table()
    head = "<tr>" + "".join(f"<th>{h}" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}" for c in row) for row in rows)
    return (
        '<html><head><meta property="article:modified_time" '
        'content="2026-06-20T12:45:46-04:00" /></head><body>'
        f"<table><thead>{head}</thead><tbody>{body}</tbody></table></body></html>"
    ).encode()


def test_parse_table_with_implied_end_tags_gives_the_same_rows() -> None:
    parsed = parse_samples(without_end_tags())

    assert by_number(parsed) == EXPECTED
    assert parsed.source_modified_at == SOURCE_MODIFIED


def test_parse_ignores_script_and_style_text_inside_a_cell() -> None:
    cell = 'Roubion<script>var leak = "x";</script><style>.a { color: red }</style>'
    row = by_number(parse_samples(page_with_cell(1, COL_NAME, cell)))[1]

    assert row["name"] == "Roubion"


@pytest.mark.parametrize("raw", ["", "n/a"], ids=["empty", "na"])
def test_parse_current_location_and_type_label_keep_the_raw_page_text(raw: str) -> None:
    where = by_number(parse_samples(page_with_cell(25, COL_CURRENT, raw)))[25]
    kind = by_number(parse_samples(page_with_cell(25, COL_TYPE, raw)))[25]

    assert (where["current_location"], where["status"]) == (raw, None)
    assert (kind["sample_type_label"], kind["sample_type"]) == (raw, None)
