"""Spirit and Opportunity (MER) adapter over the cached PDS Imaging Node EDR index files.

The adapter never goes to the network: it reads ``MER_CACHE_DIR/<volume>/index/edrindex.lbl``
and ``edrindex.tab`` downloaded once by ``python -m app.cli mer-download``. Format facts
(docs/DATA-SOURCES.md, "Spirit и Opportunity"):

- rows are CRLF-terminated, of variable width, fields separated by tabs; the label's
  ``ROW_BYTES``/``START_BYTE`` do not match the file, so rows are split on tabs only;
- a label column with ``ITEMS = n`` (``ROVER_MOTION_COUNTER``) is ``n`` tab-separated fields;
- the Descent volume (``do``) has no ``PATH_NAME``/``FILE_NAME``: paths are built from the
  product id;
- rows are not sorted by sol; a few Opportunity product ids appear twice (re-releases).

One photo is stored per frame (ADR-5): products of a frame share ``frame_key``; the best one
(``EFF`` > ``ESF`` > ``EDN``, then version, then product id, then the latest creation time)
gives the ``source_id``, all of them are listed in ``extra.products``.
"""

import asyncio
import logging
import sys
from collections import Counter, defaultdict
from collections.abc import AsyncGenerator, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from app.core.config import Settings
from app.sources.base import (
    FetchStats,
    PhotoRecord,
    SolStats,
    SolStream,
    build_record,
    validate_sol_range,
)
from app.sources.http import UpstreamError
from app.sources.normalize import compact, parse_datetime_utc

logger = logging.getLogger(__name__)

# MER-1 is Opportunity, MER-2 is Spirit.
ROVER_PREFIXES: dict[str, str] = {"opportunity": "mer1", "spirit": "mer2"}
# Navcam, Hazcam, Pancam, Microscopic Imager, Descent. The mw/om volumes are RDR mosaics.
VOLUME_KINDS: tuple[str, ...] = ("no", "ho", "po", "mo", "do")
INDEX_DIR = "index"
LABEL_FILE = "edrindex.lbl"
TABLE_FILE = "edrindex.tab"

SAMPLE_TYPES: dict[str, str] = {"EFF": "full", "ESF": "subframe", "EDN": "downsampled"}
_TYPE_RANK: dict[str, int] = {"EFF": 2, "ESF": 1, "EDN": 0}
# Thumbnails and engineering products: counted as dropped, never stored.
SKIPPED_TYPES = frozenset({"ETH", "ERP", "EHG", "ERS", "ECS"})

INSTRUMENT_CAMERAS: dict[str, str] = {
    "NAVCAM_LEFT": "NAVCAM",
    "NAVCAM_RIGHT": "NAVCAM",
    "FRONT_HAZCAM_LEFT": "FHAZ",
    "FRONT_HAZCAM_RIGHT": "FHAZ",
    "REAR_HAZCAM_LEFT": "RHAZ",
    "REAR_HAZCAM_RIGHT": "RHAZ",
    "PANCAM_LEFT": "PANCAM",
    "PANCAM_RIGHT": "PANCAM",
    "MI": "MI",
    "DESCAM": "ENTRY",
}
DEFAULT_CREDIT = "NASA/JPL-Caltech"
CAMERA_CREDITS: dict[str, str] = {
    "PANCAM": "NASA/JPL-Caltech/Cornell",
    "MI": "NASA/JPL-Caltech/USGS",
}

_MOTION_COUNTER = "ROVER_MOTION_COUNTER"
_PRODUCT_ID_LENGTH = 27


class MerCacheMissing(UpstreamError):
    """Index files of the rover are not in the local cache (run ``mer-download``)."""

    def __init__(self, missing: list[Path]) -> None:
        names = ", ".join(str(path) for path in missing[:3])
        more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
        super().__init__(
            "file://" + (names or "?"),
            None,
            f"MER index files missing: {names}{more}; run `python -m app.cli mer-download`",
        )
        self.missing = missing


def volume_ids(rover_id: str) -> list[str]:
    """EDR volume ids of a MER rover, e.g. ``mer2no_0xxx``; ``ValueError`` for other rovers."""
    prefix = ROVER_PREFIXES.get(rover_id.lower())
    if prefix is None:
        raise ValueError(f"Rover {rover_id[:64]} is not a MER rover (spirit, opportunity)")
    return [f"{prefix}{kind}_0xxx" for kind in VOLUME_KINDS]


def index_paths(cache_dir: Path, volume: str) -> tuple[Path, Path]:
    """``(label, table)`` paths of a volume's index in the cache."""
    directory = cache_dir / volume / INDEX_DIR
    return directory / LABEL_FILE, directory / TABLE_FILE


# --- PDS3 label -------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexLabel:
    """What the parser needs from ``edrindex.lbl``.

    ``fields`` maps a column name to ``(first field index, items)`` in a tab-split row,
    with multi-item columns expanded; ``field_count`` is the expected fields per row.
    """

    rows: int
    fields: dict[str, tuple[int, int]]
    field_count: int

    def index(self, name: str) -> int | None:
        position = self.fields.get(name)
        return position[0] if position is not None else None


def _label_statements(text: str) -> Iterator[tuple[str, str]]:
    """``(KEY, value)`` pairs of a PDS3 label; multi-line quoted values are joined."""
    lines = iter(text.splitlines())
    for line in lines:
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if value.startswith('"'):
            while value.count('"') < 2:
                nxt = next(lines, None)
                if nxt is None:
                    break
                value += " " + nxt.strip()
        yield key.strip(), value.strip().strip('"').strip()


def parse_label(text: str) -> IndexLabel:
    """Parse ``ROWS`` and the ``COLUMN`` objects (``NAME``, ``ITEMS``) of the index table.

    ``ROW_BYTES``, ``START_BYTE`` and ``BYTES`` are ignored: they do not match the file.
    Raises ``ValueError`` if the label has no table, no ``ROWS`` or a wrong column count.
    """
    depth_names: list[str] = []
    rows: int | None = None
    declared_columns: int | None = None
    columns: list[tuple[str, int]] = []
    current: dict[str, str] | None = None
    for key, value in _label_statements(text):
        if key == "OBJECT":
            depth_names.append(value)
            if value == "COLUMN":
                current = {}
        elif key == "END_OBJECT":
            ended = depth_names.pop() if depth_names else None
            if ended == "COLUMN" and current is not None:
                columns.append((current.get("NAME", ""), int(current.get("ITEMS", "1"))))
                current = None
        elif current is not None:
            current[key] = value
        elif depth_names and depth_names[-1].endswith("TABLE"):
            if key == "ROWS":
                rows = int(value)
            elif key == "COLUMNS":
                declared_columns = int(value)
    if rows is None or not columns:
        raise ValueError("label has no index table with ROWS and COLUMN objects")
    if declared_columns is not None and declared_columns != len(columns):
        raise ValueError(f"label declares {declared_columns} columns, has {len(columns)}")
    fields: dict[str, tuple[int, int]] = {}
    position = 0
    for name, items in columns:
        fields.setdefault(name, (position, items))
        position += items
    return IndexLabel(rows=rows, fields=fields, field_count=position)


# --- index rows -------------------------------------------------------------------------


def split_row(line: str) -> list[str]:
    """Tab-separated fields of a row with padding and double quotes removed."""
    return [value.strip().strip('"').strip() for value in line.rstrip("\r\n").split("\t")]


def product_type(product_id: str) -> str:
    """Product type: characters 12-14 of the product id (``EFF``, ``EDN``, ``ETH``...)."""
    return product_id[11:14]


def frame_key(product_id: str) -> str:
    """Product id without type, creator and version: equal for products of one frame."""
    return product_id[:11] + product_id[14:25]


class IndexRow(NamedTuple):
    """The columns of one image product the import needs (strings as in the index)."""

    volume_id: str
    product_id: str
    sol: int
    instrument_id: str
    path_name: str | None
    file_name: str | None
    creation_time: str
    start_time: str | None
    received_time: str | None
    sclk: str | None
    sequence_id: str | None
    local_true_solar_time: str | None
    lines: int | None
    line_samples: int | None
    frame_type: str | None
    filter_name: str | None
    motion_counter: tuple[int, ...] | None


def pick_product(rows: Iterable[IndexRow]) -> IndexRow:
    """Best product of a frame: type, then version, then product id, then latest creation."""
    return max(
        rows,
        key=lambda row: (
            _TYPE_RANK.get(product_type(row.product_id), -1),
            row.product_id[-1:],
            row.product_id,
            row.creation_time,
        ),
    )


def product_path(row: IndexRow) -> tuple[str, str]:
    """``(directory relative to the archive root, file name)`` of the ``.img`` product."""
    if row.path_name and row.file_name:
        return row.path_name.strip("/"), row.file_name
    # Descent volumes have no PATH_NAME/FILE_NAME (docs/DATA-SOURCES.md).
    return f"{row.volume_id}/data/sol{row.sol:04d}/edr", row.product_id.lower() + ".img"


def product_url(base_url: str, row: IndexRow) -> str:
    """URL of the raw PDS ``.img`` product."""
    directory, name = product_path(row)
    return f"{base_url.rstrip('/')}/{directory}/{name}"


def browse_url(base_url: str, row: IndexRow) -> str:
    """URL of the browse JPEG: ``/data/`` -> ``/browse/``, file name + ``.jpg``."""
    directory, name = product_path(row)
    browse_dir = directory.replace("/data/", "/browse/", 1)
    return f"{base_url.rstrip('/')}/{browse_dir}/{name}.jpg"


def camera_credit(camera_code: str) -> str:
    return CAMERA_CREDITS.get(camera_code, DEFAULT_CREDIT)


def _eye(instrument_id: str) -> str | None:
    if instrument_id.endswith("_LEFT"):
        return "L"
    if instrument_id.endswith("_RIGHT"):
        return "R"
    return None


def to_record(row: IndexRow, products: list[str], base_url: str) -> PhotoRecord | None:
    """Photo of a frame from its chosen product; None (with a warning) if incomplete."""
    camera = INSTRUMENT_CAMERAS[row.instrument_id]
    kind = product_type(row.product_id)
    motion = row.motion_counter
    fields: dict[str, Any] = {
        "source_id": row.product_id,
        "sol": row.sol,
        "camera_code": camera,
        "taken_at": parse_datetime_utc(row.start_time, field="START_TIME"),
        "sample_type": SAMPLE_TYPES[kind],
        "img_full": browse_url(base_url, row),
        "received_at": parse_datetime_utc(row.received_time, field="EARTH_RECEIVED_START_TIME"),
        "credit": camera_credit(camera),
        "source_link": product_url(base_url, row),
        "site": motion[0] if motion else None,
        "drive": motion[1] if motion else None,
        "extra": compact(
            {
                "instrument_id": row.instrument_id,
                "eye": _eye(row.instrument_id),
                "filter_name": row.filter_name,
                "product_type": kind,
                "products": products,
                "volume_id": row.volume_id,
                "sequence_id": row.sequence_id,
                "sclk": row.sclk,
                "local_true_solar_time": row.local_true_solar_time,
                "lines": row.lines,
                "line_samples": row.line_samples,
                "frame_type": row.frame_type,
                "rover_motion_counter": list(motion) if motion else None,
            }
        ),
    }
    return build_record(fields, label=f"{row.volume_id}:{row.product_id}")


def sol_records(rows: Iterable[IndexRow], base_url: str) -> list[PhotoRecord]:
    """One record per frame (frames never cross volumes), ordered by product id."""
    frames: dict[tuple[str, str], list[IndexRow]] = defaultdict(list)
    for row in rows:
        frames[(row.volume_id, frame_key(row.product_id))].append(row)
    records: list[PhotoRecord] = []
    for group in frames.values():
        products = sorted({row.product_id for row in group})
        record = to_record(pick_product(group), products, base_url)
        if record is not None:
            records.append(record)
    records.sort(key=lambda record: record.source_id)
    return records


# --- whole index ------------------------------------------------------------------------


@dataclass
class RoverIndex:
    """Image rows of one rover grouped by sol, plus what was read and skipped.

    ``received`` counts every index row of a sol (thumbnails and engineering products too);
    ``unattributed`` counts rows without a usable sol or with a wrong field count.
    """

    rows: dict[int, list[IndexRow]] = field(default_factory=lambda: defaultdict(list))
    received: Counter[int] = field(default_factory=Counter)
    declared_rows: int = 0
    read_rows: int = 0
    unattributed: int = 0
    skipped_types: Counter[str] = field(default_factory=Counter)
    unknown_types: Counter[str] = field(default_factory=Counter)
    unknown_instruments: Counter[str] = field(default_factory=Counter)


def missing_files(cache_dir: Path, rover_id: str) -> list[Path]:
    return [
        path
        for volume in volume_ids(rover_id)
        for path in index_paths(cache_dir, volume)
        if not path.is_file()
    ]


def declared_rows(cache_dir: Path, rover_id: str) -> int:
    """Sum of ``ROWS`` of the rover's index labels; ``MerCacheMissing`` if a file is absent."""
    missing = missing_files(cache_dir, rover_id)
    if missing:
        raise MerCacheMissing(missing)
    return sum(
        _read_label(index_paths(cache_dir, volume)[0]).rows for volume in volume_ids(rover_id)
    )


def load_rover_index(cache_dir: Path, rover_id: str) -> RoverIndex:
    """Read every index volume of the rover (blocking; a few seconds per volume)."""
    missing = missing_files(cache_dir, rover_id)
    if missing:
        raise MerCacheMissing(missing)
    index = RoverIndex()
    for volume in volume_ids(rover_id):
        label_path, table_path = index_paths(cache_dir, volume)
        label = _read_label(label_path)
        index.declared_rows += label.rows
        read = _read_table(table_path, label, volume, index)
        if read != label.rows:
            logger.warning("MER index %s: %d rows, label declares %d", volume, read, label.rows)
    _log_skipped(rover_id, index)
    return index


def _read_label(path: Path) -> IndexLabel:
    try:
        return parse_label(path.read_text(encoding="latin-1"))
    except ValueError as exc:
        raise UpstreamError(f"file://{path}", None, f"bad PDS label: {exc}") from exc


class _Columns(NamedTuple):
    product_id: int
    sol: int
    instrument: int
    creation: int
    path: int | None
    file: int | None
    start: int | None
    received: int | None
    sclk: int | None
    sequence: int | None
    lts: int | None
    lines: int | None
    samples: int | None
    frame_type: int | None
    filter_name: int | None
    motion: tuple[int, int] | None


def _columns(label: IndexLabel, volume: str) -> _Columns:
    required = ("PRODUCT_ID", "PLANET_DAY_NUMBER", "INSTRUMENT_ID", "PRODUCT_CREATION_TIME")
    missing = [name for name in required if label.index(name) is None]
    if missing:
        raise UpstreamError(f"file://{volume}", None, f"index has no {', '.join(missing)}")
    return _Columns(
        product_id=label.fields["PRODUCT_ID"][0],
        sol=label.fields["PLANET_DAY_NUMBER"][0],
        instrument=label.fields["INSTRUMENT_ID"][0],
        creation=label.fields["PRODUCT_CREATION_TIME"][0],
        path=label.index("PATH_NAME"),
        file=label.index("FILE_NAME"),
        start=label.index("START_TIME"),
        received=label.index("EARTH_RECEIVED_START_TIME"),
        sclk=label.index("SPACECRAFT_CLOCK_START_COUNT"),
        sequence=label.index("SEQUENCE_ID"),
        lts=label.index("LOCAL_TRUE_SOLAR_TIME"),
        lines=label.index("LINES"),
        samples=label.index("LINE_SAMPLES"),
        frame_type=label.index("FRAME_TYPE"),
        filter_name=label.index("FILTER_NAME"),
        motion=label.fields.get(_MOTION_COUNTER),
    )


def _read_table(path: Path, label: IndexLabel, volume: str, index: RoverIndex) -> int:
    columns = _columns(label, volume)
    read = 0
    with path.open(encoding="latin-1", newline="") as file:
        for line in file:
            if not line.strip():
                continue
            read += 1
            _add_row(split_row(line), label, columns, volume, index)
    index.read_rows += read
    return read


def _add_row(
    values: list[str], label: IndexLabel, columns: _Columns, volume: str, index: RoverIndex
) -> None:
    sol = _int(values[columns.sol]) if len(values) == label.field_count else None
    if sol is None:
        index.unattributed += 1
        return
    index.received[sol] += 1
    product_id = values[columns.product_id]
    kind = product_type(product_id)
    if kind not in SAMPLE_TYPES:
        counter = index.skipped_types if kind in SKIPPED_TYPES else index.unknown_types
        counter[kind] += 1
        return
    instrument = values[columns.instrument]
    if instrument not in INSTRUMENT_CAMERAS or len(product_id) != _PRODUCT_ID_LENGTH:
        index.unknown_instruments[instrument] += 1
        return
    index.rows[sol].append(_index_row(values, columns, volume, sol, product_id, instrument))


def _index_row(
    values: list[str], columns: _Columns, volume: str, sol: int, product_id: str, instrument: str
) -> IndexRow:
    def text(position: int | None, *, intern: bool = False) -> str | None:
        if position is None or not values[position]:
            return None
        return sys.intern(values[position]) if intern else values[position]

    return IndexRow(
        volume_id=sys.intern(volume),
        product_id=product_id,
        sol=sol,
        instrument_id=sys.intern(instrument),
        path_name=text(columns.path, intern=True),
        file_name=text(columns.file),
        creation_time=values[columns.creation],
        start_time=text(columns.start),
        received_time=text(columns.received),
        sclk=text(columns.sclk),
        sequence_id=text(columns.sequence, intern=True),
        local_true_solar_time=text(columns.lts, intern=True),
        lines=_int(values[columns.lines]) if columns.lines is not None else None,
        line_samples=_int(values[columns.samples]) if columns.samples is not None else None,
        frame_type=text(columns.frame_type, intern=True),
        filter_name=text(columns.filter_name, intern=True),
        motion_counter=_motion(values, columns.motion),
    )


def _motion(values: list[str], position: tuple[int, int] | None) -> tuple[int, ...] | None:
    if position is None:
        return None
    start, items = position
    numbers = [_int(value) for value in values[start : start + items]]
    if any(number is None for number in numbers):
        return None
    return tuple(number for number in numbers if number is not None)


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _log_skipped(rover_id: str, index: RoverIndex) -> None:
    for kind, count in sorted(index.unknown_types.items()):
        logger.warning("MER %s: skipped %d rows of unknown product type %r", rover_id, count, kind)
    for name, count in sorted(index.unknown_instruments.items()):
        logger.warning("MER %s: skipped %d rows of unknown instrument %r", rover_id, count, name)
    if index.unattributed:
        logger.warning("MER %s: skipped %d unparsable index rows", rover_id, index.unattributed)
    logger.info(
        "MER index loaded rover=%s rows=%d declared=%d image_rows=%d sols=%d",
        rover_id,
        index.read_rows,
        index.declared_rows,
        sum(len(rows) for rows in index.rows.values()),
        len(index.received),
    )


# --- adapter ----------------------------------------------------------------------------


class MerAdapter:
    """``SourceAdapter`` of Spirit or Opportunity over the local index cache.

    The index is read once, on first use, and kept for the life of the adapter (one read
    per command, not per batch). Per sol: ``received`` = every index row of the sol,
    ``dropped`` = received - delivered, ``source_total`` = None (the archive has none).
    """

    def __init__(self, rover_id: str, settings: Settings) -> None:
        volume_ids(rover_id)  # validates the rover
        self.rover_id = rover_id.lower()
        self._cache_dir = Path(settings.mer_cache_dir)
        self._base_url = settings.mer_pds_base_url
        self._index: RoverIndex | None = None
        self._lock = asyncio.Lock()

    def missing_files(self) -> list[Path]:
        """Index files of the rover absent from the cache."""
        return missing_files(self._cache_dir, self.rover_id)

    async def latest_sol(self) -> int:
        index = await self.load()
        if not index.received:
            raise UpstreamError(f"file://{self._cache_dir}", None, "MER index has no rows")
        return max(index.received)

    async def source_total(self) -> int | None:
        """Sum of ``ROWS`` declared by the index labels (all products, thumbnails included)."""
        return await asyncio.to_thread(declared_rows, self._cache_dir, self.rover_id)

    async def load(self) -> RoverIndex:
        async with self._lock:
            if self._index is None:
                self._index = await asyncio.to_thread(
                    load_rover_index, self._cache_dir, self.rover_id
                )
            return self._index

    def fetch_sols(self, from_sol: int, to_sol: int) -> SolStream:
        stats = FetchStats()
        return SolStream(self._fetch_sols(from_sol, to_sol, stats), stats)

    async def _fetch_sols(
        self, from_sol: int, to_sol: int, stats: FetchStats
    ) -> AsyncGenerator[tuple[int, list[PhotoRecord]]]:
        validate_sol_range(from_sol, to_sol)
        index = await self.load()
        sols = sorted((sol for sol in index.received if from_sol <= sol <= to_sol), reverse=True)
        for sol in sols:
            records = sol_records(index.rows.get(sol, ()), self._base_url)
            received = index.received[sol]
            stats.sols[sol] = SolStats(received=received, dropped=received - len(records))
            if records:
                yield sol, records
        stats.finish(from_sol, to_sol)
