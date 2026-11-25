"""Move the RAD data between databases with a file: ``rad export`` and ``rad import``.

The history is loaded on a home computer (hours of downloads) and carried to the server as one
small file: gzip-compressed JSON Lines. The first line is ``meta`` (format, version, schema
revision, counts, the state of the dataset with the validators of the listings), then one
``file`` line per ``rad_files`` row, one ``daily`` line per ``rad_daily`` row and one
``hourly`` line per ``rad_hourly`` row (version 2, stage 22); times are ISO 8601 UTC. The import
checks everything before it touches the database (a file of another schema revision is
refused), then replaces the four tables in one transaction under the RAD advisory lock.
"""

import asyncio
import gzip
import json
import logging
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.repositories import rad as rad_repo
from app.services.rad_sync import MIN_LIVE_SECONDS, RAD_LOCK_NAMESPACE

logger = logging.getLogger(__name__)

EXPORT_FORMAT = "mars-rad-export"
EXPORT_VERSION = 2
SCHEMA_REVISION = "0021"
# Columns of the state row that an import sets itself.
_IMPORT_OWN = ("imported_at", "import_meta")

# A daily file of the volume, as in the listing (also the pattern of ``source_path``).
VOLUME_PATH = r"^DATA/SOL_\d{5}_\d{5}/RAD_RDR_[A-Za-z0-9_]+\.TXT$"
VolumePath = Annotated[str, Field(pattern=VOLUME_PATH, max_length=200)]
Float = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, le=2**63 - 1)]


class RadTransferError(Exception):
    """Base of the errors of export and import."""


class RadImportError(RadTransferError):
    """The file cannot be imported (wrong format, damaged, invalid rows): nothing changed."""


class RadImportRefused(RadTransferError):
    """The file has far fewer summaries than the database; ``force`` imports it anyway."""


class RadBusy(RadTransferError):
    """Another ``rad sync`` or ``rad import`` holds the lock."""


@dataclass(frozen=True)
class TransferResult:
    status: Literal["exported", "imported", "unchanged"]
    files: int
    daily: int
    hourly: int = 0


class _Line(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FileLine(_Line):
    t: Literal["file"]
    path: VolumePath
    sol: Annotated[int, Field(ge=1, le=2**31 - 1)]
    md5: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")] | None
    status: Literal["loaded", "duplicate", "empty", "failed", "md5_mismatch"]
    role: Literal["primary", "extra"] | None
    duplicate_of: VolumePath | None
    dup_check: Literal["tail", "size"] | None
    size_bytes: Count | None
    creation_time: AwareDatetime | None
    rdr_version: Annotated[int, Field(ge=0, le=32767)] | None
    blocks: Count
    dose_obs: Count
    timing_listed: bool
    error: Annotated[str, Field(max_length=500)] | None
    processed_at: AwareDatetime

    @model_validator(mode="after")
    def _md5_present(self) -> "_FileLine":
        if (self.md5 is None) != (self.status in rad_repo.BAD_STATUSES):
            raise ValueError("md5 must be empty exactly for failed and md5_mismatch files")
        return self


class _DailyLine(_Line):
    t: Literal["daily"]
    sol: Annotated[int, Field(ge=1, le=2**31 - 1)]
    earth_date: date
    dose_b_mean: Float
    dose_b_min: Float
    dose_b_max: Float
    dose_e_mean: Float
    dose_e_min: Float
    dose_e_max: Float
    bu_count: Count
    live_s: Annotated[float, Field(ge=MIN_LIVE_SECONDS, le=90000, allow_inf_nan=False)]
    n_used: Annotated[int, Field(gt=0)]
    n_dropped_qc: Count
    n_dropped_low_rate: Count
    n_dropped_invalid: Count
    qc_bits: Annotated[int, Field(ge=0, le=255)]
    timing_caveat: bool | None
    source_path: VolumePath
    updated_at: AwareDatetime
    hourly_revision: Annotated[int, Field(ge=0, le=32767)]
    hourly_reject: Literal["overlap", "off_sol"] | None
    hourly_dropped: Annotated[int, Field(ge=0, le=32767)]


class _HourlyLine(_Line):
    t: Literal["hourly"]
    sol: Annotated[int, Field(ge=1, le=2**31 - 1)]
    hour: Annotated[int, Field(ge=0, le=23)]
    n_obs: Annotated[int, Field(gt=0, le=32767)]
    live_s: Annotated[float, Field(ge=MIN_LIVE_SECONDS, le=3698.97 * 1.01, allow_inf_nan=False)]
    dose_b_mean: Float
    dose_e_mean: Float
    bu_count: Count


class _DatasetState(_Line):
    checked_at: AwareDatetime | None
    succeeded_at: AwareDatetime | None
    changed_at: AwareDatetime | None
    checksums_etag: str | None
    checksums_last_modified: str | None
    checksums_sha256: str | None
    timing_etag: str | None
    timing_last_modified: str | None
    timing_sha256: str | None
    files_listed: Count | None
    last_error: str | None
    imported_at: AwareDatetime | None = None
    import_meta: dict[str, Any] | None = None


class _Counts(_Line):
    file: Count
    daily: Count
    hourly: Count


class _Meta(_Line):
    t: Literal["meta"]
    format: Literal["mars-rad-export"]
    version: Literal[2]
    schema_revision: str
    exported_at: AwareDatetime
    counts: _Counts
    dataset: _DatasetState | None


@dataclass(frozen=True)
class _Export:
    meta: _Meta
    files: list[dict[str, Any]]
    daily: list[dict[str, Any]]
    hourly: list[dict[str, Any]]


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


async def export_rad(session: AsyncSession, path: Path) -> TransferResult:
    """Write the three tables to ``path`` (gzip JSON Lines); the only place that writes a file."""
    # One snapshot of the three tables even while a sync commits sols.
    await session.rollback()
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    files, daily, hourly, dataset = await rad_repo.export_rows(session)
    await session.rollback()
    state = None
    if dataset is not None:
        state = {k: v for k, v in dataset.items() if k != "id"}
    meta = {
        "t": "meta",
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "schema_revision": SCHEMA_REVISION,
        "exported_at": datetime.now(UTC),
        "counts": {"file": len(files), "daily": len(daily), "hourly": len(hourly)},
        "dataset": state,
    }
    lines = [_dumps(meta)]
    lines += [_dumps({"t": "file", **row}) for row in files]
    lines += [_dumps({"t": "daily", **row}) for row in daily]
    lines += [_dumps({"t": "hourly", **row}) for row in hourly]
    await asyncio.to_thread(_write_gzip, path, lines)
    return TransferResult("exported", len(files), len(daily), len(hourly))


def _read_text(path: Path, limit: int) -> str:
    try:
        with gzip.open(path, "rb") as file:
            data = file.read(limit + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise RadImportError(
            f"cannot read {path.name} as a gzip file: {type(exc).__name__}"
        ) from exc
    if len(data) > limit:
        raise RadImportError(f"the file is larger than {limit} bytes unpacked")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RadImportError("the file is not UTF-8 text") from exc


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


def _parse_line(number: int, text: str) -> dict[str, Any]:
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise RadImportError(f"line {number}: not valid JSON") from exc
    if not isinstance(value, dict):
        raise RadImportError(f"line {number}: not a JSON object")
    return value


def _validate[T: BaseModel](number: int, model: type[T], data: dict[str, Any]) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise RadImportError(f"line {number}: invalid {where}: {first['msg']}"[:300]) from exc


def parse_export(path: Path, limit: int) -> _Export:
    """Read and validate the whole file; ``RadImportError`` for anything wrong with it."""
    lines = [line for line in _read_text(path, limit).splitlines() if line.strip()]
    if not lines:
        raise RadImportError("the file is empty")
    meta = _validate(1, _Meta, _parse_line(1, lines[0]))
    if meta.schema_revision != SCHEMA_REVISION:
        raise RadImportError(
            f"schema revision {meta.schema_revision!r} of the file differs from "
            f"{SCHEMA_REVISION!r} of this version"
        )
    files: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    hourly: list[dict[str, Any]] = []
    for number, text in enumerate(lines[1:], start=2):
        data = _parse_line(number, text)
        kind = data.get("t")
        if kind == "file":
            files.append(_validate(number, _FileLine, data).model_dump(exclude={"t"}))
        elif kind == "daily":
            daily.append(_validate(number, _DailyLine, data).model_dump(exclude={"t"}))
        elif kind == "hourly":
            hourly.append(_validate(number, _HourlyLine, data).model_dump(exclude={"t"}))
        else:
            raise RadImportError(f"line {number}: unknown line type")
    counts = (meta.counts.file, meta.counts.daily, meta.counts.hourly)
    if (len(files), len(daily), len(hourly)) != counts:
        raise RadImportError(
            f"counts in meta ({meta.counts.file} files, {meta.counts.daily} daily, "
            f"{meta.counts.hourly} hourly) differ from the lines "
            f"({len(files)}, {len(daily)}, {len(hourly)})"
        )
    _check_unique(files, "path")
    _check_unique(daily, "sol")
    _check_hourly(daily, hourly)
    paths = {row["path"] for row in files}
    if any(row["source_path"] not in paths for row in daily):
        raise RadImportError("a daily row names a source file that is not in the file list")
    return _Export(meta, files, daily, hourly)


def _check_hourly(daily: list[dict[str, Any]], hourly: list[dict[str, Any]]) -> None:
    """Every hour belongs to a sol of the file whose hours were calculated and not refused, and
    no hour occurs twice."""
    if len({(row["sol"], row["hour"]) for row in hourly}) != len(hourly):
        raise RadImportError("an hour occurs twice")
    accepted = {
        row["sol"] for row in daily if row["hourly_revision"] > 0 and row["hourly_reject"] is None
    }
    if any(row["sol"] not in accepted for row in hourly):
        raise RadImportError("an hourly row belongs to a sol without calculated hours")
    in_hours: dict[int, int] = {}
    for row in hourly:
        in_hours[row["sol"]] = in_hours.get(row["sol"], 0) + row["n_obs"]
    for row in daily:
        if row["hourly_revision"] == 0:
            continue
        counted = 0 if row["hourly_reject"] is not None else in_hours.get(row["sol"], 0)
        if counted + row["hourly_dropped"] != row["n_used"]:
            raise RadImportError(f"sol {row['sol']}: the hours do not add up to n_used")


def _check_unique(rows: list[dict[str, Any]], key: str) -> None:
    if len({row[key] for row in rows}) != len(rows):
        raise RadImportError(f"a {key} occurs twice")


async def import_rad(
    session: AsyncSession, path: Path, force: bool = False, *, settings: Settings | None = None
) -> TransferResult:
    """Replace the content of the three tables with the file's.

    ``RadImportError``: the file is wrong (nothing is changed). ``RadBusy``: a sync or another
    import runs. ``RadImportRefused``: the file has fewer summaries than ``RAD_MIN_KEEP_RATIO``
    of the stored ones (unless ``force``). An import of what is already stored changes nothing.
    """
    settings = settings or get_settings()
    parsed = await asyncio.to_thread(parse_export, path, settings.rad_import_max_bytes)
    try:
        if not await session.scalar(select(func.pg_try_advisory_xact_lock(RAD_LOCK_NAMESPACE))):
            raise RadBusy("another RAD sync or import is running")
        current = await rad_repo.export_rows(session)
        current_files, current_daily, current_hourly, _ = current
        if _same_content(parsed, *current):
            await session.rollback()
            return TransferResult(
                "unchanged", len(current_files), len(current_daily), len(current_hourly)
            )
        shrinks = len(parsed.daily) < settings.rad_min_keep_ratio * len(current_daily)
        if not force and current_daily and shrinks:
            raise RadImportRefused(
                f"the file has {len(parsed.daily)} sols, the database {len(current_daily)}; "
                "use --force to replace them"
            )
        await rad_repo.replace_all(
            session, parsed.files, parsed.daily, parsed.hourly, _dataset_row(parsed)
        )
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return TransferResult("imported", len(parsed.files), len(parsed.daily), len(parsed.hourly))


def _dataset_fields(parsed: _Export) -> dict[str, Any]:
    state = parsed.meta.dataset
    fields = state.model_dump() if state is not None else {}
    for name in _IMPORT_OWN:
        fields.pop(name, None)
    return fields


def _dataset_row(parsed: _Export) -> dict[str, Any]:
    meta = parsed.meta
    own = {
        "imported_at": datetime.now(UTC),
        "import_meta": {
            "exported_at": meta.exported_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "format_version": meta.version,
            "schema_revision": meta.schema_revision,
            "files": meta.counts.file,
            "daily": meta.counts.daily,
            "hourly": meta.counts.hourly,
        },
    }
    return {"id": rad_repo.DATASET_ID, **_dataset_fields(parsed), **own}


def _same_content(
    parsed: _Export,
    files: list[dict[str, Any]],
    daily: list[dict[str, Any]],
    hourly: list[dict[str, Any]],
    dataset: dict[str, Any] | None,
) -> bool:
    """The stored tables already hold what the file holds (the import marks aside)."""
    if files != sorted(parsed.files, key=lambda row: row["path"]):
        return False
    if daily != sorted(parsed.daily, key=lambda row: row["sol"]):
        return False
    if hourly != sorted(parsed.hourly, key=lambda row: (row["sol"], row["hour"])):
        return False
    stored = {k: v for k, v in (dataset or {}).items() if k not in ("id", *_IMPORT_OWN)}
    return stored == _dataset_fields(parsed)
