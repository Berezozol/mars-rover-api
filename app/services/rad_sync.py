"""Load the MSL RAD daily summaries from the PDS PPI volume (stage 21).

``RadLoader.run`` reads the two listings of the volume (``CHECKSUMS.TXT`` and
``TIMING_ERRORS.TXT``, conditional requests), plans the sols whose files are new or changed
and loads them one sol at a time: the daily files of the sol are streamed through
``RadParser`` (nothing is written to disk), checked against the md5 of the listing, combined
into one summary and stored together with the state of the files in ONE transaction. An
interrupted run leaves no half-loaded sol and the next run continues where it stopped.

Several files of one sol (the volume has up to 17): the last one by name is read in full;
every other one is asked for its last 64 KiB (``Range``) and is a duplicate when the size and
the tail are the same as the main file's; only a file that differs is read in full and merged
into the sol by observation (docs/proposals/stage-21-rad.md, ADR-3).

The whole run holds the advisory lock ``RAD_LOCK_NAMESPACE`` (shared with ``rad import``).

``run(workers=N)`` (1..``MAX_WORKERS``) loads up to N sols at once: each worker takes the next
sol of the plan, in order, and stores it in its own session and transaction. The request pause
of the client still spaces the starts of the requests; ``_ServerDown`` in one worker cancels
the others (their transactions are rolled back); a failed sol does not stop the rest.
"""

import asyncio
import hashlib
import logging
import math
import statistics
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.mars_time import HOUR_LIVE_S, local_mean_time
from app.db.models import RadDataset
from app.db.models import RadFile as RadFileRow
from app.db.session import create_session_factory
from app.repositories import rad as rad_repo
from app.sources.http import (
    ConditionalBody,
    IncompleteResponse,
    RequestStats,
    ResponseRejected,
    TailProbe,
    UpstreamClient,
    UpstreamError,
)
from app.sources.rad import (
    TAIL_BYTES,
    ChecksumEntry,
    RadFile,
    RadFormatError,
    RadObservation,
    RadParser,
    file_name,
    parse_checksums,
    parse_qc,
    parse_timing_errors,
)

logger = logging.getLogger(__name__)

# First key of the advisory lock of ``rad sync`` and ``rad import`` (the samples use 7_250_003).
RAD_LOCK_NAMESPACE = 7_250_004

EXIT_OK = 0
EXIT_REMAINING = 1
EXIT_USAGE = 2
EXIT_SERVER_DOWN = 3
# The most sols loaded at once (``rad sync --workers``).
MAX_WORKERS = 8

CHECKSUMS_NAME = "CHECKSUMS.TXT"
TIMING_NAME = "DOCUMENT/TIMING_ERRORS.TXT"
MIN_LIVE_SECONDS = 1.0
MAX_LIVE_SECONDS = 7200.0
# Pauses between the full attempts at one file (the last one is repeated).
RETRY_PAUSES = (30.0, 120.0)
# The date of a file header is trusted when it is within this of the observations' median.
DATE_TOLERANCE = timedelta(days=2)
MAX_ERROR_LENGTH = 500
# Hours (stage 22, docs/proposals/sol-conditions.md): the east longitude of Curiosity and the
# local day number (floor of MSD + lon / 360) of sol 0.
CURIOSITY_LON_E = 137.4
CURIOSITY_SOL0_MSD = 49269
# An observation may start this much (s) before the end of the last accepted one without being
# a conflicting one.
OVERLAP_TOLERANCE_S = 60.0
# More conflicting observations than this share of the sol's observations refuse its hours.
MAX_OVERLAP_DROPPED_SHARE = 0.10
# The share of the used observations that must fall into the sol (by UTC) for it to have hours.
MIN_ON_SOL_SHARE = 0.5
# A dose of B or E below this (uGy/h) at QC 0 is not plausible (the norm is 6.6-13.1).
MIN_DOSE_UGY_H = 1.0
# ``HOUR_LIVE_S`` (an hour of LMST in SI seconds) lives in ``app.core.mars_time``; re-exported.
__all__ = ["HOUR_LIVE_S"]
MAX_SMALLINT = 32767
# The revision of the hourly calculation; ``rad sync --reprocess`` redoes sols below it.
RAD_HOURLY_REVISION = 2

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class SolSummary:
    """One sol of dosimetry: doses in uGy/h, the live time and the counts of its observations."""

    dose_b_mean: float
    dose_b_min: float
    dose_b_max: float
    dose_e_mean: float
    dose_e_min: float
    dose_e_max: float
    bu_count: int
    live_s: float
    used: int
    dropped_qc: int
    dropped_low_rate: int
    dropped_invalid: int
    qc_bits: int
    # Start times (UTC) of the used observations that have a readable one.
    used_utc: tuple[datetime, ...] = ()
    # The used observations themselves (the input of ``combine_hours``).
    used_observations: tuple[RadObservation, ...] = ()


@dataclass(frozen=True)
class HourSummary:
    """One hour (LMST) of a sol: observations, live time, doses in uGy/h weighted by live time
    and the BU count (the rate is ``bu_count / live_s``)."""

    hour: int
    n_obs: int
    live_s: float
    dose_b_mean: float
    dose_e_mean: float
    bu_count: int


@dataclass(frozen=True)
class HourlyResult:
    """The hours of a sol, how many used observations are not in them and, if the hours of the
    sol were refused, why (then ``hours`` is empty and ``dropped`` is all of the used)."""

    hours: list[HourSummary]
    dropped: int
    reject: Literal["overlap", "off_sol"] | None


@dataclass
class RadRunResult:
    """What one ``rad sync`` did; ``exit_code`` is the process exit code."""

    exit_code: int = EXIT_OK
    locked: bool = False
    not_modified: bool = False
    stopped: str | None = None
    sols_planned: int = 0
    sols_loaded: int = 0
    sols_failed: list[int] = field(default_factory=list)
    files_loaded: int = 0
    files_duplicate: int = 0
    files_empty: int = 0
    files_failed: int = 0
    files_deleted: int = 0
    sols_deleted: int = 0
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    # HTTP requests of the run (retries and redirect hops included) and the body bytes read.
    requests_get: int = 0
    requests_probe: int = 0
    requests_conditional: int = 0
    bytes_received: int = 0
    slowest_wait: float = 0.0


class _ServerDown(Exception):
    """The server kept failing after the attempts and the cooldown: stop the run."""


class _FileFailed(Exception):
    """A file cannot be loaded (``failed``) or arrived damaged (``md5_mismatch``)."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _ListingFailed(Exception):
    def __init__(self, name: str, cause: UpstreamError) -> None:
        super().__init__(f"{name}: {cause}")
        self.cause = cause


# --- combining observations ---------------------------------------------------------------


def _is_complete(obs: RadObservation) -> bool:
    """Both doses are finite numbers >= 0, the live time is 1..7200 s, the BU count >= 0."""
    doses = (obs.dose_b, obs.dose_e)
    if any(d is None or not math.isfinite(d) or d < 0 for d in doses):
        return False
    live = obs.live_s
    if live is None or not math.isfinite(live) or not MIN_LIVE_SECONDS <= live <= MAX_LIVE_SECONDS:
        return False
    return obs.bu_count is not None and obs.bu_count >= 0


def _rate(obs: RadObservation) -> float:
    return (obs.bu_count or 0) / (obs.live_s or 1.0)


def combine_sol(
    observations: Sequence[RadObservation], low_rate_ratio: float, *, repeated: int = 0
) -> SolSummary | None:
    """The summary of a sol, or ``None`` when no observation is usable.

    An observation is dropped as ``dropped_invalid`` (unreadable QC, missing or odd numbers),
    ``dropped_qc`` (QC bit 0: possible quality problems) or ``dropped_low_rate`` (the rate of
    detector B below ``low_rate_ratio`` of the median of the sol: the instrument hardly
    counted). After that rule an observation whose dose B or E is below ``MIN_DOSE_UGY_H`` is
    ``dropped_invalid`` too (an implausible dose with a clean QC; stage 22). QC bits 2 (DAN)
    and 3 (Solar Event) do not drop anything and are reported in ``qc_bits``. Means are
    weighted by the live time. ``repeated`` observations that were merged away for sharing a
    start time inside one file are added to ``dropped_invalid``.
    """
    dropped_qc = 0
    dropped_invalid = repeated
    passed: list[tuple[RadObservation, int]] = []
    for obs in observations:
        qc = parse_qc(obs.qc_raw)
        if qc is None:
            dropped_invalid += 1
        elif qc & 1:
            dropped_qc += 1
        elif not _is_complete(obs):
            dropped_invalid += 1
        else:
            passed.append((obs, qc))
    median = statistics.median(_rate(obs) for obs, _ in passed) if passed else 0.0
    fast = [(obs, qc) for obs, qc in passed if _rate(obs) >= low_rate_ratio * median]
    kept = [(obs, qc) for obs, qc in fast if _is_plausible(obs)]
    if not kept:
        return None
    return _summarize(
        [obs for obs, _ in kept],
        qc_bits=_or_all(qc for _, qc in kept),
        dropped=(dropped_qc, len(passed) - len(fast), dropped_invalid + len(fast) - len(kept)),
    )


def _is_plausible(obs: RadObservation) -> bool:
    """Both doses are at least ``MIN_DOSE_UGY_H`` (the observation is already complete)."""
    return (obs.dose_b or 0.0) >= MIN_DOSE_UGY_H and (obs.dose_e or 0.0) >= MIN_DOSE_UGY_H


def _or_all(values: Any) -> int:
    bits = 0
    for value in values:
        bits |= value
    return bits


def _summarize(
    used: Sequence[RadObservation], *, qc_bits: int, dropped: tuple[int, int, int]
) -> SolSummary:
    live = sum(o.live_s or 0.0 for o in used)
    b = [o.dose_b or 0.0 for o in used]
    e = [o.dose_e or 0.0 for o in used]
    weights = [o.live_s or 0.0 for o in used]
    return SolSummary(
        dose_b_mean=sum(d * w for d, w in zip(b, weights, strict=True)) / live,
        dose_b_min=min(b),
        dose_b_max=max(b),
        dose_e_mean=sum(d * w for d, w in zip(e, weights, strict=True)) / live,
        dose_e_min=min(e),
        dose_e_max=max(e),
        bu_count=sum(o.bu_count or 0 for o in used),
        live_s=live,
        used=len(used),
        dropped_qc=dropped[0],
        dropped_low_rate=dropped[1],
        dropped_invalid=dropped[2],
        qc_bits=qc_bits,
        used_utc=tuple(o.start_utc for o in used if o.start_utc is not None),
        used_observations=tuple(used),
    )


# --- the hours of a sol ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Timed:
    """A used observation with its start, duration (s) and, when it is in the sol, its hour."""

    obs: RadObservation
    start: datetime
    duration: float
    hour: int | None


def _duration(obs: RadObservation) -> float:
    """``OBS_DURATION``; the early format has none, then the live time."""
    return obs.obs_duration_s or obs.live_s or 0.0


def _timed(obs: RadObservation, file_sol: int) -> _Timed | None:
    """The observation placed by the UTC of its middle (LMST at ``CURIOSITY_LON_E``), or ``None``
    without a readable start. ``hour`` is ``None`` when the middle falls into another sol."""
    if obs.start_utc is None:
        return None
    duration = _duration(obs)
    try:
        middle = obs.start_utc + timedelta(seconds=duration / 2)
        day, seconds = local_mean_time(middle, CURIOSITY_LON_E)
    except (OverflowError, ValueError):
        return None
    hour = min(int(seconds // 3600), 23) if day - CURIOSITY_SOL0_MSD == file_sol else None
    return _Timed(obs, obs.start_utc, duration, hour)


def _split_conflicts(timed: Sequence[_Timed]) -> tuple[list[_Timed], list[_Timed]]:
    """Split into (accepted, conflicting) in one pass over the observations sorted by start.

    Equal starts are ordered by the block number (``RadObservation.index``). An
    observation conflicts when it starts earlier than the last *accepted* one ends, less
    ``OVERLAP_TOLERANCE_S``; a conflicting one never becomes the last accepted.
    """
    accepted: list[_Timed] = []
    conflicting: list[_Timed] = []
    for item in sorted(timed, key=lambda t: (t.start, t.obs.index)):
        if accepted:
            last = accepted[-1]
            gap = (item.start - last.start).total_seconds()
            if gap < last.duration - OVERLAP_TOLERANCE_S:
                conflicting.append(item)
                continue
        accepted.append(item)
    return accepted, conflicting


def combine_hours(used: Sequence[RadObservation], file_sol: int) -> HourlyResult:
    """The hours (LMST) of the sol ``file_sol`` from its used observations.

    An observation belongs to the hour of its middle (start + duration / 2 by UTC). Those whose
    middle is in another sol or whose start is unreadable are ``dropped`` (they stay in the
    daily summary). The hours of the whole sol are refused (``reject``) when fewer than
    ``MIN_ON_SOL_SHARE`` of the used observations are in the sol (``off_sol``). Then the
    observations of the sol that overlap an accepted one by more than ``OVERLAP_TOLERANCE_S``
    (``_split_conflicts``) are left out of the hours and counted in ``dropped``; when they are
    more than ``MAX_OVERLAP_DROPPED_SHARE`` of the observations of the sol, the hours are
    refused too (``overlap``): two interleaved streams, the times are contradictory.
    """
    placed = [item for obs in used if (item := _timed(obs, file_sol)) is not None]
    on_sol = [item for item in placed if item.hour is not None]
    if len(on_sol) < MIN_ON_SOL_SHARE * len(used):
        return HourlyResult([], len(used), "off_sol")
    accepted, conflicting = _split_conflicts(on_sol)
    if len(conflicting) > Fraction(str(MAX_OVERLAP_DROPPED_SHARE)) * len(on_sol):
        return HourlyResult([], len(used), "overlap")
    by_hour: dict[int, list[RadObservation]] = defaultdict(list)
    for item in accepted:
        by_hour[item.hour or 0].append(item.obs)
    hours = [_hour_summary(hour, group) for hour, group in sorted(by_hour.items())]
    return HourlyResult(hours, len(used) - len(accepted), None)


def _hour_summary(hour: int, group: Sequence[RadObservation]) -> HourSummary:
    live = sum(o.live_s or 0.0 for o in group)
    weighted_b = sum((o.dose_b or 0.0) * (o.live_s or 0.0) for o in group)
    weighted_e = sum((o.dose_e or 0.0) * (o.live_s or 0.0) for o in group)
    return HourSummary(
        hour=hour,
        n_obs=len(group),
        live_s=live,
        dose_b_mean=weighted_b / live,
        dose_e_mean=weighted_e / live,
        bu_count=sum(o.bu_count or 0 for o in group),
    )


def merge_files(files: Sequence[tuple[str, RadFile]]) -> list[RadObservation]:
    """The observations of several files of one sol, one per key (``START_SOL``,
    ``START_OBS_UTC`` as written in the file): for a key present in several files (or twice
    in one) the file with the later ``CREATION_TIME`` wins, on a tie the greater path.
    Observations without a start time are never merged."""
    return merge_files_counted(files)[0]


def merge_files_counted(
    files: Sequence[tuple[str, RadFile]],
) -> tuple[list[RadObservation], int]:
    """``merge_files`` and the number of observations lost to a start time that repeats inside
    one file (those are unreadable data: ``combine_sol`` counts them as ``dropped_invalid``)."""
    epoch = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(files, key=lambda item: (item[1].creation_time or epoch, item[0]))
    merged: dict[tuple[Any, ...], RadObservation] = {}
    origin: dict[tuple[Any, ...], str] = {}
    repeated = 0
    for path, parsed in ordered:
        for obs in parsed.observations:
            if obs.start_utc_raw is None:
                merged[(path, obs.index)] = obs
                continue
            key = (parsed.start_sol, obs.start_utc_raw)
            if origin.get(key) == path:
                repeated += 1
            merged[key] = obs
            origin[key] = path
    return list(merged.values()), repeated


def choose_earth_date(start_time: datetime | None, used_utc: Sequence[datetime]) -> date | None:
    """The Earth date of a sol: the UTC date of the file's ``START_TIME``; if that is more than
    2 days from the median start of the used observations (the field is sometimes garbage), the
    date of the earliest used observation within 2 days of the median."""
    if not used_utc:
        return start_time.date() if start_time is not None else None
    stamps = sorted(used_utc)
    median = datetime.fromtimestamp(
        statistics.median(moment.timestamp() for moment in stamps), tz=UTC
    )
    if start_time is not None and abs(start_time - median) <= DATE_TOLERANCE:
        return start_time.date()
    near = [moment for moment in stamps if abs(moment - median) <= DATE_TOLERANCE]
    return (near[0] if near else median).date()


# --- the loader ---------------------------------------------------------------------------


@asynccontextmanager
async def advisory_lock(engine: AsyncEngine) -> AsyncIterator[bool]:
    """Hold the RAD advisory lock on a connection of its own; yields whether it was free."""
    async with engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        statement = select(func.pg_try_advisory_lock(RAD_LOCK_NAMESPACE))
        acquired = bool(await connection.scalar(statement))
        try:
            yield acquired
        finally:
            if acquired:
                await connection.execute(select(func.pg_advisory_unlock(RAD_LOCK_NAMESPACE)))


@dataclass(frozen=True)
class _State:
    """Plain values of the ``rad_dataset`` row."""

    checksums_etag: str | None = None
    checksums_last_modified: str | None = None
    checksums_sha256: str | None = None
    timing_etag: str | None = None
    timing_last_modified: str | None = None
    timing_sha256: str | None = None
    files_listed: int | None = None

    @classmethod
    def of(cls, row: RadDataset | None) -> "_State":
        if row is None:
            return cls()
        return cls(
            row.checksums_etag,
            row.checksums_last_modified,
            row.checksums_sha256,
            row.timing_etag,
            row.timing_last_modified,
            row.timing_sha256,
            row.files_listed,
        )


@dataclass
class _Timing:
    """The timing listing as far as it is known: the set of names, or ``None``."""

    names: set[str] | None = None
    response: ConditionalBody | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass
class _SolWork:
    """The files of one sol, read and judged, ready to be stored."""

    sol: int
    primary: ChecksumEntry
    rows: list[dict[str, Any]] = field(default_factory=list)
    parsed: list[tuple[str, RadFile]] = field(default_factory=list)
    earth_date: date | None = None
    failed: bool = False
    hourly: HourlyResult | None = None


def _is_transient(exc: BaseException) -> bool:
    """Worth another attempt: a broken or timed out transfer, a 429 or a 5xx."""
    if isinstance(exc, IncompleteResponse | TimeoutError):
        return True
    if isinstance(exc, ResponseRejected) or not isinstance(exc, UpstreamError):
        return False
    return exc.status is None or exc.status == 429 or exc.status >= 500


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]


def _check_range(from_sol: int | None, to_sol: int | None) -> None:
    for value in (from_sol, to_sol):
        if value is not None and value < 1:
            raise ValueError("sols start at 1")
    if from_sol is not None and to_sol is not None and from_sol > to_sol:
        raise ValueError("--from-sol must not be greater than --to-sol")


async def _default_sleep(seconds: float) -> None:
    """``asyncio.sleep`` looked up when it is called (tests replace it)."""
    await asyncio.sleep(seconds)


class RadLoader:
    """Loads the RAD summaries; ``run`` never raises for a source failure (see the exit code).

    ``client`` must be built for this source: ``min_interval`` = ``RAD_PAUSE_SECONDS`` and
    ``timeout`` = ``RAD_READ_TIMEOUT_SECONDS`` (``build_rad_client``). The file attempts are
    counted here (``RAD_FILE_ATTEMPTS``), so file requests are sent with ``max_retries=0``.
    ``sleep`` serves the pauses between attempts and the cooldown.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        client: UpstreamClient,
        settings: Settings,
        *,
        sleep: Sleep | None = None,
    ) -> None:
        self._engine = engine
        self._factory = create_session_factory(engine)
        self._client = client
        self._settings = settings
        self._sleep: Sleep = sleep or _default_sleep
        # Set (unset event) while a cooldown after repeated failures runs: all workers wait.
        self._cooling: asyncio.Event | None = None
        self._base = settings.rad_base_url.rstrip("/") + "/"

    async def run(
        self,
        from_sol: int | None = None,
        to_sol: int | None = None,
        reprocess: bool = False,
        workers: int = 1,
    ) -> RadRunResult:
        """Load the sols in ``[from_sol, to_sol]`` (all without bounds; only then do files that
        left the listing disappear). Exit codes: 0 done, 1 some files stay unloaded, 2 the lock
        is taken, 3 the server stayed unavailable.

        ``reprocess``: instead of the files that are new or changed, read again the ``loaded``
        files of the sols whose ``hourly_revision`` is below ``RAD_HOURLY_REVISION`` (no probes
        of duplicates, nothing forgotten, the listing validators are left as they were). Every
        sol is one transaction, so an interrupted run is continued by running it again.

        ``workers``: how many sols are loaded at the same time (1..``MAX_WORKERS``)."""
        _check_range(from_sol, to_sol)
        if not 1 <= workers <= MAX_WORKERS:
            raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
        async with advisory_lock(self._engine) as acquired:
            if not acquired:
                logger.warning("Another RAD sync or import is running; nothing done")
                return RadRunResult(exit_code=EXIT_USAGE, locked=True)
            result = RadRunResult()
            before = self._client.stats.snapshot()
            try:
                await self._run(result, from_sol, to_sol, reprocess, workers)
            except _ListingFailed as exc:
                transient = _is_transient(exc.cause)
                result.exit_code = EXIT_SERVER_DOWN if transient else EXIT_REMAINING
                result.stopped = "listing"
                result.error = _describe(exc)
            except _ServerDown as exc:
                result.exit_code = EXIT_SERVER_DOWN
                result.stopped = "server_down"
                result.error = _describe(exc)
            self._count_requests(result, before)
            if result.stopped is not None:
                await self._record_failure(result)
            return result

    def _count_requests(self, result: RadRunResult, before: RequestStats) -> None:
        now = self._client.stats
        result.requests_get = now.gets - before.gets
        result.requests_probe = now.probes - before.probes
        result.requests_conditional = now.conditional - before.conditional
        result.bytes_received = now.bytes_received - before.bytes_received
        result.slowest_wait = now.slowest_wait
        logger.info(
            "RAD sync requests: %d full, %d range, %d conditional; %d bytes; slowest wait %.1f s",
            result.requests_get,
            result.requests_probe,
            result.requests_conditional,
            result.bytes_received,
            result.slowest_wait,
        )

    async def _run(
        self,
        result: RadRunResult,
        lo: int | None,
        hi: int | None,
        reprocess: bool,
        workers: int = 1,
    ) -> None:
        now = datetime.now(UTC)
        pending: list[int] = []
        async with self._factory() as session:
            state = _State.of(await rad_repo.get_dataset(session))
            known = {row.path: row for row in await rad_repo.list_files(session)}
            if reprocess:
                pending = await rad_repo.sols_below_hourly_revision(
                    session, RAD_HOURLY_REVISION, lo, hi
                )
        if reprocess:
            conditional = bool(state.checksums_etag or state.checksums_last_modified)
        else:
            conditional = self._can_use_validators(state, known, lo, hi)
        response = await self._fetch(
            CHECKSUMS_NAME,
            state.checksums_etag if conditional else None,
            state.checksums_last_modified if conditional else None,
        )
        entries = None if response.not_modified else self._listing_entries(response)
        groups = _group(entries or [])
        if reprocess:
            groups = _reprocess_groups(pending, known, groups)
            todo = list(groups)
        else:
            todo = _plan(groups, known, lo, hi) if entries is not None else []
        result.sols_planned = len(todo)
        result.not_modified = response.not_modified
        timing = await self._fetch_timing(state, conditional_ok=not todo)
        if timing.error:
            result.notes.append(timing.error)
        if timing.names is not None and timing.response and not timing.response.not_modified:
            await self._refresh_timing_flags(timing.names)
        await self._load_sols(result, todo, groups, known, timing.names, reprocess, workers)
        result.sols_failed.sort()
        if entries is not None and lo is None and hi is None and not reprocess:
            await self._prune(result, known, groups, len(entries))
        if result.sols_failed or result.files_failed:
            result.exit_code = EXIT_REMAINING
            listed = ", ".join(str(sol) for sol in result.sols_failed[:10])
            result.error = f"{len(result.sols_failed)} sols not loaded: {listed}"
        # A reprocess does not look at the listing for changes: it must not store its validators.
        await self._finish(
            result,
            now,
            response,
            None if reprocess else entries,
            timing,
            groups,
            reprocess=reprocess,
        )

    # --- the listings

    def _can_use_validators(
        self, state: _State, known: Mapping[str, RadFileRow], lo: int | None, hi: int | None
    ) -> bool:
        """May the conditional request of the listing be sent?

        A ``304`` leaves the plan to the stored files, so it is trusted only when they cover
        what the run is about: no file in the scope is waiting for a retry and every sol of a
        bounded range (or, without bounds, every listed file) has its rows.
        """
        if state.checksums_etag is None and state.checksums_last_modified is None:
            return False
        scope = [r for r in known.values() if (lo or 0) <= r.sol <= (hi or 2**31)]
        if any(r.status in rad_repo.BAD_STATUSES for r in scope):
            return False
        if lo is not None and hi is not None:
            have = {r.sol for r in scope}
            return all(sol in have for sol in range(lo, hi + 1))
        return state.files_listed is not None and len(known) == state.files_listed

    async def _fetch(
        self, name: str, etag: str | None, last_modified: str | None
    ) -> ConditionalBody:
        try:
            return await self._client.get_conditional(
                self._base + name,
                max_bytes=self._settings.rad_list_max_bytes,
                allowed_origin=self._base,
                etag=etag,
                last_modified=last_modified,
            )
        except UpstreamError as exc:
            raise _ListingFailed(name, exc) from exc

    @staticmethod
    def _listing_entries(response: ConditionalBody) -> list[ChecksumEntry]:
        return parse_checksums((response.body or b"").decode("utf-8", errors="replace"))

    async def _fetch_timing(self, state: _State, *, conditional_ok: bool) -> _Timing:
        """``TIMING_ERRORS.TXT``; conditional only when no sol is to be loaded (the stored
        flags then cover every file), else in full. A failure is a note, not a failed run."""
        use = conditional_ok and (state.timing_etag or state.timing_last_modified)
        try:
            response = await self._fetch(
                TIMING_NAME,
                state.timing_etag if use else None,
                state.timing_last_modified if use else None,
            )
        except _ListingFailed as exc:
            message = f"{TIMING_NAME} not available: {exc}"[:MAX_ERROR_LENGTH]
            logger.warning(message)
            return _Timing(error=message)
        if response.not_modified:
            return _Timing(response=response)
        body = response.body or b""
        names = parse_timing_errors(body.decode("utf-8", errors="replace"))
        return _Timing(names, response, hashlib.sha256(body).hexdigest())

    async def _refresh_timing_flags(self, names: set[str]) -> None:
        async with self._factory() as session:
            changed = await rad_repo.set_timing_flags(session, names)
            await session.commit()
        if changed:
            logger.info("Timing error flags changed for %d sols", changed)

    # --- the sols

    async def _load_sols(
        self,
        result: RadRunResult,
        todo: Sequence[int],
        groups: Mapping[int, Sequence[ChecksumEntry]],
        known: Mapping[str, RadFileRow],
        timing: set[str] | None,
        reprocess: bool,
        workers: int,
    ) -> None:
        """Load the sols with at most ``workers`` at a time, taking them in order. The first
        error that is not a failed sol (``_ServerDown``, a crash) cancels the other workers (their
        transactions are rolled back) and is raised (the one that failed first in time); so is a
        cancellation of the run. A worker cancelled while its commit has already gone through on
        the server leaves consistent data, but ``sols_loaded`` may then be one too low."""
        queue = iter(todo)

        async def worker() -> None:
            # ``next`` and the counting in ``_load_sol`` have no await inside: no races.
            for sol in queue:
                await self._load_sol(result, sol, groups[sol], known, timing, reprocess)

        done: set[asyncio.Task[None]] = set()
        tasks = [asyncio.create_task(worker()) for _ in range(min(workers, len(todo)))]
        try:
            if tasks:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        failed = [task for task in done if not task.cancelled() and task.exception() is not None]
        if failed:
            raise failed[0].exception()  # type: ignore[misc]

    # --- one sol

    async def _load_sol(
        self,
        result: RadRunResult,
        sol: int,
        entries: Sequence[ChecksumEntry],
        known: Mapping[str, RadFileRow],
        timing: set[str] | None,
        reprocess: bool = False,
    ) -> None:
        work = _SolWork(sol, entries[-1])
        summary: SolSummary | None = None
        try:
            await self._read_group(work, entries, probe=not reprocess)
            summary = self._summarize(work)
        except _FileFailed as exc:
            self._mark_failed(work, entries, exc)
        await self._save_sol(work, summary, known, timing)
        self._count(result, work, summary)

    async def _read_group(
        self, work: _SolWork, entries: Sequence[ChecksumEntry], *, probe: bool = True
    ) -> None:
        primary = work.primary
        parsed = await self._read_file(primary)
        work.parsed.append((primary.path, parsed))
        work.rows.append(_file_row(primary, parsed, status="loaded", role="primary"))
        for entry in entries[:-1]:
            if probe:
                tail = await self._probe(entry)
                if _same_file(tail, parsed):
                    work.rows.append(_duplicate_row(entry, primary.path, tail))
                    continue
            try:
                other = await self._read_file(entry)
            except _FileFailed as exc:
                work.rows.append(_failed_row(entry, "extra", exc.status, exc.message))
                raise
            work.parsed.append((entry.path, other))
            work.rows.append(_file_row(entry, other, status="loaded", role="extra"))

    def _summarize(self, work: _SolWork) -> SolSummary | None:
        """Like ``_summarize_sol``, but an unexpected error of one sol fails that sol only."""
        try:
            return self._summarize_sol(work)
        except _FileFailed:
            raise
        except Exception as exc:
            logger.exception("RAD sol %d could not be combined", work.sol)
            raise _FileFailed("failed", f"unexpected {_describe(exc)}") from exc

    def _summarize_sol(self, work: _SolWork) -> SolSummary | None:
        """The sol's summary (``None``: no usable observation); sets the sol's Earth date."""
        merged, collapsed = merge_files_counted(work.parsed)
        if collapsed:
            logger.warning(
                "RAD sol %d: %d observations repeat a start time inside a file, counted invalid",
                work.sol,
                collapsed,
            )
        summary = combine_sol(merged, self._settings.rad_low_rate_ratio, repeated=collapsed)
        if summary is not None:
            work.hourly = combine_hours(summary.used_observations, work.sol)
            _check_smallint(work.hourly, summary.used)
            primary = dict(work.parsed)[work.primary.path]
            work.earth_date = choose_earth_date(primary.start_time, summary.used_utc)
            if work.earth_date is None:
                raise _FileFailed("failed", "no readable date in the file")
        return summary

    @staticmethod
    def _mark_failed(work: _SolWork, entries: Sequence[ChecksumEntry], exc: _FileFailed) -> None:
        """Rows of a sol that cannot be loaded: the file at fault keeps its own, the files that
        were not reached are ``failed`` too, so nothing looks done."""
        work.failed = True
        for row in work.rows:
            if row["status"] not in rad_repo.BAD_STATUSES:
                # Read fine, but the sol cannot be stored: the next run must try again.
                row.update(
                    status="failed",
                    md5=None,
                    duplicate_of=None,
                    dup_check=None,
                    error=exc.message[:MAX_ERROR_LENGTH],
                )
        have = {row["path"] for row in work.rows}
        for entry in entries:
            if entry.path in have:
                continue
            role = "primary" if entry is work.primary else "extra"
            if entry is work.primary:
                work.rows.append(_failed_row(entry, role, exc.status, exc.message))
            else:
                work.rows.append(_failed_row(entry, role, "failed", "not read: " + exc.message))

    async def _save_sol(
        self,
        work: _SolWork,
        summary: SolSummary | None,
        known: Mapping[str, RadFileRow],
        timing: set[str] | None,
    ) -> None:
        """One transaction: the rows of the sol's files and its summary (or none)."""
        async with self._factory() as session:
            existing = (await rad_repo.get_daily_map(session, [work.sol])).get(work.sol)
            rows = _with_timing(work.rows, known, timing)
            daily = None if work.failed else _daily_row(work, summary, timing, existing)
            if _is_empty(work, summary):
                for row in rows:
                    if row["status"] == "loaded":
                        row["status"] = "empty"
            await rad_repo.upsert_files(session, rows)
            if daily is not None:
                await rad_repo.upsert_daily(session, daily)
                await self._save_hours(session, work)
            elif not work.failed:
                await rad_repo.delete_daily(session, [work.sol])
            await session.commit()

    @staticmethod
    async def _save_hours(session: AsyncSession, work: _SolWork) -> None:
        """The hours of the sol and the state of their calculation (the revision is set last)."""
        hourly = work.hourly
        if hourly is None:
            return
        rows = [_hour_row(work.sol, hour) for hour in hourly.hours]
        await rad_repo.replace_hourly(session, work.sol, rows)
        await rad_repo.set_hourly_state(
            session,
            work.sol,
            revision=RAD_HOURLY_REVISION,
            reject=hourly.reject,
            dropped=hourly.dropped,
        )

    @staticmethod
    def _count(result: RadRunResult, work: _SolWork, summary: SolSummary | None) -> None:
        for row in work.rows:
            empty = _is_empty(work, summary) and row["status"] == "loaded"
            status = "empty" if empty else row["status"]
            if status in ("failed", "md5_mismatch"):
                result.files_failed += 1
            elif status == "duplicate":
                result.files_duplicate += 1
            elif status == "empty":
                result.files_empty += 1
            else:
                result.files_loaded += 1
        if work.failed:
            result.sols_failed.append(work.sol)
        else:
            result.sols_loaded += 1

    # --- files

    def _url(self, path: str) -> str:
        return self._base + path

    async def _read_file(self, entry: ChecksumEntry) -> RadFile:
        """Stream the file through the parser, check md5 and sol; the body is read again once
        when the md5 differs from the listing's."""
        for _ in range(2):
            parsed = await self._attempts(entry.path, lambda: self._download(entry.path))
            if parsed.md5 == entry.md5:
                break
            logger.warning("md5 of %s differs from the listing (%s)", entry.path, parsed.md5)
        else:
            raise _FileFailed("md5_mismatch", f"md5 {parsed.md5} differs from the listing")
        if parsed.start_sol != entry.sol:
            raise _FileFailed(
                "failed", f"START_SOL {parsed.start_sol} differs from the name ({entry.sol})"
            )
        logger.info(
            "RAD %s: %d bytes, %d dose observations",
            entry.path,
            parsed.size,
            len(parsed.observations),
        )
        return parsed

    async def _download(self, path: str) -> RadFile:
        settings = self._settings
        async with asyncio.timeout(settings.rad_file_deadline_seconds):
            return await self._client.stream_into(
                self._url(path),
                lambda: RadParser(settings.rad_max_line_bytes, settings.rad_max_blocks),
                max_bytes=settings.rad_max_file_bytes,
                allowed_origin=self._base,
                max_retries=0,
            )

    async def _probe(self, entry: ChecksumEntry) -> TailProbe | None:
        """The size and tail of a file without reading it; ``None`` when the probe failed."""
        try:
            return await self._attempts(
                entry.path,
                lambda: self._client.probe_tail(
                    self._url(entry.path), TAIL_BYTES, allowed_origin=self._base, max_retries=0
                ),
            )
        except _FileFailed as exc:
            logger.warning("Probe of %s failed (%s); reading the file", entry.path, exc.message)
            return None

    async def _attempts[T](self, label: str, call: Callable[[], Awaitable[T]]) -> T:
        """``call`` with ``RAD_FILE_ATTEMPTS`` attempts (pauses of 30 and 120 s), then the
        cooldown and one more attempt; ``_ServerDown`` if that fails too. An error that more
        attempts cannot cure is a ``_FileFailed``."""
        attempts = self._settings.rad_file_attempts
        for number in range(1, attempts + 1):
            await self._wait_cooldown()
            try:
                return await call()
            except Exception as exc:
                self._check_transient(label, exc, number)
            if number < attempts:
                await self._sleep(RETRY_PAUSES[min(number - 1, len(RETRY_PAUSES) - 1)])
        await self._cool_down(label)
        try:
            return await call()
        except Exception as exc:
            self._check_transient(label, exc, attempts + 1)
            raise _ServerDown(f"{label}: {_describe(exc)}") from exc

    async def _wait_cooldown(self) -> None:
        """Wait while any worker's cooldown runs (no new requests hit a failing server)."""
        if self._cooling is not None:
            await self._cooling.wait()

    async def _cool_down(self, label: str) -> None:
        """The cooldown after the attempts failed; one is shared by all workers: the first to
        get here sleeps, the others wait for it to end."""
        active = self._cooling
        if active is not None:
            await active.wait()
            return
        event = self._cooling = asyncio.Event()
        logger.warning("%s: cooling down for %.0f s", label, self._settings.rad_cooldown_seconds)
        try:
            await self._sleep(self._settings.rad_cooldown_seconds)
        finally:
            self._cooling = None
            event.set()

    @staticmethod
    def _check_transient(label: str, exc: Exception, number: int) -> None:
        """Return for a transient failure; raise ``_FileFailed`` for any other known one."""
        if isinstance(exc, UpstreamError | RadFormatError | TimeoutError):
            if _is_transient(exc):
                logger.warning("%s: attempt %d failed: %s", label, number, _describe(exc))
                return
            raise _FileFailed("failed", _describe(exc)) from exc
        raise exc

    # --- the end of a run

    async def _prune(
        self,
        result: RadRunResult,
        known: Mapping[str, RadFileRow],
        groups: Mapping[int, Sequence[ChecksumEntry]],
        listed: int,
    ) -> None:
        """Forget files that left the listing (and the sols left without any file), unless the
        listing shrank suspiciously."""
        if listed < self._settings.rad_min_keep_ratio * len(known):
            message = f"listing has {listed} files, {len(known)} known: nothing deleted"
            result.notes.append(message)
            result.exit_code = EXIT_REMAINING
            logger.error(message)
            return
        listed_paths = _paths(groups)
        gone = [path for path in known if path not in listed_paths]
        gone_sols = {known[path].sol for path in gone} - set(groups)
        async with self._factory() as session:
            await rad_repo.delete_files(session, gone)
            await rad_repo.delete_daily(session, gone_sols)
            await session.commit()
        result.files_deleted, result.sols_deleted = len(gone), len(gone_sols)

    async def _finish(
        self,
        result: RadRunResult,
        now: datetime,
        response: ConditionalBody,
        entries: list[ChecksumEntry] | None,
        timing: _Timing,
        groups: Mapping[int, Sequence[ChecksumEntry]],
        *,
        reprocess: bool = False,
    ) -> None:
        """Store the state: validators of the listings (their content is fully dealt with)
        and the outcome; ``last_error`` gets what needs a look."""
        notes = [*result.notes, *([result.error] if result.error else [])]
        if entries is not None and not entries:
            notes.append("the listing has no daily files")
            result.exit_code = max(result.exit_code, EXIT_REMAINING)
        fields: dict[str, Any] = {
            "checked_at": now,
            "last_error": "; ".join(notes)[:MAX_ERROR_LENGTH] or None,
        }
        if result.exit_code == EXIT_OK and not reprocess:
            fields["succeeded_at"] = now
        async with self._factory() as session:
            state = _State.of(await rad_repo.get_dataset(session))
            if entries is not None:
                fields.update(self._checksum_fields(state, response, entries))
            if timing.response is not None and not timing.response.not_modified:
                fields.update(self._timing_fields(timing))
            await rad_repo.save_dataset(session, **fields)
            await session.commit()

    @staticmethod
    def _checksum_fields(
        state: _State, response: ConditionalBody, entries: Sequence[ChecksumEntry]
    ) -> dict[str, Any]:
        digest = hashlib.sha256(response.body or b"").hexdigest()
        fields: dict[str, Any] = {
            "checksums_etag": response.etag,
            "checksums_last_modified": response.last_modified,
            "checksums_sha256": digest,
            "files_listed": len(entries),
        }
        if digest != state.checksums_sha256:
            fields["changed_at"] = datetime.now(UTC)
        return fields

    @staticmethod
    def _timing_fields(timing: _Timing) -> dict[str, Any]:
        response = timing.response
        assert response is not None  # noqa: S101 - callers check
        return {
            "timing_etag": response.etag,
            "timing_last_modified": response.last_modified,
            "timing_sha256": timing.sha256,
        }

    async def _record_failure(self, result: RadRunResult) -> None:
        """A run that stopped: the reason in ``last_error`` (validators stay as they were)."""
        try:
            async with self._factory() as session:
                await rad_repo.save_dataset(
                    session, checked_at=datetime.now(UTC), last_error=result.error
                )
                await session.commit()
        except Exception:
            logger.exception("Could not record the RAD failure")


# --- helpers of the plan and the rows -----------------------------------------------------


def _group(entries: Sequence[ChecksumEntry]) -> dict[int, list[ChecksumEntry]]:
    """The listed files by sol, each group sorted by path (the last one is the main file)."""
    groups: dict[int, list[ChecksumEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.sol].append(entry)
    return {sol: sorted(group, key=lambda e: e.path) for sol, group in sorted(groups.items())}


def _paths(groups: Mapping[int, Sequence[ChecksumEntry]]) -> set[str]:
    return {entry.path for group in groups.values() for entry in group}


def _plan(
    groups: Mapping[int, Sequence[ChecksumEntry]],
    known: Mapping[str, RadFileRow],
    lo: int | None,
    hi: int | None,
) -> list[int]:
    """The sols in range whose files are not stored as listed (new, changed md5, a file added
    or removed, a file that failed), ascending."""
    stored: dict[int, dict[str, RadFileRow]] = defaultdict(dict)
    for row in known.values():
        stored[row.sol][row.path] = row
    todo = []
    for sol, group in groups.items():
        if (lo is not None and sol < lo) or (hi is not None and sol > hi):
            continue
        rows = stored.get(sol, {})
        # With a range, files that left the listing are ignored (they are only forgotten by a
        # run without bounds); otherwise one would be re-read every time.
        same_set = len(rows) == len(group) or lo is not None or hi is not None
        done = same_set and all(
            (row := rows.get(e.path)) is not None
            and row.md5 == e.md5
            and row.status not in rad_repo.BAD_STATUSES
            for e in group
        )
        if not done:
            todo.append(sol)
    return todo


def _check_smallint(hourly: HourlyResult, used: int) -> None:
    """The counts are stored as SMALLINT: a sol over the limit is failed, not truncated."""
    if used > MAX_SMALLINT or any(hour.n_obs > MAX_SMALLINT for hour in hourly.hours):
        raise _FileFailed("failed", f"more than {MAX_SMALLINT} observations in one sol")


def _hour_row(sol: int, hour: HourSummary) -> dict[str, Any]:
    return {
        "sol": sol,
        "hour": hour.hour,
        "n_obs": hour.n_obs,
        "live_s": hour.live_s,
        "dose_b_mean": hour.dose_b_mean,
        "dose_e_mean": hour.dose_e_mean,
        "bu_count": hour.bu_count,
    }


def _reprocess_groups(
    sols: Sequence[int],
    known: Mapping[str, RadFileRow],
    groups: Mapping[int, Sequence[ChecksumEntry]],
) -> dict[int, list[ChecksumEntry]]:
    """The files to read again for ``sols``: the stored ``loaded`` ones, the main file last.

    The md5 comes from the listing when it was read (it may have changed since the file was
    stored), else from the stored row. A sol whose main file is not ``loaded`` is left to
    ``rad sync`` without the flag.
    """
    listed = {entry.path: entry for group in groups.values() for entry in group}
    loaded: dict[int, list[RadFileRow]] = defaultdict(list)
    for row in known.values():
        if row.status == "loaded" and row.md5 is not None:
            loaded[row.sol].append(row)
    plan: dict[int, list[ChecksumEntry]] = {}
    for sol in sorted(sols):
        rows = sorted(loaded.get(sol, []), key=lambda r: (r.role == "primary", r.path))
        if not rows or rows[-1].role != "primary":
            logger.warning("RAD sol %d has no loaded main file: left to rad sync", sol)
            continue
        plan[sol] = [listed.get(r.path) or ChecksumEntry(r.path, r.md5 or "", sol) for r in rows]
    return plan


def _is_empty(work: _SolWork, summary: SolSummary | None) -> bool:
    """The files were read but not one observation is usable."""
    return summary is None and not work.failed


def _daily_row(
    work: _SolWork, summary: SolSummary | None, timing: set[str] | None, existing: Any
) -> dict[str, Any] | None:
    """The ``rad_daily`` values of the sol; the timing flag is as stored when the listing of
    timing errors is not known."""
    if summary is None:
        return None
    if timing is not None:
        caveat: bool | None = file_name(work.primary.path) in timing
    else:
        caveat = existing.timing_caveat if existing is not None else None
    return {
        "sol": work.sol,
        "earth_date": work.earth_date,
        "dose_b_mean": summary.dose_b_mean,
        "dose_b_min": summary.dose_b_min,
        "dose_b_max": summary.dose_b_max,
        "dose_e_mean": summary.dose_e_mean,
        "dose_e_min": summary.dose_e_min,
        "dose_e_max": summary.dose_e_max,
        "bu_count": summary.bu_count,
        "live_s": summary.live_s,
        "n_used": summary.used,
        "n_dropped_qc": summary.dropped_qc,
        "n_dropped_low_rate": summary.dropped_low_rate,
        "n_dropped_invalid": summary.dropped_invalid,
        "qc_bits": summary.qc_bits,
        "timing_caveat": caveat,
        "source_path": work.primary.path,
    }


def _same_file(probe: TailProbe | None, main: RadFile) -> bool:
    if probe is None or probe.total_size != main.size:
        return False
    return probe.status == 200 or probe.tail == main.tail


def _blank_row(entry: ChecksumEntry, role: str, status: str) -> dict[str, Any]:
    return {
        "path": entry.path,
        "sol": entry.sol,
        "md5": entry.md5,
        "status": status,
        "role": role,
        "duplicate_of": None,
        "dup_check": None,
        "size_bytes": None,
        "creation_time": None,
        "rdr_version": None,
        "blocks": 0,
        "dose_obs": 0,
        "timing_listed": False,
        "error": None,
        "processed_at": datetime.now(UTC),
    }


def _file_row(entry: ChecksumEntry, parsed: RadFile, *, status: str, role: str) -> dict[str, Any]:
    row = _blank_row(entry, role, status)
    row.update(
        size_bytes=parsed.size,
        creation_time=parsed.creation_time,
        rdr_version=parsed.rdr_version,
        blocks=parsed.blocks,
        dose_obs=len(parsed.observations),
    )
    return row


def _duplicate_row(entry: ChecksumEntry, of: str, probe: TailProbe | None) -> dict[str, Any]:
    row = _blank_row(entry, "extra", "duplicate")
    row["duplicate_of"] = of
    row["dup_check"] = "tail" if probe is not None and probe.status == 206 else "size"
    row["size_bytes"] = probe.total_size if probe is not None else None
    return row


def _failed_row(entry: ChecksumEntry, role: str, status: str, message: str) -> dict[str, Any]:
    row = _blank_row(entry, role, status)
    row["md5"] = None
    row["error"] = message[:MAX_ERROR_LENGTH]
    return row


def _with_timing(
    rows: Sequence[dict[str, Any]], known: Mapping[str, RadFileRow], timing: set[str] | None
) -> list[dict[str, Any]]:
    """``timing_listed`` of each row: from the listing when it is known, else as stored."""
    out = []
    for row in rows:
        if timing is not None:
            flag = file_name(row["path"]) in timing
        else:
            stored = known.get(row["path"])
            flag = bool(stored.timing_listed) if stored is not None else False
        out.append({**row, "timing_listed": flag})
    return out


def build_rad_client(settings: Settings, **kwargs: Any) -> UpstreamClient:
    """The client of the RAD host: a pause of ``RAD_PAUSE_SECONDS`` after every response and
    ``RAD_READ_TIMEOUT_SECONDS`` without a byte as the limit (the other settings as usual)."""
    return UpstreamClient(
        settings,
        min_interval=settings.rad_pause_seconds,
        timeout=settings.rad_read_timeout_seconds,
        **kwargs,
    )
