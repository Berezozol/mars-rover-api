"""Readers of the local copy of the weather archive: REMS (Curiosity) and MEDA (Perseverance).

The only module that knows the formats (stage 17, docs/DATA-SOURCES.md "Погода: разбор локальных
файлов"). It reads files that ``scripts/weather_download.py`` stored under ``WEATHER_RAW_DIR``;
it never calls the network. Every ``iter_*`` function streams one file (the file is never held
in memory) and yields ``WeatherSample`` rows with the local mean solar time of the row in
seconds, the parameter and the value in SI-like units of the store: K, Pa, %, W/m2, m/s, degrees.

Rules shared by all readers:

- the sol of a row is the first five digits of its ``LMST``; a row of another sol is skipped and
  counted (``foreign_sol_rows``); a file where over half of the rows are foreign is broken;
- the source's gaps (``999999999`` and an empty field in MEDA, ``-999.00`` in REMS) are dropped
  without a count; a value outside the physical bounds is dropped and counted in
  ``out_of_range`` (checked before the confidence masks of the source);
- humidity between 100 and the upper bound is kept and counted in ``humidity_over_100``;
- a malformed row raises ``WeatherFormatError`` with the line number.
"""

import math
import os
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

# Parameter names of the samples (also the keys of ``RawCounters.out_of_range``).
AIR = "air_temp"
PRESSURE = "pressure"
HUMIDITY = "humidity"
GROUND = "ground_temp"
UV = "uv"
WIND_SPEED = "wind_speed"
WIND_DIR = "wind_dir"

# Directory of each rover's files under the raw directory.
MISSION_DIRS = {"perseverance": "meda", "curiosity": "rems"}
MEDA_COLLECTIONS = ("data_calibrated_env", "data_derived_env")
MEDA_PRODUCTS = ("CAL_ATS", "CAL_WS", "DER_PS", "DER_RHS", "DER_TIRS")
REMS_PRODUCT = "RMD"

_MEDA_FILL = 999_999_999.0
# Stefan-Boltzmann constant (W m-2 K-4): MEDA's DER_TIRS upward flux is sigma * T^4.
_STEFAN_BOLTZMANN = 5.670374419e-8
_REMS_FILL = -999.0
_SOL_SECONDS_MAX = 86_399.999
_FOREIGN_SHARE = 0.5
_LABEL_BYTES = 64 * 1024

_MEDA_FILE = re.compile(
    r"WE__(\d{4,5})_+(CAL_ATS|CAL_WS|DER_PS|DER_RHS|DER_TIRS)_+(P[0-9A-Z]{1,3})\.CSV", re.IGNORECASE
)
_REMS_FILE = re.compile(r"RME_\d+RMD\d+_+(P[0-9A-Z]{1,3})\.TAB", re.IGNORECASE)
_MEDA_RANGE_DIR = re.compile(r"sol_\d{4,5}_\d{4,5}")
_MEDA_SOL_DIR = re.compile(r"sol_(\d{4,5})")
_REMS_RANGE_DIR = re.compile(r"SOL_\d{5}_\d{5}")
_REMS_SOL_DIR = re.compile(r"SOL(\d{5})")
_START_TIME = re.compile(r"<(?:[\w-]+:)?start_date_time>\s*([^<\s]+)\s*<")
_START_LS = re.compile(r"<(?:[\w-]+:)?start_solar_longitude[^>]*>\s*([-+0-9.eE]+)\s*<")
_LBL_START = re.compile(r"^START_TIME\s*=\s*(\S+)", re.MULTILINE)
_VERSION = re.compile(r"P(\d+)")
_VERSION_LETTERS = re.compile(r"P([A-Z]+)")


class WeatherFormatError(Exception):
    """A file does not look like the product it is named after."""


class WeatherSample(NamedTuple):
    lmst_s: float
    param: str
    value: float


@dataclass(frozen=True)
class PhysicalBounds:
    """Values outside these bounds (inclusive) are instrument faults, not weather."""

    air: tuple[float, float] = (150.0, 300.0)
    ground: tuple[float, float] = (120.0, 330.0)
    pressure: tuple[float, float] = (300.0, 1200.0)
    humidity: tuple[float, float] = (0.0, 200.0)
    uv: tuple[float, float] = (0.0, 60.0)
    wind_speed: tuple[float, float] = (0.0, 60.0)
    wind_dir: tuple[float, float] = (0.0, 360.0)

    def with_humidity_max(self, humidity_max: float) -> "PhysicalBounds":
        return PhysicalBounds(
            self.air,
            self.ground,
            self.pressure,
            (0.0, humidity_max),
            self.uv,
            self.wind_speed,
            self.wind_dir,
        )


DEFAULT_BOUNDS = PhysicalBounds()


@dataclass
class RawCounters:
    """What one file (or the files of a sol, merged) told besides its values."""

    out_of_range: Counter[str] = field(default_factory=Counter)
    humidity_over_100: int = 0
    foreign_sol_rows: int = 0
    rows_total: int = 0
    rows_used: int = 0

    def merge(self, other: "RawCounters") -> None:
        self.out_of_range.update(other.out_of_range)
        self.humidity_over_100 += other.humidity_over_100
        self.foreign_sol_rows += other.foreign_sol_rows
        self.rows_total += other.rows_total
        self.rows_used += other.rows_used


@dataclass(frozen=True)
class LabelInfo:
    """What a product label tells: the start of the data (UTC) and, for MEDA, its Ls."""

    start_time: datetime | None
    ls: float | None


@dataclass(frozen=True)
class RawFile:
    """A product file found in the raw directory (``path`` is relative to the mission root)."""

    rover: str
    path: str
    sol: int
    product: str
    version: str
    size: int
    mtime_ns: int
    full_path: Path | None = None


# --- time and numbers ----------------------------------------------------------------------


def parse_lmst(text: str) -> tuple[int, float]:
    """``00136M00:00:00.471`` -> (136, 0.471): the sol and the seconds since local midnight."""
    text = text.strip().strip('"').strip()
    if len(text) < 13 or text[5] != "M" or text[8] != ":" or text[11] != ":":
        raise ValueError(f"not an LMST: {text[:30]!r}")
    hours, minutes, seconds = int(text[6:8]), int(text[9:11]), float(text[12:])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds < 61):
        raise ValueError(f"not an LMST: {text[:30]!r}")
    return int(text[:5]), min(hours * 3600 + minutes * 60 + seconds, _SOL_SECONDS_MAX)


def _meda_number(text: str) -> float | None:
    """A MEDA value; the gap marker, an empty field and a non-finite number are ``None``."""
    text = text.strip()
    if not text:
        return None
    value = float(text)
    return value if math.isfinite(value) and value != _MEDA_FILL else None


def _rems_number(text: str) -> float | None:
    value = float(text)
    return value if math.isfinite(value) and value != _REMS_FILL else None


def version_key(version: str) -> tuple[int, int, str]:
    """Order of product versions: ``P02`` > ``P01`` > ``P1`` ... a letter version (``PA``, the
    first REMS release of the early sols) is older than every numbered one."""
    text = version.strip().upper()
    if match := _VERSION.fullmatch(text):
        return 1, int(match.group(1)), ""
    if match := _VERSION_LETTERS.fullmatch(text):
        return 0, 0, match.group(1)
    return -1, 0, text


# --- labels --------------------------------------------------------------------------------


def _read_head(path: Path) -> str | None:
    try:
        if path.is_symlink() or path.is_junction():
            return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_LABEL_BYTES)
    except OSError:
        return None


def _parse_ls(text: str) -> float | None:
    """Ls of a label, only a value to compare with; text that is no number gives None."""
    try:
        return float(text)
    except ValueError:
        return None


def _parse_moment(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _label_info(path: Path) -> LabelInfo:
    """Read the ``.xml`` label next to a data file (or the label itself)."""
    label = path if path.suffix.lower() == ".xml" else path.with_suffix(".xml")
    head = _read_head(label)
    start = _START_TIME.search(head) if head else None
    ls = _START_LS.search(head) if head else None
    return LabelInfo(
        _parse_moment(start.group(1)) if start else None,
        _parse_ls(ls.group(1)) if ls else None,
    )


def read_meda_label(path: Path) -> LabelInfo:
    """Start time and Ls of a MEDA product (``path``: the CSV or its ``.xml``); missing label:
    empty info."""
    return _label_info(path)


def read_rems_label(path: Path) -> LabelInfo:
    """Start time of a REMS product (``path``: the TAB, ``.LBL`` or ``.xml``); REMS labels have
    no Ls. The PDS4 ``.xml`` is preferred, the PDS3 ``.LBL`` is the fallback."""
    info = _label_info(path)
    if info.start_time is not None:
        return info
    head = _read_head(path.with_suffix(".LBL"))
    match = _LBL_START.search(head) if head else None
    return LabelInfo(_parse_moment(match.group(1)) if match else None, None)


# --- scanning ------------------------------------------------------------------------------


def _subdirs(path: Path, pattern: re.Pattern[str]) -> Iterator[os.DirEntry[str]]:
    """Subdirectories whose names match; links are not followed (nothing outside the tree)."""
    try:
        with os.scandir(path) as entries:
            found = [
                entry
                for entry in entries
                if pattern.fullmatch(entry.name)
                and entry.is_dir(follow_symlinks=False)
                and not entry.is_junction()
            ]
    except OSError:
        return
    yield from sorted(found, key=lambda entry: entry.name)


def _files(path: Path) -> Iterator[os.DirEntry[str]]:
    try:
        with os.scandir(path) as entries:
            found = [entry for entry in entries if entry.is_file(follow_symlinks=False)]
    except OSError:
        return
    yield from sorted(found, key=lambda entry: entry.name)


def _raw_file(
    rover: str, root: Path, entry: os.DirEntry[str], sol: int, product: str, version: str
) -> RawFile | None:
    try:
        stat = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    full = Path(entry.path)
    return RawFile(
        rover,
        full.relative_to(root).as_posix(),
        sol,
        product,
        version.upper(),
        stat.st_size,
        stat.st_mtime_ns,
        full,
    )


def _scan_meda(root: Path) -> Iterator[RawFile]:
    for collection in MEDA_COLLECTIONS:
        for span in _subdirs(root / collection, _MEDA_RANGE_DIR):
            for sol_dir in _subdirs(Path(span.path), _MEDA_SOL_DIR):
                sol = int(_MEDA_SOL_DIR.fullmatch(sol_dir.name).group(1))  # type: ignore[union-attr]
                for entry in _files(Path(sol_dir.path)):
                    match = _MEDA_FILE.fullmatch(entry.name)
                    if match is None or int(match.group(1)) != sol:
                        continue
                    found = _raw_file(
                        "perseverance", root, entry, sol, match.group(2).upper(), match.group(3)
                    )
                    if found is not None:
                        yield found


def _scan_rems(root: Path) -> Iterator[RawFile]:
    for span in _subdirs(root / "DATA", _REMS_RANGE_DIR):
        for sol_dir in _subdirs(Path(span.path), _REMS_SOL_DIR):
            sol = int(_REMS_SOL_DIR.fullmatch(sol_dir.name).group(1))  # type: ignore[union-attr]
            for entry in _files(Path(sol_dir.path)):
                match = _REMS_FILE.fullmatch(entry.name)
                if match is None:
                    continue
                found = _raw_file("curiosity", root, entry, sol, REMS_PRODUCT, match.group(1))
                if found is not None:
                    yield found


def scan_raw_dir(root: Path, rover: str) -> list[RawFile]:
    """Every product file of the rover under ``root`` (the raw directory), all versions, ordered
    by sol, product and version; stat only, nothing is read. ``.part`` files, labels and other
    products are ignored. An unknown rover or a missing directory gives an empty list."""
    mission = MISSION_DIRS.get(rover)
    if mission is None:
        return []
    base = root / mission
    found = _scan_meda(base) if rover == "perseverance" else _scan_rems(base)
    return sorted(found, key=lambda f: (f.sol, f.product, version_key(f.version), f.path))


def select_latest(files: Sequence[RawFile]) -> tuple[list[RawFile], list[RawFile]]:
    """Split the files of one sol into the newest version of each product and the older ones."""
    best: dict[str, RawFile] = {}
    for item in files:
        current = best.get(item.product)
        if current is None or version_key(item.version) > version_key(current.version):
            best[item.product] = item
    chosen = sorted(best.values(), key=lambda f: f.product)
    older = [item for item in files if best[item.product] is not item]
    return chosen, older


# --- reading rows --------------------------------------------------------------------------


def _lines(path: Path) -> Iterator[tuple[int, str]]:
    """Non-empty lines of a text file with their numbers; the file is streamed."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for number, line in enumerate(handle, start=1):
                line = line.rstrip("\r\n")
                if line.strip():
                    yield number, line
    except UnicodeDecodeError as exc:
        raise WeatherFormatError(f"{path.name}: not a text file ({exc.reason})") from None


def _fail(path: Path, number: int, reason: object) -> WeatherFormatError:
    return WeatherFormatError(f"{path.name} line {number}: {reason}")


def _check_foreign(path: Path, counters: RawCounters) -> None:
    if counters.rows_total and counters.foreign_sol_rows > _FOREIGN_SHARE * counters.rows_total:
        raise WeatherFormatError(
            f"{path.name}: {counters.foreign_sol_rows} of {counters.rows_total} rows "
            "belong to another sol"
        )


def _meda_rows(
    path: Path, sol: int, counters: RawCounters, names: Sequence[str]
) -> Iterator[tuple[int, float, list[str]]]:
    """Rows of a MEDA CSV of ``sol``: (line number, LMST seconds, the values of ``names``)."""
    lines = _lines(path)
    first = next(lines, None)
    if first is None:
        raise WeatherFormatError(f"{path.name}: the file is empty")
    header = [name.strip() for name in first[1].split(",")]
    try:
        picks = [header.index(name) for name in ("LMST", *names)]
    except ValueError as exc:
        raise WeatherFormatError(f"{path.name}: a column is missing ({exc})") from None
    width = len(header)
    for number, line in lines:
        fields = line.split(",")
        if len(fields) != width:
            raise _fail(path, number, f"{len(fields)} fields, expected {width}")
        counters.rows_total += 1
        try:
            row_sol, seconds = parse_lmst(fields[picks[0]])
        except ValueError as exc:
            raise _fail(path, number, exc) from None
        if row_sol != sol:
            counters.foreign_sol_rows += 1
            continue
        yield number, seconds, [fields[i] for i in picks[1:]]
    _check_foreign(path, counters)


def iter_meda_ats(
    path: Path,
    sol: int,
    counters: RawCounters | None = None,
    bounds: PhysicalBounds = DEFAULT_BOUNDS,
) -> Iterator[WeatherSample]:
    """Air temperature (K) of ``CAL_ATS``: the median of the three mast sensors T1-T3 when all
    three have a value (T4 and T5 sit on the rover's front and are warmed by it)."""
    counters = counters if counters is not None else RawCounters()
    names = ("ATS_LOCAL_TEMP1", "ATS_LOCAL_TEMP2", "ATS_LOCAL_TEMP3")
    low, high = bounds.air
    for number, seconds, values in _meda_rows(path, sol, counters, names):
        try:
            temps = [x for x in map(_meda_number, values) if x is not None]
        except ValueError as exc:
            raise _fail(path, number, exc) from None
        if any(not low <= x <= high for x in temps):
            counters.out_of_range[AIR] += 1
        elif len(temps) == 3:
            counters.rows_used += 1
            yield WeatherSample(seconds, AIR, sorted(temps)[1])


def _iter_meda_single(
    path: Path,
    sol: int,
    counters: RawCounters,
    column: str,
    param: str,
    limits: tuple[float, float],
) -> Iterator[WeatherSample]:
    low, high = limits
    for number, seconds, values in _meda_rows(path, sol, counters, (column,)):
        try:
            value = _meda_number(values[0])
        except ValueError as exc:
            raise _fail(path, number, exc) from None
        if value is None:
            continue
        if not low <= value <= high:
            counters.out_of_range[param] += 1
            continue
        if param == HUMIDITY and value > 100:
            counters.humidity_over_100 += 1
        counters.rows_used += 1
        yield WeatherSample(seconds, param, value)


def iter_meda_ps(
    path: Path,
    sol: int,
    counters: RawCounters | None = None,
    bounds: PhysicalBounds = DEFAULT_BOUNDS,
) -> Iterator[WeatherSample]:
    """Pressure (Pa) of ``DER_PS``."""
    counters = counters if counters is not None else RawCounters()
    return _iter_meda_single(path, sol, counters, "PRESSURE", PRESSURE, bounds.pressure)


def iter_meda_rhs(
    path: Path,
    sol: int,
    counters: RawCounters | None = None,
    bounds: PhysicalBounds = DEFAULT_BOUNDS,
) -> Iterator[WeatherSample]:
    """Relative humidity (%) of ``DER_RHS``; values from 100 up to the bound are kept."""
    counters = counters if counters is not None else RawCounters()
    return _iter_meda_single(
        path, sol, counters, "LOCAL_RELATIVE_HUMIDITY", HUMIDITY, bounds.humidity
    )


def iter_meda_tirs(
    path: Path,
    sol: int,
    counters: RawCounters | None = None,
    bounds: PhysicalBounds = DEFAULT_BOUNDS,
) -> Iterator[WeatherSample]:
    """Ground brightness temperature (K) of ``DER_TIRS`` (part 17G, an estimate).

    ``T = (UPWARD_LW_IRRADIANCE / sigma) ** 0.25``: the product's flux is itself ``sigma * T^4``
    of the instrument's brightness temperature (emissivity 1). Only rows with the rover level
    (``ROVER_LOW_TILT`` = 1) and the wheel not in the downward field of view (the flag is 0 only
    when the wheel is seen; an empty flag counts as not seen) are used; the bounds are checked
    on those rows. Sol 1 is skipped.
    """
    counters = counters if counters is not None else RawCounters()
    if sol == 1:
        return
    names = ("UPWARD_LW_IRRADIANCE", "ROVER_LOW_TILT", "WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV")
    low, high = bounds.ground
    for number, seconds, values in _meda_rows(path, sol, counters, names):
        if values[1].strip() != "1" or values[2].strip() == "0":
            continue
        try:
            flux = _meda_number(values[0])
        except ValueError as exc:
            raise _fail(path, number, exc) from None
        if flux is None or flux <= 0:
            continue
        kelvin = (flux / _STEFAN_BOLTZMANN) ** 0.25
        if not low <= kelvin <= high:
            counters.out_of_range[GROUND] += 1
            continue
        counters.rows_used += 1
        yield WeatherSample(seconds, GROUND, kelvin)


def _vector_heading(directions: Sequence[float]) -> float | None:
    """The direction of the sum of unit vectors (degrees, from north, clockwise); ``None`` when
    the vectors cancel out."""
    east = sum(math.sin(math.radians(d)) for d in directions)
    north = sum(math.cos(math.radians(d)) for d in directions)
    if math.hypot(east, north) < 1e-9:
        return None
    return math.degrees(math.atan2(east, north)) % 360.0


def iter_meda_ws(
    path: Path,
    sol: int,
    counters: RawCounters | None = None,
    bounds: PhysicalBounds = DEFAULT_BOUNDS,
) -> Iterator[WeatherSample]:
    """Wind of ``CAL_WS``: per row the mean speed (m/s) of the booms in a stable state and the
    direction (degrees, as the source gives it) of the sum of their unit vectors."""
    counters = counters if counters is not None else RawCounters()
    names = (
        "BOOM1_HORIZONTAL_WIND_SPEED",
        "BOOM1_WIND_DIRECTION",
        "BOOM1_ALG_STATE_STABLE",
        "BOOM2_HORIZONTAL_WIND_SPEED",
        "BOOM2_WIND_DIRECTION",
        "BOOM2_ALG_STATE_STABLE",
    )
    for number, seconds, values in _meda_rows(path, sol, counters, names):
        speeds: list[float] = []
        directions: list[float] = []
        try:
            for start in (0, 3):
                speed = _checked(
                    _meda_number(values[start]), bounds.wind_speed, WIND_SPEED, counters
                )
                heading = _checked(
                    _meda_number(values[start + 1]), bounds.wind_dir, WIND_DIR, counters
                )
                if values[start + 2].strip() != "1":
                    continue
                if speed is not None:
                    speeds.append(speed)
                if heading is not None:
                    directions.append(heading)
        except ValueError as exc:
            raise _fail(path, number, exc) from None
        yield from _wind_samples(seconds, speeds, directions, counters)


def _checked(
    value: float | None, limits: tuple[float, float], param: str, counters: RawCounters
) -> float | None:
    """``value`` if it is within the bounds; a value outside is counted and dropped."""
    if value is None:
        return None
    if not limits[0] <= value <= limits[1]:
        counters.out_of_range[param] += 1
        return None
    return value


def _wind_samples(
    seconds: float, speeds: list[float], directions: list[float], counters: RawCounters
) -> Iterator[WeatherSample]:
    heading = _vector_heading(directions) if directions else None
    if speeds:
        yield WeatherSample(seconds, WIND_SPEED, sum(speeds) / len(speeds))
    if heading is not None:
        yield WeatherSample(seconds, WIND_DIR, heading)
    if speeds or heading is not None:
        counters.rows_used += 1


# REMS ``RMD`` table: 40 comma-separated columns, positions from 0 (label MODRDR6.FMT).
_RMD_FIELDS = 40
_RMD_LMST = 1
_RMD_GROUND, _RMD_GROUND_MASK = 7, 10
_RMD_AIR, _RMD_AIR_MASK = 15, 16
_RMD_UV_ABC = 20
_RMD_HUMIDITY = 30
_RMD_PRESSURE, _RMD_PRESSURE_MASK = 37, 39


def _mask(text: str) -> str:
    return text.strip().strip('"').strip()


def _rems_value(
    fields: list[str],
    column: int,
    limits: tuple[float, float],
    param: str,
    counters: RawCounters,
) -> float | None:
    """The value of a column unless it is a gap or outside the bounds (that is counted)."""
    value = _rems_number(fields[column])
    return None if value is None else _checked(value, limits, param, counters)


def _rems_row_samples(
    fields: list[str], seconds: float, bounds: PhysicalBounds, counters: RawCounters
) -> Iterator[WeatherSample]:
    air = _rems_value(fields, _RMD_AIR, bounds.air, AIR, counters)
    mask = _mask(fields[_RMD_AIR_MASK])
    if air is not None and len(mask) >= 9 and mask[7] == "1" and mask[8] == "1":
        yield WeatherSample(seconds, AIR, air)
    pressure = _rems_value(fields, _RMD_PRESSURE, bounds.pressure, PRESSURE, counters)
    mask = _mask(fields[_RMD_PRESSURE_MASK])
    if pressure is not None and len(mask) >= 2 and mask[1] == "1":
        yield WeatherSample(seconds, PRESSURE, pressure)
    ground = _rems_value(fields, _RMD_GROUND, bounds.ground, GROUND, counters)
    mask = _mask(fields[_RMD_GROUND_MASK])
    if ground is not None and mask[:2] == "11":
        yield WeatherSample(seconds, GROUND, ground)
    uv = _rems_number(fields[_RMD_UV_ABC])
    if uv is not None and uv >= 0 and _checked(uv, bounds.uv, UV, counters) is not None:
        yield WeatherSample(seconds, UV, uv)
    humidity = _rems_value(fields, _RMD_HUMIDITY, bounds.humidity, HUMIDITY, counters)
    if humidity is not None:
        if humidity > 100:
            counters.humidity_over_100 += 1
        yield WeatherSample(seconds, HUMIDITY, humidity)


def iter_rems_rmd(
    path: Path,
    sol: int,
    counters: RawCounters | None = None,
    bounds: PhysicalBounds = DEFAULT_BOUNDS,
) -> Iterator[WeatherSample]:
    """Samples of a REMS ``RMD`` table: air, pressure, ground temperature, UV and humidity;
    the wind columns are not read. The confidence masks decide which values count: air needs
    bytes 7 and 8 of its mask to be ``1``, pressure byte 1, ground temperature bytes 0 and 1."""
    counters = counters if counters is not None else RawCounters()
    for number, line in _lines(path):
        fields = line.split(",")
        if len(fields) != _RMD_FIELDS:
            raise _fail(path, number, f"{len(fields)} fields, expected {_RMD_FIELDS}")
        counters.rows_total += 1
        try:
            row_sol, seconds = parse_lmst(fields[_RMD_LMST])
            if row_sol != sol:
                counters.foreign_sol_rows += 1
                continue
            samples = list(_rems_row_samples(fields, seconds, bounds, counters))
        except ValueError as exc:
            raise _fail(path, number, exc) from None
        if samples:
            counters.rows_used += 1
        yield from samples
    _check_foreign(path, counters)
