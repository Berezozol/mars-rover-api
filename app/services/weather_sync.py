"""Weather per sol from the local copy of the archive (stage 17): ``weather parse``.

``WeatherParser.run`` scans ``WEATHER_RAW_DIR`` (stat only), picks the newest version of every
product of every sol, compares size, modification time and version with ``weather_files`` and
reads only the sols that are new or changed (or whose rows were made by an older
``WEATHER_PARSER_REVISION``). The files of a sol are read by ``parse_sol_files`` in a worker
process, which has no database and returns one small ``SolParseResult``; the main process then
stores the sol (its files, its summary and its 24 hours) in ONE transaction, so an interrupted
run leaves no half-written sol and the next run goes on.

A summary is made by ``combine_sol``: ``min`` and ``max`` are the extremes of the valid values,
``mean`` is the mean of the hourly means over the hours that have data (an uneven schedule of
the instrument does not skew it), ``coverage`` is the share of the 144 ten-minute bins of the
local day that have an air temperature. The whole run holds the advisory lock
``WEATHER_LOCK_NAMESPACE`` (shared with ``weather import``).
"""

import asyncio
import logging
import math
import os
from array import array
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import PROJECT_ROOT, Settings
from app.core.mars_time import solar_longitude
from app.db.models import WeatherFile as WeatherFileRow
from app.db.session import create_session_factory
from app.repositories import weather as weather_repo
from app.sources import weather as src
from app.sources.weather import (
    AIR,
    DEFAULT_BOUNDS,
    GROUND,
    HUMIDITY,
    MEDA_PRODUCTS,
    MISSION_DIRS,
    PRESSURE,
    UV,
    WIND_DIR,
    WIND_SPEED,
    LabelInfo,
    RawCounters,
    RawFile,
    WeatherFormatError,
    WeatherSample,
)

logger = logging.getLogger(__name__)

# First key of the advisory lock of ``weather parse`` and ``weather import`` (RAD uses 7_250_004).
WEATHER_LOCK_NAMESPACE = 7_250_005
# Raise it whenever the reading, the bounds or the aggregation change: every sol is read again.
WEATHER_PARSER_REVISION = 5
FLAG_ORDER = ("partial_coverage", "humidity_over_100", "out_of_range")

EXIT_OK = 0
EXIT_REMAINING = 1
EXIT_USAGE = 2

BINS_PER_HOUR = 6
BINS_PER_SOL = 24 * BINS_PER_HOUR
SECTORS = 16
_SECTOR_WIDTH = 360.0 / SECTORS
# The response key of every parameter (the ``parameter`` of a flag).
PARAMETER_KEYS = {
    AIR: "air_temp_c",
    PRESSURE: "pressure_pa",
    HUMIDITY: "humidity_pct",
    GROUND: "ground_temp_c",
    UV: "uv_wm2",
    WIND_SPEED: "wind",
    WIND_DIR: "wind",
}
_PRODUCT_READERS = {
    "CAL_ATS": src.iter_meda_ats,
    "CAL_WS": src.iter_meda_ws,
    "DER_PS": src.iter_meda_ps,
    "DER_RHS": src.iter_meda_rhs,
    "DER_TIRS": src.iter_meda_tirs,
    "RMD": src.iter_rems_rmd,
}
# Products that make a sol of each rover (a missing one just means no such parameter).
ROVER_PRODUCTS = {"perseverance": MEDA_PRODUCTS, "curiosity": ("RMD",)}


# --- aggregation ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stat:
    """The extremes of the valid values of a sol, the mean of the hourly means and the count."""

    min: float
    max: float
    mean: float
    n: int


@dataclass(frozen=True)
class WindStat:
    """Speed (m/s) and direction (degrees, from north, clockwise) of a sol."""

    speed_mean: float
    gust: float
    direction_mode: float | None
    direction_r: float | None
    n: int


@dataclass(frozen=True)
class HourAggregate:
    """The means of one hour of local mean solar time (``None``: nothing valid in the hour)."""

    hour: int
    coverage: float
    air: float | None = None
    pressure: float | None = None
    humidity: float | None = None
    ground: float | None = None
    uv: float | None = None
    wind_speed: float | None = None
    wind_dir: float | None = None
    # The lowest and highest valid value of the hour (stage 22) and the gust (99th percentile
    # of the wind speed of the hour).
    air_min: float | None = None
    air_max: float | None = None
    pressure_min: float | None = None
    pressure_max: float | None = None
    humidity_min: float | None = None
    humidity_max: float | None = None
    ground_min: float | None = None
    ground_max: float | None = None
    uv_min: float | None = None
    uv_max: float | None = None
    wind_gust: float | None = None


@dataclass(frozen=True)
class SolAggregate:
    """Everything stored for one sol."""

    sol: int
    earth_date: date
    ls: float | None
    coverage: float
    hours_covered: int
    air: Stat | None
    pressure: Stat | None
    humidity: Stat | None
    ground: Stat | None
    uv: Stat | None
    wind: WindStat | None
    hours: tuple[HourAggregate, ...]
    flags: list[dict[str, Any]]
    source_version: dict[str, str]

    def daily_row(self, rover: str, revision: int) -> dict[str, Any]:
        """The ``weather_daily`` row (without ``updated_at``)."""
        row: dict[str, Any] = {
            "rover_id": rover,
            "sol": self.sol,
            "earth_date": self.earth_date,
            "ls": self.ls,
            "coverage": self.coverage,
            "hours_covered": self.hours_covered,
        }
        for prefix, unit, stat in (
            ("air", "k", self.air),
            ("pressure", "pa", self.pressure),
            ("humidity", "pct", self.humidity),
            ("ground", "k", self.ground),
        ):
            row[f"{prefix}_min_{unit}"] = stat.min if stat else None
            row[f"{prefix}_max_{unit}"] = stat.max if stat else None
            row[f"{prefix}_mean_{unit}"] = stat.mean if stat else None
        wind = self.wind
        row.update(
            uv_max_wm2=self.uv.max if self.uv else None,
            uv_mean_wm2=self.uv.mean if self.uv else None,
            wind_mean_ms=wind.speed_mean if wind else None,
            wind_gust_ms=wind.gust if wind else None,
            wind_dir_mode_deg=wind.direction_mode if wind else None,
            wind_dir_r=wind.direction_r if wind else None,
            n_air=self.air.n if self.air else 0,
            n_pressure=self.pressure.n if self.pressure else 0,
            n_humidity=self.humidity.n if self.humidity else 0,
            n_ground=self.ground.n if self.ground else 0,
            n_uv=self.uv.n if self.uv else 0,
            n_wind=wind.n if wind else 0,
            flags=self.flags,
            source_version=self.source_version,
            parser_revision=revision,
        )
        return row

    def hourly_rows(self, rover: str) -> list[dict[str, Any]]:
        """The 24 ``weather_hourly`` rows."""
        return [
            {
                "rover_id": rover,
                "sol": self.sol,
                "hour": item.hour,
                "coverage": item.coverage,
                "air_k": item.air,
                "pressure_pa": item.pressure,
                "humidity_pct": item.humidity,
                "ground_k": item.ground,
                "uv_wm2": item.uv,
                "wind_ms": item.wind_speed,
                "wind_dir_deg": item.wind_dir,
                "air_min_k": item.air_min,
                "air_max_k": item.air_max,
                "pressure_min_pa": item.pressure_min,
                "pressure_max_pa": item.pressure_max,
                "humidity_min_pct": item.humidity_min,
                "humidity_max_pct": item.humidity_max,
                "ground_min_k": item.ground_min,
                "ground_max_k": item.ground_max,
                "uv_min_wm2": item.uv_min,
                "uv_max_wm2": item.uv_max,
                "wind_gust_ms": item.wind_gust,
            }
            for item in self.hours
        ]


class _Series:
    """Extremes, and sums and counts per hour, of one parameter."""

    __slots__ = ("counts", "high", "highs", "low", "lows", "sums", "total")

    def __init__(self) -> None:
        self.low = math.inf
        self.high = -math.inf
        self.total = 0
        self.sums = [0.0] * 24
        self.counts = [0] * 24
        self.lows = [math.inf] * 24
        self.highs = [-math.inf] * 24

    def add(self, value: float, hour: int) -> None:
        if value < self.low:
            self.low = value
        if value > self.high:
            self.high = value
        self.total += 1
        self.sums[hour] += value
        self.counts[hour] += 1
        if value < self.lows[hour]:
            self.lows[hour] = value
        if value > self.highs[hour]:
            self.highs[hour] = value

    def hour_low(self, hour: int) -> float | None:
        return self.lows[hour] if self.counts[hour] else None

    def hour_high(self, hour: int) -> float | None:
        return self.highs[hour] if self.counts[hour] else None

    def hour_mean(self, hour: int) -> float | None:
        return self.sums[hour] / self.counts[hour] if self.counts[hour] else None

    def hourly_means(self) -> list[float]:
        return [self.sums[h] / self.counts[h] for h in range(24) if self.counts[h]]

    def mean_of_hours(self) -> float:
        means = self.hourly_means()
        return sum(means) / len(means)

    def stat(self) -> Stat | None:
        if not self.total:
            return None
        return Stat(self.low, self.high, self.mean_of_hours(), self.total)


class _Wind:
    """Wind speeds (all kept, for the percentile) and directions (sectors, vector sum)."""

    def __init__(self) -> None:
        self.speed = _Series()
        self.speeds = array("d")
        self.hour_speeds = [array("d") for _ in range(24)]
        self.sectors = [0] * SECTORS
        self.hour_sectors = [[0] * SECTORS for _ in range(24)]
        self.east = 0.0
        self.north = 0.0
        self.directions = 0

    def add_speed(self, value: float, hour: int) -> None:
        self.speed.add(value, hour)
        self.speeds.append(value)
        self.hour_speeds[hour].append(value)

    def hour_gust(self, hour: int) -> float | None:
        speeds = self.hour_speeds[hour]
        return percentile(sorted(speeds), 0.99) if speeds else None

    def add_direction(self, degrees: float, hour: int) -> None:
        sector = int(((degrees + _SECTOR_WIDTH / 2) % 360.0) // _SECTOR_WIDTH)
        self.sectors[sector] += 1
        self.hour_sectors[hour][sector] += 1
        self.east += math.sin(math.radians(degrees))
        self.north += math.cos(math.radians(degrees))
        self.directions += 1

    def hour_direction(self, hour: int) -> float | None:
        return _mode_center(self.hour_sectors[hour])

    def stat(self) -> WindStat | None:
        if not self.speed.total:
            return None
        resultant = (
            round(math.hypot(self.east, self.north) / self.directions, 6)
            if self.directions
            else None
        )
        return WindStat(
            self.speed.mean_of_hours(),
            percentile(sorted(self.speeds), 0.99),
            _mode_center(self.sectors),
            resultant,
            self.speed.total,
        )


def _mode_center(sectors: Sequence[int]) -> float | None:
    """The centre (degrees) of the sector with the most values; the first of equal ones."""
    top = max(sectors)
    return sectors.index(top) * _SECTOR_WIDTH if top else None


def percentile(ordered: Sequence[float], fraction: float) -> float:
    """The ``fraction`` quantile of a sorted sequence, linear interpolation between neighbours."""
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def _flags(hours_covered: int, counters: RawCounters, full_hours: int) -> list[dict[str, Any]]:
    """Remarks on a sol in the order of ``FLAG_ORDER``, inside a code by ``parameter``."""
    flags: list[dict[str, Any]] = []
    if hours_covered < full_hours:
        flags.append({"code": "partial_coverage", "parameter": None, "count": None})
    if counters.humidity_over_100:
        flags.append(
            {
                "code": "humidity_over_100",
                "parameter": PARAMETER_KEYS[HUMIDITY],
                "count": counters.humidity_over_100,
            }
        )
    faults: dict[str, int] = defaultdict(int)
    for param, count in counters.out_of_range.items():
        if count and param in PARAMETER_KEYS:
            faults[PARAMETER_KEYS[param]] += count
    flags.extend(
        {"code": "out_of_range", "parameter": key, "count": faults[key]} for key in sorted(faults)
    )
    return [item for code in FLAG_ORDER for item in flags if item["code"] == code]


def _start_time(labels: Sequence[LabelInfo]) -> datetime:
    starts = [item.start_time for item in labels if item.start_time is not None]
    if not starts:
        raise WeatherFormatError("no label with a start time (start_date_time)")
    return min(starts)


_NIGHT_HOURS = frozenset((4, 5, 6))
_NOON_HOURS = frozenset((11, 12, 13))


def _ground_complete(counts: Sequence[int], full_hours: int) -> bool:
    """Ground temperature of a sol is kept when its samples cover ``full_hours`` hours, at
    least two of the coldest hours (4-6) and two of the warmest (11-13)."""
    hours = {hour for hour, count in enumerate(counts) if count}
    return (
        len(hours) >= full_hours
        and len(hours & _NIGHT_HOURS) >= 2
        and len(hours & _NOON_HOURS) >= 2
    )


def combine_sol(
    samples: Iterable[WeatherSample],
    labels: Sequence[LabelInfo],
    *,
    sol: int = 0,
    counters: RawCounters | None = None,
    source_version: Mapping[str, str] | None = None,
    full_hours: int = 18,
    ground_rule: bool = False,
) -> SolAggregate | None:
    """The summary of one sol from its samples, or ``None`` when nothing valid was measured.

    ``labels`` give the date and Ls of the start of the sol (the earliest ``start_time``);
    ``counters`` (filled by the readers while ``samples`` is consumed) give the flags.
    """
    counters = counters if counters is not None else RawCounters()
    series = {name: _Series() for name in (AIR, PRESSURE, HUMIDITY, GROUND, UV)}
    wind = _Wind()
    bins: set[int] = set()
    for lmst_s, param, value in samples:
        hour = min(int(lmst_s // 3600), 23)
        if param == WIND_SPEED:
            wind.add_speed(value, hour)
        elif param == WIND_DIR:
            wind.add_direction(value, hour)
        else:
            series[param].add(value, hour)
            if param == AIR:
                bins.add(min(int(lmst_s // 600), BINS_PER_SOL - 1))
    if ground_rule and not _ground_complete(series[GROUND].counts, full_hours):
        series[GROUND] = _Series()  # an incomplete TIRS session is not used (summary and hours)
    wind_stat = wind.stat()
    if wind_stat is None and not any(item.total for item in series.values()):
        return None
    start = _start_time(labels)
    hours_covered = sum(1 for count in series[AIR].counts if count)
    return SolAggregate(
        sol=sol,
        earth_date=start.astimezone(UTC).date(),
        ls=round(solar_longitude(start), 3),
        coverage=round(len(bins) / BINS_PER_SOL, 4),
        hours_covered=hours_covered,
        air=series[AIR].stat(),
        pressure=series[PRESSURE].stat(),
        humidity=series[HUMIDITY].stat(),
        ground=series[GROUND].stat(),
        uv=series[UV].stat(),
        wind=wind_stat,
        hours=_hours(series, wind, bins),
        flags=_flags(hours_covered, counters, full_hours),
        source_version=dict(sorted((source_version or {}).items())),
    )


def _hours(series: dict[str, _Series], wind: _Wind, bins: set[int]) -> tuple[HourAggregate, ...]:
    per_hour = [0] * 24
    for index in bins:
        per_hour[index // BINS_PER_HOUR] += 1
    return tuple(
        HourAggregate(
            hour=hour,
            coverage=round(per_hour[hour] / BINS_PER_HOUR, 4),
            air=series[AIR].hour_mean(hour),
            pressure=series[PRESSURE].hour_mean(hour),
            humidity=series[HUMIDITY].hour_mean(hour),
            ground=series[GROUND].hour_mean(hour),
            uv=series[UV].hour_mean(hour),
            wind_speed=wind.speed.hour_mean(hour),
            wind_dir=wind.hour_direction(hour),
            air_min=series[AIR].hour_low(hour),
            air_max=series[AIR].hour_high(hour),
            pressure_min=series[PRESSURE].hour_low(hour),
            pressure_max=series[PRESSURE].hour_high(hour),
            humidity_min=series[HUMIDITY].hour_low(hour),
            humidity_max=series[HUMIDITY].hour_high(hour),
            ground_min=series[GROUND].hour_low(hour),
            ground_max=series[GROUND].hour_high(hour),
            uv_min=series[UV].hour_low(hour),
            uv_max=series[UV].hour_high(hour),
            wind_gust=wind.hour_gust(hour),
        )
        for hour in range(24)
    )


# --- reading the files of a sol (in a worker process) ---------------------------------------


@dataclass(frozen=True)
class FileOutcome:
    """What reading one file gave (the numbers and label of a file that was read to the end)."""

    path: str
    rows_total: int = 0
    rows_used: int = 0
    foreign_sol_rows: int = 0
    label_start_time: datetime | None = None
    label_ls: float | None = None


@dataclass(frozen=True)
class SolParseResult:
    """The result of reading one sol: its summary (``None``: no valid values) or an error."""

    sol: int
    aggregate: SolAggregate | None
    files: tuple[FileOutcome, ...] = ()
    error: str | None = None
    failed_path: str | None = None


def _read_label(item: RawFile) -> LabelInfo:
    assert item.full_path is not None
    read = src.read_rems_label if item.product == "RMD" else src.read_meda_label
    return read(item.full_path)


def describe_error(exc: BaseException) -> str:
    """A short reason for ``last_error``: no local file names (an OSError gives its strerror)."""
    text = (exc.strerror or "") if isinstance(exc, OSError) else str(exc)
    return f"{type(exc).__name__}: {text}"[:500]


def parse_sol_files(
    rover: str,
    sol: int,
    files: Sequence[RawFile],
    humidity_max: float = 200.0,
    full_hours: int = 18,
) -> SolParseResult:
    """Read the files of one sol (the newest version of each product) into a summary.

    Runs in a worker process and touches no database. A damaged file (``WeatherFormatError``
    or an unreadable one) gives a result with ``error`` and ``failed_path``; nothing is raised
    for it.
    """
    bounds = DEFAULT_BOUNDS.with_humidity_max(humidity_max)
    total = RawCounters()
    outcomes: list[FileOutcome] = []
    labels: list[LabelInfo] = []
    current: list[str] = [files[0].path]

    def stream() -> Iterator[WeatherSample]:
        for item, label in zip(files, labels, strict=True):
            current[:] = [item.path]
            counters = RawCounters()
            reader = _PRODUCT_READERS[item.product]
            assert item.full_path is not None
            yield from reader(item.full_path, sol, counters, bounds)
            total.merge(counters)
            outcomes.append(
                FileOutcome(
                    item.path,
                    counters.rows_total,
                    counters.rows_used,
                    counters.foreign_sol_rows,
                    label.start_time,
                    label.ls,
                )
            )

    versions = {item.product: item.version for item in files}
    try:
        for item in files:
            current[:] = [item.path]
            labels.append(_read_label(item))
        aggregate = combine_sol(
            stream(),
            labels,
            sol=sol,
            counters=total,
            source_version=versions,
            full_hours=full_hours,
            ground_rule=rover == "perseverance",
        )
    except (WeatherFormatError, OSError) as exc:
        return SolParseResult(sol, None, tuple(outcomes), describe_error(exc), current[0])
    return SolParseResult(sol, aggregate, tuple(outcomes))


# --- the parser ----------------------------------------------------------------------------


@dataclass
class WeatherRunResult:
    rover: str = ""
    sols_planned: int = 0
    sols_parsed: int = 0
    sols_failed: list[int] = field(default_factory=list)
    sols_unchanged: int = 0
    sols_deferred: int = 0
    sols_without_data: int = 0
    sols_changed: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    sols_missing: list[int] = field(default_factory=list)
    sols_pruned: int = 0
    prune_refused: bool = False
    locked: bool = False
    exit_code: int = EXIT_OK
    error: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Planned:
    sol: int
    chosen: list[RawFile]
    older: list[RawFile]


@asynccontextmanager
async def advisory_lock(engine: AsyncEngine) -> AsyncIterator[bool]:
    """Hold the weather advisory lock on a connection of its own; yields whether it was free."""
    async with engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        statement = select(func.pg_try_advisory_lock(WEATHER_LOCK_NAMESPACE))
        acquired = bool(await connection.scalar(statement))
        try:
            yield acquired
        finally:
            if acquired:
                await connection.execute(select(func.pg_advisory_unlock(WEATHER_LOCK_NAMESPACE)))


def raw_root(settings: Settings) -> Path:
    """The raw directory; a relative setting is resolved against the project root."""
    path = settings.weather_raw_dir
    return path if path.is_absolute() else PROJECT_ROOT / path


def worker_count(settings: Settings) -> int:
    return settings.weather_parse_workers or max(1, (os.cpu_count() or 2) - 2)


def _group_by_sol(files: Iterable[RawFile]) -> dict[int, list[RawFile]]:
    grouped: dict[int, list[RawFile]] = defaultdict(list)
    for item in files:
        grouped[item.sol].append(item)
    return grouped


def _in_range(sol: int, from_sol: int | None, to_sol: int | None) -> bool:
    return (from_sol is None or sol >= from_sol) and (to_sol is None or sol <= to_sol)


def _needs_reading(
    chosen: Sequence[RawFile],
    stored: Mapping[str, WeatherFileRow],
    revision: int | None,
) -> bool:
    """A sol is read when a file is new, changed or failed, a stored file is gone, or the
    sol's rows come from another parser revision."""
    if revision is not None and revision != WEATHER_PARSER_REVISION:
        return True
    names = {item.path for item in chosen}
    if any(row.status == "parsed" and path not in names for path, row in stored.items()):
        return True
    for item in chosen:
        row = stored.get(item.path)
        if row is None or row.status != "parsed":
            return True
        if (row.size_bytes, row.mtime_ns, row.version) != (item.size, item.mtime_ns, item.version):
            return True
        if row.parser_revision != WEATHER_PARSER_REVISION:
            return True
    return False


class WeatherParser:
    """``weather parse``: bring the stored summaries up to date with the raw directory."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings
        self._factory = create_session_factory(engine)

    async def run(
        self,
        rover: str,
        from_sol: int | None = None,
        to_sol: int | None = None,
        *,
        force: bool = False,
        prune: bool = False,
    ) -> WeatherRunResult:
        """Parse the new and changed sols of ``rover`` (optionally within a sol range).

        ``ValueError`` for an unknown rover or a bad range (nothing is changed). Exit codes of
        the result: 0 done, 1 a file failed or a prune was refused, 2 the raw directory is
        missing or another ``weather parse`` / ``weather import`` holds the lock.
        """
        if rover not in MISSION_DIRS:
            raise ValueError(f"unknown rover {rover!r}: use perseverance or curiosity")
        for value in (from_sol, to_sol):
            if value is not None and value < 0:
                raise ValueError("sols start at 0")
        if None not in (from_sol, to_sol) and from_sol > to_sol:  # type: ignore[operator]
            raise ValueError("--from-sol must not be greater than --to-sol")
        async with advisory_lock(self._engine) as acquired:
            if not acquired:
                return WeatherRunResult(rover, locked=True, exit_code=EXIT_USAGE)
            return await self._run_locked(rover, from_sol, to_sol, force, prune)

    async def _run_locked(
        self, rover: str, from_sol: int | None, to_sol: int | None, force: bool, prune: bool
    ) -> WeatherRunResult:
        result = WeatherRunResult(rover)
        root = raw_root(self._settings)
        if not root.is_dir():
            result.exit_code = EXIT_USAGE
            result.error = "WEATHER_RAW_DIR does not exist"
            return result
        scanned = await asyncio.to_thread(src.scan_raw_dir, root, rover)
        by_sol = {
            sol: files
            for sol, files in _group_by_sol(scanned).items()
            if _in_range(sol, from_sol, to_sol)
        }
        async with self._factory() as session:
            stored = await weather_repo.list_files(session, rover)
            revisions = await weather_repo.daily_revisions(session, rover)
        plan = self._plan(by_sol, stored, revisions, force, result)
        result.sols_planned = len(plan)
        known = set(stored) | set(revisions)
        result.sols_missing = sorted(
            sol for sol in known if sol not in by_sol and _in_range(sol, from_sol, to_sol)
        )
        await self._parse_all(rover, plan, stored, result)
        if prune and result.sols_missing:
            await self._prune(rover, result, len(known))
        await self._finish(rover, result)
        return result

    def _plan(
        self,
        by_sol: Mapping[int, Sequence[RawFile]],
        stored: Mapping[int, Mapping[str, WeatherFileRow]],
        revisions: Mapping[int, int],
        force: bool,
        result: WeatherRunResult,
    ) -> list[_Planned]:
        plan: list[_Planned] = []
        now_ns = datetime.now(UTC).timestamp() * 1e9
        min_age_ns = self._settings.weather_min_file_age_seconds * 1e9
        for sol in sorted(by_sol):
            chosen, older = src.select_latest(by_sol[sol])
            if any(now_ns - item.mtime_ns < min_age_ns for item in chosen):
                result.sols_deferred += 1
            elif force or _needs_reading(chosen, stored.get(sol, {}), revisions.get(sol)):
                plan.append(_Planned(sol, chosen, older))
            else:
                result.sols_unchanged += 1
        return plan

    def _executor(self) -> tuple[Executor, int]:
        workers = worker_count(self._settings)
        if workers == 1:
            return ThreadPoolExecutor(max_workers=1), 1
        return ProcessPoolExecutor(max_workers=workers), workers

    async def _parse_all(
        self,
        rover: str,
        plan: Sequence[_Planned],
        stored: Mapping[int, Mapping[str, WeatherFileRow]],
        result: WeatherRunResult,
    ) -> bool:
        """Read the planned sols in the workers and store each one as it arrives."""
        if not plan:
            return False
        loop = asyncio.get_running_loop()
        executor, workers = self._executor()
        todo = iter(plan)
        running: dict[asyncio.Future[SolParseResult], _Planned] = {}
        changed = False
        try:
            while True:
                while len(running) < workers * 2 and (item := next(todo, None)) is not None:
                    job = partial(
                        parse_sol_files,
                        rover,
                        item.sol,
                        item.chosen,
                        self._settings.weather_humidity_max,
                        self._settings.weather_full_hours,
                    )
                    running[loop.run_in_executor(executor, job)] = item
                if not running:
                    break
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    item = running.pop(future)
                    changed |= await self._store(
                        rover, item, future.result(), stored.get(item.sol, {}), result
                    )
        finally:
            for future in running:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        return changed

    async def _store(
        self,
        rover: str,
        item: _Planned,
        parsed: SolParseResult,
        stored: Mapping[str, WeatherFileRow],
        result: WeatherRunResult,
    ) -> bool:
        """Write one sol in one transaction; True when the summary or its hours changed."""
        if parsed.error is not None:
            await self._store_failure(rover, item, parsed)
            result.sols_failed.append(item.sol)
            result.files_failed += 1
            logger.warning("weather sol %s of %s failed: %s", item.sol, rover, parsed.error)
            return False
        now = datetime.now(UTC)
        outcomes = {outcome.path: outcome for outcome in parsed.files}
        rows = [_file_row(rover, raw, outcomes.get(raw.path), now) for raw in item.chosen]
        rows += [_file_row(rover, raw, None, now, superseded=True) for raw in item.older]
        gone = set(stored) - {row["path"] for row in rows}
        async with self._factory() as session, session.begin():
            await weather_repo.upsert_files(session, rows)
            await weather_repo.delete_files(session, rover, gone)
            changed = await self._write_aggregate(session, rover, item.sol, parsed.aggregate)
            if changed:
                await weather_repo.touch_dataset(session, rover)
        result.sols_parsed += 1
        result.files_parsed += len(item.chosen)
        result.sols_changed += int(changed)
        result.sols_without_data += int(parsed.aggregate is None)
        return changed

    async def _write_aggregate(
        self, session: Any, rover: str, sol: int, aggregate: SolAggregate | None
    ) -> bool:
        if aggregate is None:
            return bool(await weather_repo.delete_daily(session, rover, [sol]))
        row = aggregate.daily_row(rover, WEATHER_PARSER_REVISION)
        daily_changed = await weather_repo.upsert_daily(session, row)
        hours_changed = await weather_repo.replace_hourly(
            session, rover, sol, aggregate.hourly_rows(rover)
        )
        return daily_changed or hours_changed

    async def _store_failure(self, rover: str, item: _Planned, parsed: SolParseResult) -> None:
        """Mark the file that could not be read; the rest of the sol stays as it was."""
        raw = next((r for r in item.chosen if r.path == parsed.failed_path), item.chosen[0])
        row = _file_row(rover, raw, None, datetime.now(UTC))
        row.update(status="failed", error=parsed.error)
        async with self._factory() as session, session.begin():
            await weather_repo.upsert_files(session, [row])

    async def _prune(self, rover: str, result: WeatherRunResult, known: int) -> bool:
        """Forget the sols whose files are all gone, unless that is too many of them."""
        gone = result.sols_missing
        if len(gone) > (1 - self._settings.weather_min_keep_ratio) * known:
            result.prune_refused = True
            result.exit_code = EXIT_REMAINING
            result.notes.append(
                f"prune refused: {len(gone)} of {known} sols have no files on disk "
                f"(WEATHER_MIN_KEEP_RATIO={self._settings.weather_min_keep_ratio})"
            )
            return False
        async with self._factory() as session, session.begin():
            deleted = await weather_repo.delete_daily(session, rover, gone)
            await weather_repo.delete_files_of_sols(session, rover, gone)
            if deleted:
                await weather_repo.touch_dataset(session, rover)
        result.sols_pruned = len(gone)
        return bool(deleted)

    async def _finish(self, rover: str, result: WeatherRunResult) -> None:
        """Record the run in the state row of the rover."""
        if result.sols_failed:
            first = result.sols_failed[0]
            result.error = f"{len(result.sols_failed)} sols failed, the first is sol {first}"
            result.exit_code = max(result.exit_code, EXIT_REMAINING)
        async with self._factory() as session, session.begin():
            max_sol, _ = await weather_repo.daily_extent(session, rover)
            await weather_repo.save_dataset(
                session,
                rover,
                parsed_at=datetime.now(UTC),
                files_parsed=result.files_parsed,
                files_failed=result.files_failed,
                last_error=result.error,
                source_max_sol=max_sol,
            )


def _file_row(
    rover: str,
    raw: RawFile,
    outcome: FileOutcome | None,
    now: datetime,
    *,
    superseded: bool = False,
) -> dict[str, Any]:
    """A ``weather_files`` row of a file that was read (or skipped as an older version)."""
    return {
        "rover_id": rover,
        "path": raw.path,
        "sol": raw.sol,
        "product": raw.product,
        "version": raw.version,
        "size_bytes": raw.size,
        "mtime_ns": raw.mtime_ns,
        "status": "superseded" if superseded else "parsed",
        "rows_total": outcome.rows_total if outcome else 0,
        "rows_used": outcome.rows_used if outcome else 0,
        "foreign_sol_rows": outcome.foreign_sol_rows if outcome else 0,
        "label_start_time": outcome.label_start_time if outcome else None,
        "label_ls": outcome.label_ls if outcome else None,
        "error": None,
        "parser_revision": WEATHER_PARSER_REVISION,
        "parsed_at": now,
    }


# --- status --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoverWeatherStatus:
    rover: str
    state: weather_repo.WeatherStatus
    # Sols with stored rows but no file on disk (None: the raw directory is not there).
    vanished_sols: list[int] | None


async def collect_status(
    engine: AsyncEngine, settings: Settings, rovers: Sequence[str]
) -> list[RoverWeatherStatus]:
    """The stored state of each rover and the sols whose files left the disk."""
    root = raw_root(settings)
    found: list[RoverWeatherStatus] = []
    for rover in rovers:
        async with create_session_factory(engine)() as session:
            state = await weather_repo.status(session, rover)
        vanished: list[int] | None = None
        if root.is_dir():
            on_disk = {f.sol for f in await asyncio.to_thread(src.scan_raw_dir, root, rover)}
            vanished = sorted(state.file_sols - on_disk)
        found.append(RoverWeatherStatus(rover, state, vanished))
    return found
