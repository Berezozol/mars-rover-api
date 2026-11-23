"""Move the weather data between databases with a file: ``weather export`` and ``weather import``.

The raw files are parsed on a home computer and the summaries are carried to the server as one
small file: gzip-compressed JSON Lines. The first line is ``meta`` (format, version, schema
revision, counts per rover), then one ``dataset`` line per rover, one ``daily`` line per
``weather_daily`` row and one ``hourly`` line per ``weather_hourly`` row; times are ISO 8601 UTC.
``weather_files`` is not carried (the raw files are not on the server). The import checks
everything before it touches the database, then replaces the data of the rovers in the file in
one transaction under the weather advisory lock.
"""

import asyncio
import gzip
import json
import logging
import math
import zlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.repositories import weather as weather_repo
from app.services.weather_sync import WEATHER_LOCK_NAMESPACE

logger = logging.getLogger(__name__)

EXPORT_FORMAT = "mars-weather-export"
EXPORT_VERSION = 1
SCHEMA_REVISION = "0018"
ROVERS = ("curiosity", "perseverance")
# Columns of the state row that an import sets itself.
_IMPORT_OWN = ("imported_at", "import_meta")
_MAX_INT = 2**31 - 1

Float = Annotated[float, Field(allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, le=_MAX_INT)]
Rover = Literal["curiosity", "perseverance"]
_STATS = (("air", "k"), ("pressure", "pa"), ("humidity", "pct"), ("ground", "k"))
_HOURLY_FLOATS = (
    "air_k",
    "pressure_pa",
    "humidity_pct",
    "ground_k",
    "uv_wm2",
    "wind_ms",
    "wind_dir_deg",
)
_HOURLY_KEYS = frozenset(("t", "rover_id", "sol", "hour", "coverage", *_HOURLY_FLOATS))


class WeatherTransferError(Exception):
    """Base of the errors of export and import."""


class WeatherImportError(WeatherTransferError):
    """The file cannot be imported (wrong format, damaged, invalid rows): nothing changed."""


class WeatherImportRefused(WeatherTransferError):
    """The file has far fewer sols than the database; ``force`` imports it anyway."""


class WeatherBusy(WeatherTransferError):
    """Another ``weather parse`` or ``weather import`` holds the lock."""


@dataclass(frozen=True)
class TransferResult:
    status: Literal["exported", "imported", "unchanged"]
    # Per rover: (daily rows, hourly rows).
    counts: dict[str, tuple[int, int]]

    @property
    def daily(self) -> int:
        return sum(daily for daily, _ in self.counts.values())

    @property
    def hourly(self) -> int:
        return sum(hourly for _, hourly in self.counts.values())


class _Line(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Flag(_Line):
    code: Annotated[str, Field(max_length=40)]
    parameter: Annotated[str, Field(max_length=40)] | None
    count: Count | None


class _DailyLine(_Line):
    t: Literal["daily"]
    rover_id: Rover
    sol: Count
    earth_date: date
    ls: Float | None
    coverage: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    hours_covered: Annotated[int, Field(ge=0, le=24)]
    air_min_k: Float | None
    air_max_k: Float | None
    air_mean_k: Float | None
    pressure_min_pa: Float | None
    pressure_max_pa: Float | None
    pressure_mean_pa: Float | None
    humidity_min_pct: Float | None
    humidity_max_pct: Float | None
    humidity_mean_pct: Float | None
    ground_min_k: Float | None
    ground_max_k: Float | None
    ground_mean_k: Float | None
    uv_max_wm2: Float | None
    uv_mean_wm2: Float | None
    wind_mean_ms: Float | None
    wind_gust_ms: Float | None
    wind_dir_mode_deg: Float | None
    wind_dir_r: Float | None
    n_air: Count
    n_pressure: Count
    n_humidity: Count
    n_ground: Count
    n_uv: Count
    n_wind: Count
    flags: Annotated[list[_Flag], Field(max_length=50)]
    source_version: Annotated[
        dict[Annotated[str, Field(max_length=40)], Annotated[str, Field(max_length=20)]],
        Field(max_length=20),
    ]
    parser_revision: Annotated[int, Field(ge=0, le=32767)]
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _stats_complete(self) -> "_DailyLine":
        for name, unit in _STATS:
            values = [getattr(self, f"{name}_{kind}_{unit}") for kind in ("min", "max", "mean")]
            if len({value is None for value in values}) != 1:
                raise ValueError(f"{name}: min, max and mean are all set or all empty")
        if (self.uv_max_wm2 is None) != (self.uv_mean_wm2 is None):
            raise ValueError("uv: max and mean are both set or both empty")
        return self


class _DatasetLine(_Line):
    t: Literal["dataset"]
    rover_id: Rover
    parsed_at: AwareDatetime | None
    changed_at: AwareDatetime | None
    updated_at: AwareDatetime | None
    source_max_sol: Count | None
    files_parsed: Count
    files_failed: Count
    last_error: Annotated[str, Field(max_length=2000)] | None
    imported_at: AwareDatetime | None = None
    import_meta: dict[str, Any] | None = None


class _RoverCounts(_Line):
    daily: Count
    hourly: Count


class _Meta(_Line):
    t: Literal["meta"]
    format: Literal["mars-weather-export"]
    version: Literal[1]
    schema_revision: str
    exported_at: AwareDatetime
    counts: dict[Rover, _RoverCounts]


@dataclass(frozen=True)
class _RoverData:
    daily: list[dict[str, Any]]
    hourly: list[dict[str, Any]]
    dataset: dict[str, Any] | None


@dataclass(frozen=True)
class _Export:
    meta: _Meta
    rovers: dict[str, _RoverData]


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not serializable: {type(value).__name__}")


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=_json_default, allow_nan=False, separators=(",", ":"))


def _write_gzip(path: Path, lines: list[str]) -> None:
    """Write the file next to its final name first, so a failed export leaves no half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw) as zipped:
            zipped.write(("\n".join(lines) + "\n").encode("utf-8"))
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


async def export_weather(
    session: AsyncSession, path: Path, rovers: Sequence[str] = ROVERS
) -> TransferResult:
    """Write the data of ``rovers`` to ``path`` (gzip JSON Lines); the only place that writes."""
    # One snapshot of all tables even while a parse commits sols.
    await session.rollback()
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    data = {rover: await weather_repo.export_rows(session, rover) for rover in rovers}
    await session.rollback()
    counts = {rover: (len(daily), len(hourly)) for rover, (daily, hourly, _) in data.items()}
    meta = {
        "t": "meta",
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "schema_revision": SCHEMA_REVISION,
        "exported_at": datetime.now(UTC),
        "counts": {r: {"daily": d, "hourly": h} for r, (d, h) in counts.items()},
    }
    lines = [_dumps(meta)]
    for daily, hourly, dataset in data.values():
        if dataset is not None:
            lines.append(_dumps({"t": "dataset", **dataset}))
        lines += [_dumps({"t": "daily", **row}) for row in daily]
        lines += [_dumps({"t": "hourly", **row}) for row in hourly]
    await asyncio.to_thread(_write_gzip, path, lines)
    return TransferResult("exported", counts)


def _read_lines(path: Path, limit: int) -> Iterator[tuple[int, str]]:
    """The non-empty lines of the unpacked file with their numbers, one at a time; the unpacked
    size is counted and must not pass ``limit`` bytes."""
    total = 0
    number = 0
    try:
        with gzip.open(path, "rb") as file:
            while raw := file.readline(limit - total + 1):
                total += len(raw)
                if total > limit:
                    raise WeatherImportError(f"the file is larger than {limit} bytes unpacked")
                if not raw.strip():
                    continue
                number += 1
                try:
                    yield number, raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise WeatherImportError("the file is not UTF-8 text") from exc
    except (OSError, EOFError, zlib.error) as exc:
        raise WeatherImportError(
            f"cannot read {path.name} as a gzip file: {type(exc).__name__}"
        ) from exc


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


def _parse_line(number: int, text: str) -> dict[str, Any]:
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise WeatherImportError(f"line {number}: not valid JSON") from exc
    if not isinstance(value, dict):
        raise WeatherImportError(f"line {number}: not a JSON object")
    return value


def _validate[T: BaseModel](number: int, model: type[T], data: dict[str, Any]) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise WeatherImportError(f"line {number}: invalid {where}: {first['msg']}"[:300]) from exc


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _hourly_row(number: int, data: dict[str, Any]) -> dict[str, Any]:
    """Check one ``hourly`` line without a model per row (there are ~156 thousand of them)."""
    if data.keys() != _HOURLY_KEYS:
        raise WeatherImportError(f"line {number}: wrong fields of an hourly row")
    sol, hour = data["sol"], data["hour"]
    if data["rover_id"] not in ROVERS:
        raise WeatherImportError(f"line {number}: unknown rover")
    if not (isinstance(sol, int) and not isinstance(sol, bool) and 0 <= sol <= _MAX_INT):
        raise WeatherImportError(f"line {number}: invalid sol")
    if not (isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 23):
        raise WeatherImportError(f"line {number}: invalid hour")
    coverage = data["coverage"]
    if not (_is_number(coverage) and 0 <= coverage <= 1):
        raise WeatherImportError(f"line {number}: invalid coverage")
    for name in _HOURLY_FLOATS:
        if data[name] is not None and not _is_number(data[name]):
            raise WeatherImportError(f"line {number}: invalid {name}")
    row = {key: value for key, value in data.items() if key != "t"}
    row["coverage"] = float(coverage)
    return row


def parse_export(path: Path, limit: int) -> _Export:
    """Read and validate the whole file; ``WeatherImportError`` for anything wrong with it."""
    lines = _read_lines(path, limit)
    first = next(lines, None)
    if first is None:
        raise WeatherImportError("the file is empty")
    meta = _validate(1, _Meta, _parse_line(1, first[1]))
    if meta.schema_revision != SCHEMA_REVISION:
        raise WeatherImportError(
            f"schema revision {meta.schema_revision!r} of the file differs from "
            f"{SCHEMA_REVISION!r} of this version"
        )
    daily: dict[str, list[dict[str, Any]]] = {rover: [] for rover in ROVERS}
    hourly: dict[str, list[dict[str, Any]]] = {rover: [] for rover in ROVERS}
    datasets: dict[str, dict[str, Any]] = {}
    for number, text in lines:
        data = _parse_line(number, text)
        kind = data.get("t")
        if kind == "daily":
            row = _validate(number, _DailyLine, data).model_dump(exclude={"t"})
            daily[row["rover_id"]].append(row)
        elif kind == "hourly":
            row = _hourly_row(number, data)
            hourly[row["rover_id"]].append(row)
        elif kind == "dataset":
            row = _validate(number, _DatasetLine, data).model_dump(exclude={"t"})
            if row["rover_id"] in datasets:
                raise WeatherImportError(f"line {number}: a second state of {row['rover_id']}")
            datasets[row["rover_id"]] = row
        else:
            raise WeatherImportError(f"line {number}: unknown line type")
    _check_counts(meta, daily, hourly)
    rovers = {}
    for rover in ROVERS:
        _check_rover(rover, daily[rover], hourly[rover])
        rovers[rover] = _RoverData(daily[rover], hourly[rover], datasets.get(rover))
    return _Export(meta, rovers)


def _check_counts(
    meta: _Meta, daily: dict[str, list[dict[str, Any]]], hourly: dict[str, list[dict[str, Any]]]
) -> None:
    for rover in ROVERS:
        stated = meta.counts.get(rover, _RoverCounts(daily=0, hourly=0))
        if (len(daily[rover]), len(hourly[rover])) != (stated.daily, stated.hourly):
            raise WeatherImportError(
                f"counts in meta of {rover} ({stated.daily} daily, {stated.hourly} hourly) "
                f"differ from the lines ({len(daily[rover])}, {len(hourly[rover])})"
            )


def _check_rover(rover: str, daily: list[dict[str, Any]], hourly: list[dict[str, Any]]) -> None:
    """Every sol once, with all 24 hours and no hour of a sol that has no summary."""
    sols = {row["sol"] for row in daily}
    if len(sols) != len(daily):
        raise WeatherImportError(f"a sol of {rover} occurs twice")
    keys = {(row["sol"], row["hour"]) for row in hourly}
    if len(keys) != len(hourly):
        raise WeatherImportError(f"an hour of {rover} occurs twice")
    if {sol for sol, _ in keys} != sols or len(hourly) != 24 * len(daily):
        raise WeatherImportError(f"{rover}: every sol needs exactly its 24 hours")


async def import_weather(
    session: AsyncSession, path: Path, force: bool = False, *, settings: Settings | None = None
) -> TransferResult:
    """Replace the data of the rovers in the file with the file's content.

    ``WeatherImportError``: the file is wrong (nothing is changed). ``WeatherBusy``: a parse or
    another import runs. ``WeatherImportRefused``: the file has fewer sols of a rover than
    ``WEATHER_MIN_KEEP_RATIO`` of the stored ones (unless ``force``). An import of what is
    already stored changes nothing. A rover without summaries in the file is left alone.
    """
    settings = settings or get_settings()
    parsed = await asyncio.to_thread(parse_export, path, settings.weather_import_max_bytes)
    rovers = [rover for rover in ROVERS if parsed.rovers[rover].daily]
    counts = {r: (len(parsed.rovers[r].daily), len(parsed.rovers[r].hourly)) for r in rovers}
    try:
        if not await session.scalar(select(func.pg_try_advisory_xact_lock(WEATHER_LOCK_NAMESPACE))):
            raise WeatherBusy("another weather parse or import is running")
        current = {rover: await weather_repo.export_rows(session, rover) for rover in rovers}
        if all(_same_content(parsed.rovers[r], current[r]) for r in rovers):
            await session.rollback()
            return TransferResult("unchanged", counts)
        for rover in rovers:
            _refuse_shrink(
                rover, len(parsed.rovers[rover].daily), len(current[rover][0]), force, settings
            )
        for rover in rovers:
            data = parsed.rovers[rover]
            await weather_repo.replace_rover(
                session, rover, data.daily, data.hourly, _dataset_row(parsed, rover)
            )
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return TransferResult("imported", counts)


def _refuse_shrink(rover: str, incoming: int, stored: int, force: bool, settings: Settings) -> None:
    if not force and stored and incoming < settings.weather_min_keep_ratio * stored:
        raise WeatherImportRefused(
            f"the file has {incoming} sols of {rover}, the database {stored}; "
            "use --force to replace them"
        )


def _dataset_fields(data: _RoverData) -> dict[str, Any]:
    fields = dict(data.dataset or {})
    for name in _IMPORT_OWN:
        fields.pop(name, None)
    return fields


def _dataset_row(parsed: _Export, rover: str) -> dict[str, Any]:
    data = parsed.rovers[rover]
    meta = parsed.meta
    fields = _dataset_fields(data)
    if not fields:
        newest = max(row["updated_at"] for row in data.daily)
        fields = {
            "rover_id": rover,
            "updated_at": newest,
            "changed_at": newest,
            "source_max_sol": max(row["sol"] for row in data.daily),
        }
    own = {
        "imported_at": datetime.now(UTC),
        "import_meta": {
            "exported_at": meta.exported_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "format_version": meta.version,
            "schema_revision": meta.schema_revision,
            "daily": len(data.daily),
            "hourly": len(data.hourly),
        },
    }
    return {**fields, **own}


def _same_content(
    data: _RoverData,
    stored: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None],
) -> bool:
    """The stored rows already hold what the file holds (the import marks aside)."""
    daily, hourly, dataset = stored
    if daily != sorted(data.daily, key=lambda row: row["sol"]):
        return False
    if hourly != sorted(data.hourly, key=lambda row: (row["sol"], row["hour"])):
        return False
    if data.dataset is None:
        return True
    held = {k: v for k, v in (dataset or {}).items() if k not in _IMPORT_OWN}
    return held == _dataset_fields(data)
