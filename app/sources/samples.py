"""Perseverance sample table of the NASA "Mars Rock Samples" page, stage 13.

The page is HTML (docs/DATA-SOURCES.md, "Образцы Perseverance"). Only this module knows its
format: the single ``<table>`` is found by its headers (not by a CSS class), columns are
read by name, and the stdlib ``html.parser`` is enough for ~30 rows. A page that does not
pass the strict checks raises ``SamplesFormatError``; the caller keeps the old data.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser

from app.core.config import Settings
from app.sources.http import ConditionalBody, UpstreamClient, UpstreamError

logger = logging.getLogger(__name__)

SAMPLES_ROVER = "perseverance"

_MAX_NUMBER = 2**15 - 1
_MAX_INT4 = 2**31 - 1
_MAX_DIGITS = 10
# numeric(5, 2)
_MAX_HEIGHT = 999.99

# Normalized (lower-case) column headers the page must have.
COL_NUMBER = "sample no."
COL_DATE = "date sealed"
COL_SOL = "sol sealed"
COL_NAME = "sample name"
COL_FEATURE = "feature name"
COL_LOCATION = "sampling location"
COL_CURRENT = "current location"
COL_NOTES = "science notes"
COL_TYPE = "sample type"
COL_HEIGHT = "sample height cm inch"
COL_ROCK = "rock type"
EXPECTED_COLUMNS = (
    COL_NUMBER,
    COL_DATE,
    COL_SOL,
    COL_NAME,
    COL_FEATURE,
    COL_LOCATION,
    COL_CURRENT,
    COL_NOTES,
    COL_TYPE,
    COL_HEIGHT,
    COL_ROCK,
)

_STATUSES = {"perseverance rover": "on_rover", "sample depot": "depot"}
_SAMPLE_TYPES = {"rock core": "rock_core", "regolith": "regolith", "atmospheric": "atmospheric"}

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}  # fmt: skip

_DATE_RE = re.compile(r"([A-Za-z]+)\.? ([0-9]{1,2}), ([0-9]{4})")
_INT_RE = re.compile(r"[0-9]+")
_NUM = r"[0-9]+(?:\.[0-9]+)?"
_HEIGHT_PAIR_RE = re.compile(rf"({_NUM}) {_NUM}")
_HEIGHT_CM_RE = re.compile(rf"({_NUM}) cm(?: .*)?")
_SPACES_RE = re.compile(r"\s+")


class SamplesFormatError(UpstreamError):
    """The page does not have the expected sample table (or not parsable values in it)."""

    def __init__(self, message: str, url: str = "") -> None:
        super().__init__(url, None, message)


@dataclass(frozen=True)
class SampleRecord:
    """One table row; the fields of ``rover_samples`` without ``rover_id``."""

    number: int
    name: str
    feature_name: str | None
    sampling_location: str | None
    current_location: str | None
    status: str | None
    sample_type: str | None
    sample_type_label: str | None
    rock_type: str | None
    sealed_on: date | None
    sol: int | None
    height_cm: float | None
    height_note: str | None
    dossier_url: str | None


@dataclass
class ParsedSamples:
    items: list[SampleRecord]
    # ``article:modified_time`` of the page in UTC, None if absent or invalid.
    source_modified_at: datetime | None


# --- HTML -----------------------------------------------------------------------------------


@dataclass
class _Cell:
    text: str
    links: list[str]


def normalize(text: str) -> str:
    """NBSP to space, whitespace collapsed and stripped, Unicode NFC."""
    return unicodedata.normalize("NFC", _SPACES_RE.sub(" ", text.replace("\xa0", " "))).strip()


class _PageParser(HTMLParser):
    """Collects every table as rows of cells (text and links) and the page's modified time."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_Cell]]] = []
        self.modified: str | None = None
        self._depth = 0
        # Depth of <script>/<style> elements: their text is not cell text.
        self._skip = 0
        self._row: list[_Cell] | None = None
        self._cell: tuple[list[str], list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("property") == "article:modified_time":
            self.modified = values.get("content")
        elif tag == "table":
            self._close_row()
            self._depth += 1
            self.tables.append([])
        elif self._depth and tag == "tr":
            self._close_row()
            self._row = []
        elif self._depth and tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell = ([], [])
        elif tag in ("script", "style"):
            self._skip += 1
        elif self._cell is not None:
            if tag == "br":
                self._cell[0].append(" ")
            elif tag == "a" and values.get("href"):
                self._cell[1].append((values["href"] or "").strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table" and self._depth:
            self._close_row()
            self._depth -= 1
        elif tag in ("script", "style") and self._skip:
            self._skip -= 1

    def _close_cell(self) -> None:
        """A cell ends at its end tag or implicitly at the next cell, row or table end."""
        if self._cell is not None and self._row is not None:
            self._row.append(_Cell(normalize("".join(self._cell[0])), self._cell[1]))
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None and self.tables:
            self.tables[-1].append(self._row)
        self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None and not self._skip:
            self._cell[0].append(data)


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SamplesFormatError("page is not valid UTF-8") from exc


def _source_modified_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def _columns(header: list[_Cell]) -> dict[str, int]:
    """Column index by normalized lower-case header; every expected column must exist."""
    index: dict[str, int] = {}
    for position, cell in enumerate(header):
        index.setdefault(cell.text.lower(), position)
    missing = [name for name in EXPECTED_COLUMNS if name not in index]
    if missing:
        raise SamplesFormatError(f"table has no column(s): {', '.join(missing)}")
    extra = [name for name in index if name not in EXPECTED_COLUMNS]
    if extra:
        logger.warning("Samples table has unknown column(s): %s", ", ".join(extra))
    return index


# --- values ---------------------------------------------------------------------------------


def _text_or_none(value: str) -> str | None:
    """The text, or None for an empty cell or the page's ``n/a`` marker."""
    return None if value == "" or value.lower() == "n/a" else value


def _is_tbd(value: str) -> bool:
    return value == "" or value.upper() == "TBD"


def _is_int(value: str) -> bool:
    """Plain decimal digits, short enough for ``int()`` (no huge-number conversion)."""
    return len(value) <= _MAX_DIGITS and _INT_RE.fullmatch(value) is not None


def _parse_number(value: str) -> int:
    if not _is_int(value) or not 1 <= int(value) <= _MAX_NUMBER:
        raise SamplesFormatError(f"invalid sample number: {value[:32]!r}")
    return int(value)


def _parse_sol(value: str) -> int | None:
    if _is_tbd(value):
        return None
    if not _is_int(value) or int(value) > _MAX_INT4:
        raise SamplesFormatError(f"invalid sol: {value[:32]!r}")
    return int(value)


def _parse_date(value: str) -> date | None:
    if _is_tbd(value):
        return None
    match = _DATE_RE.fullmatch(value)
    month = _MONTHS.get(match.group(1).lower()) if match else None
    if match is None or month is None:
        raise SamplesFormatError(f"invalid date: {value[:32]!r}")
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError as exc:
        raise SamplesFormatError(f"invalid date: {value[:32]!r}") from exc


def _parse_height(value: str) -> tuple[float | None, str | None]:
    """``(height_cm, height_note)``: a number if the text starts with one, else the text."""
    if value == "" or value.lower() in ("n/a", "n/a n/a"):
        return None, None
    match = _HEIGHT_PAIR_RE.fullmatch(value) or _HEIGHT_CM_RE.fullmatch(value)
    if match is not None and float(match.group(1)) <= _MAX_HEIGHT:
        return float(match.group(1)), None
    return None, value


def _dossier_url(links: list[str]) -> str | None:
    """The first https link of the Science Notes cell (others, e.g. javascript:, are ignored)."""
    for link in links:
        if link.lower().startswith("https://") and not _SPACES_RE.search(link):
            return link
    return None


def _mapped(value: str, mapping: dict[str, str], what: str, number: int) -> str | None:
    result = mapping.get(value.lower())
    if result is None and value:
        logger.warning("Sample %d has an unknown %s: %r", number, what, value[:64])
    return result


def _record(cells: list[_Cell], index: dict[str, int]) -> SampleRecord:
    def cell(name: str) -> _Cell:
        position = index[name]
        if position >= len(cells):
            raise SamplesFormatError("table row has too few cells")
        return cells[position]

    def text(name: str) -> str:
        return cell(name).text

    number = _parse_number(text(COL_NUMBER))
    name = text(COL_NAME)
    if not name:
        raise SamplesFormatError(f"sample {number} has no name")
    height_cm, height_note = _parse_height(text(COL_HEIGHT))
    type_label = text(COL_TYPE)
    current = text(COL_CURRENT)
    return SampleRecord(
        number=number,
        name=name,
        feature_name=_text_or_none(text(COL_FEATURE)),
        sampling_location=_text_or_none(text(COL_LOCATION)),
        current_location=current,
        status=_mapped(current, _STATUSES, "current location", number),
        sample_type=_mapped(type_label, _SAMPLE_TYPES, "sample type", number),
        sample_type_label=type_label,
        rock_type=_text_or_none(text(COL_ROCK)),
        sealed_on=_parse_date(text(COL_DATE)),
        sol=_parse_sol(text(COL_SOL)),
        height_cm=height_cm,
        height_note=height_note,
        dossier_url=_dossier_url(cell(COL_NOTES).links),
    )


def parse_samples(body: bytes) -> ParsedSamples:
    """Rows of the sample table by number; ``SamplesFormatError`` if the page fails the checks."""
    parser = _PageParser()
    parser.feed(_decode(body))
    parser.close()
    if len(parser.tables) != 1:
        raise SamplesFormatError(f"expected one table, found {len(parser.tables)}")
    header, *rows = parser.tables[0] or [[]]
    index = _columns(header)
    items: dict[int, SampleRecord] = {}
    for cells in rows:
        if not any(cell.text for cell in cells):
            continue
        record = _record(cells, index)
        if record.number in items:
            raise SamplesFormatError(f"sample number {record.number} is repeated")
        items[record.number] = record
    if not items:
        raise SamplesFormatError("table has no rows")
    return ParsedSamples(
        sorted(items.values(), key=lambda item: item.number),
        _source_modified_at(parser.modified),
    )


# --- download -------------------------------------------------------------------------------


class SamplesAdapter:
    """Downloads the samples page with a conditional request (it sends no validators today)."""

    def __init__(self, client: UpstreamClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def fetch(
        self, *, etag: str | None = None, last_modified: str | None = None
    ) -> ConditionalBody:
        """The page, or ``not_modified`` if the validators still match; only the configured
        host is accepted (also after redirects), the body is capped at SAMPLES_MAX_BYTES.
        """
        return await self._client.get_conditional(
            self._settings.samples_url,
            max_bytes=self._settings.samples_max_bytes,
            allowed_origin=self._settings.samples_url,
            etag=etag,
            last_modified=last_modified,
        )
