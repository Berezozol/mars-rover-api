"""Reconciliation report: what we store against what the source returned and declared.

Unexplained differences (they make the CLI exit with 1):
- photos total differs from the sum of sol_summaries or of camera_sol_counts;
- a finished run whose declared ``source_total`` differs from the sum of ``received`` over
  its sols, unless it is at the live edge (see ``_check_runs``);
- a sol whose ``received - dropped`` differs from its photos in the table;
- a sol whose per-sol ``source_total`` is known and differs from ``received``;
- a sol in [0, max_sol] without data that failed in sync_runs (see
  ``_classify_missing``) or was never fetched;
- with a legacy manifest: a sol where our v1 count (full frames, or every frame for the
  rovers v1 shows them for) is below the old API's.

The overall source total is informational: the live feed keeps growing.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SolSourceCount, SyncRun
from app.repositories import photos as photos_repo
from app.repositories import rovers as rovers_repo
from app.repositories import source_counts as counts_repo
from app.repositories import sync_runs as runs_repo
from app.services.legacy import shows_all_frames
from app.services.sync import compact_ranges

LIST_LIMIT = 20
GOOD_STATUSES = ("success", "partial")

# First sol compared with the old API manifest, per rover (owner decision, docs/LEGACY-API.md,
# accepted differences): up to Curiosity sol 4102 the old API's database held every
# frame type, thumbnails included, while v1 shows full frames only; from sol 4103 it counted
# full frames as v1 does.
CURIOSITY_LEGACY_FIRST_SOL = 4103
LEGACY_COMPARE_MIN_SOL: dict[str, int] = {"curiosity": CURIOSITY_LEGACY_FIRST_SOL}


@dataclass(frozen=True)
class StoredMismatch:
    sol: int
    received: int
    dropped: int
    stored: int


@dataclass(frozen=True)
class SourceTotalMismatch:
    sol: int
    source_total: int
    received: int


@dataclass(frozen=True)
class RunTotalMismatch:
    run_id: int
    mode: str
    from_sol: int
    to_sol: int
    source_total: int
    received: int


@dataclass(frozen=True)
class LegacyDiff:
    sol: int
    ours: int
    legacy: int


@dataclass
class LegacyComparison:
    sols_compared: int = 0
    # Manifest sols below from_sol are not compared (LEGACY_COMPARE_MIN_SOL).
    from_sol: int = 0
    sols_skipped: int = 0
    equal: int = 0
    # ours > old: expected (the old API was capped / incomplete).
    ours_higher: list[LegacyDiff] = field(default_factory=list)
    # ours < old: unexplained.
    unexplained: list[LegacyDiff] = field(default_factory=list)


@dataclass
class ReconcileReport:
    rover_id: str
    photos_total: int
    summaries_total: int
    # Sum of camera_sol_counts.total_photos (what the cameras endpoint serves).
    camera_counts_total: int
    source_total: int | None
    source_total_error: str | None
    received_total: int
    dropped_total: int
    max_sol: int | None
    stored_mismatches: list[StoredMismatch]
    source_total_mismatches: list[SourceTotalMismatch]
    missing_failed: list[int]
    missing_source_empty: list[int]
    missing_unknown: list[int]
    # Runs whose declared total differs from the sum of received over their sols.
    run_total_mismatches: list[RunTotalMismatch] = field(default_factory=list)
    # Same, for runs at the live edge (the source still grows there): informational.
    live_edge_runs: list[RunTotalMismatch] = field(default_factory=list)
    # Runs with a total whose sol counts were overwritten by a later fetch: not checked.
    runs_superseded: int = 0
    legacy: LegacyComparison | None = None

    @property
    def unexplained_count(self) -> int:
        count = (
            len(self.stored_mismatches)
            + len(self.source_total_mismatches)
            + len(self.missing_failed)
            + len(self.missing_unknown)
            + len(self.run_total_mismatches)
        )
        if self.photos_total != self.summaries_total:
            count += 1
        if self.photos_total != self.camera_counts_total:
            count += 1
        if self.legacy is not None:
            count += len(self.legacy.unexplained)
        return count

    @property
    def ok(self) -> bool:
        return self.unexplained_count == 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["unexplained_count"] = self.unexplained_count
        data["ok"] = self.ok
        return data


def parse_legacy_manifest(data: Any) -> dict[int, int]:
    """``sol -> total_photos`` of an old API manifest; ``ValueError`` on another shape."""
    try:
        photos = data["photo_manifest"]["photos"]
        return {int(entry["sol"]): int(entry["total_photos"]) for entry in photos}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            'legacy manifest must look like {"photo_manifest": {"photos": '
            '[{"sol": N, "total_photos": N, ...}]}}'
        ) from exc


class UnknownRover(ValueError):
    """The rover slug is not in the database."""


async def build_report(
    session: AsyncSession,
    rover_id: str,
    *,
    source_total: int | None = None,
    source_total_error: str | None = None,
    legacy_manifest: dict[int, int] | None = None,
) -> ReconcileReport:
    """Compare the rover's stored data with the recorded source counts (DB reads only).

    Raises ``UnknownRover`` if the rover does not exist.
    """
    rover = await rovers_repo.get_rover(session, rover_id)
    if rover is None:
        raise UnknownRover(f"Unknown rover: {rover_id}")
    rover_id = rover.id
    stored = await counts_repo.stored_photos_by_sol(session, rover_id)
    summaries = await counts_repo.summary_counts_by_sol(session, rover_id)
    rows = {row.sol: row for row in await counts_repo.list_sol_source_counts(session, rover_id)}
    runs = await runs_repo.finished_runs(session, rover_id)
    max_sol = max(summaries) if summaries else None
    top = -1 if max_sol is None else max_sol
    missing = [sol for sol in range(top + 1) if sol not in summaries]
    failed, empty, unknown = _classify_missing(missing, runs, rows)
    checked, edge, superseded = _check_runs(runs, rows)
    report = ReconcileReport(
        rover_id=rover_id,
        photos_total=await photos_repo.count_photos(session, photos_repo.PhotoFilter(rover_id)),
        summaries_total=sum(total for total, _ in summaries.values()),
        camera_counts_total=await counts_repo.camera_counts_total(session, rover_id),
        source_total=source_total,
        source_total_error=source_total_error,
        received_total=sum(row.received for row in rows.values()),
        dropped_total=sum(row.dropped for row in rows.values()),
        max_sol=max_sol,
        stored_mismatches=[
            StoredMismatch(row.sol, row.received, row.dropped, stored.get(row.sol, 0))
            for row in rows.values()
            if row.received - row.dropped != stored.get(row.sol, 0)
        ],
        source_total_mismatches=[
            SourceTotalMismatch(row.sol, row.source_total, row.received)
            for row in rows.values()
            if row.source_total is not None and row.source_total != row.received
        ],
        missing_failed=failed,
        missing_source_empty=empty,
        missing_unknown=unknown,
        run_total_mismatches=checked,
        live_edge_runs=edge,
        runs_superseded=superseded,
    )
    if legacy_manifest is not None:
        # v1 counts: every frame for the rovers v1 shows them for, else full frames.
        column = 0 if shows_all_frames(rover_id) else 1
        v1_counts = {sol: counts[column] for sol, counts in summaries.items()}
        min_sol = LEGACY_COMPARE_MIN_SOL.get(rover_id, 0)
        report.legacy = compare_legacy(v1_counts, legacy_manifest, min_sol=min_sol)
    return report


def _classify_missing(
    missing: list[int], runs: Sequence[SyncRun], rows: dict[int, SolSourceCount]
) -> tuple[list[int], list[int], list[int]]:
    """Split no-data sols into (failed, source has nothing, never fetched).

    A sol is failed when its latest failing run (one listing it in ``failed_sols``) was
    not followed by a ``success``/``partial`` run covering it without listing it, nor by a
    fetch recorded in sol_source_counts. Otherwise a recorded fetch or such a covering run
    means the source has nothing for it.
    """
    wanted = set(missing)
    failed_at: dict[int, datetime] = {}
    good_at: dict[int, datetime] = {}
    for run in runs:
        if run.from_sol is None or run.to_sol is None or run.finished_at is None:
            continue
        listed = set(run.failed_sols or ())
        for sol in listed & wanted:
            failed_at[sol] = max(failed_at.get(sol, run.finished_at), run.finished_at)
        if run.status not in GOOD_STATUSES:
            continue
        for sol in range(max(run.from_sol, 0), run.to_sol + 1):
            if sol in wanted and sol not in listed:
                good_at[sol] = max(good_at.get(sol, run.finished_at), run.finished_at)
    failed: list[int] = []
    empty: list[int] = []
    unknown: list[int] = []
    for sol in missing:
        row = rows.get(sol)
        last_fail = failed_at.get(sol)
        recovered = [t for t in (good_at.get(sol), row.fetched_at if row else None) if t]
        if last_fail is not None and not any(t > last_fail for t in recovered):
            failed.append(sol)
        elif row is not None or sol in good_at:
            empty.append(sol)
        else:
            unknown.append(sol)
    return failed, empty, unknown


def _check_runs(
    runs: Sequence[SyncRun], rows: dict[int, SolSourceCount]
) -> tuple[list[RunTotalMismatch], list[RunTotalMismatch], int]:
    """Runs whose declared total differs from what their sols received.

    Checked are finished runs with a ``source_total`` whose every sol has a
    sol_source_counts row not fetched after the run finished; runs with a missing row or
    a row overwritten by a later fetch are counted as superseded. A mismatch of a run
    that reached the highest sol known when it started is the live edge (informational).
    """
    mismatches: list[RunTotalMismatch] = []
    live_edge: list[RunTotalMismatch] = []
    superseded = 0
    by_start = sorted(runs, key=lambda run: (run.started_at, run.id))
    highest: dict[int, int] = {}
    top = -1
    for run in by_start:
        top = max(top, run.to_sol if run.to_sol is not None else -1)
        highest[run.id] = top
    for run in by_start:
        if run.source_total is None or run.from_sol is None or run.to_sol is None:
            continue
        own = [rows.get(sol) for sol in range(run.from_sol, run.to_sol + 1)]
        if not all(row is not None and _fetched_by(row, run) for row in own):
            superseded += 1
            continue
        received = sum(row.received for row in own if row is not None)
        if received == run.source_total:
            continue
        item = RunTotalMismatch(
            run.id, run.mode, run.from_sol, run.to_sol, run.source_total, received
        )
        (live_edge if run.to_sol >= highest[run.id] else mismatches).append(item)
    return mismatches, live_edge, superseded


def _fetched_by(row: SolSourceCount, run: SyncRun) -> bool:
    """False if a later fetch overwrote the row after the run finished."""
    return run.finished_at is not None and row.fetched_at <= run.finished_at


def compare_legacy(
    ours: dict[int, int], legacy: dict[int, int], *, min_sol: int = 0
) -> LegacyComparison:
    """Per-sol v1 counts against the old API manifest, for sols >= ``min_sol`` only."""
    compared = sorted(sol for sol in legacy if sol >= min_sol)
    comparison = LegacyComparison(
        sols_compared=len(compared), from_sol=min_sol, sols_skipped=len(legacy) - len(compared)
    )
    for sol in compared:
        diff = LegacyDiff(sol, ours.get(sol, 0), legacy[sol])
        if diff.ours == diff.legacy:
            comparison.equal += 1
        elif diff.ours > diff.legacy:
            comparison.ours_higher.append(diff)
        else:
            comparison.unexplained.append(diff)
    return comparison


def render_json(report: ReconcileReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def render_text(report: ReconcileReport) -> str:
    """A readable multi-line report; long lists are cut to ``LIST_LIMIT`` entries."""
    source = report.source_total if report.source_total is not None else "unknown"
    if report.source_total_error:
        source = f"unknown ({report.source_total_error})"
    lines = [
        f"Reconciliation: {report.rover_id}",
        f"  photos in table:           {report.photos_total}",
        f"  sum of sol_summaries:      {report.summaries_total}",
        f"  sum of camera_sol_counts:  {report.camera_counts_total}",
        f"  source total (overall):    {source}",
        f"  received / dropped (sols): {report.received_total} / {report.dropped_total}",
        f"  max sol:                   {report.max_sol}",
        _items("sols where received - dropped != stored", report.stored_mismatches),
        _items("sols where source total != received", report.source_total_mismatches),
        _sols("sols without data, failed in sync_runs", report.missing_failed),
        _sols("sols without data, source has nothing", report.missing_source_empty),
        _sols("sols without data, never fetched", report.missing_unknown),
        _items("runs where source total != sum of received", report.run_total_mismatches),
        _items("live-edge runs (informational)", report.live_edge_runs),
        f"  runs not checked (sol counts overwritten later): {report.runs_superseded}",
    ]
    if report.legacy is not None:
        legacy = report.legacy
        expected = legacy.equal + len(legacy.ours_higher)
        lines += [
            f"  legacy manifest: {legacy.sols_compared} sols compared, {expected} expected "
            f"({legacy.equal} equal, {len(legacy.ours_higher)} ours higher: old API capped "
            "or incomplete)",
            f"  legacy sols below {legacy.from_sol} not compared: {legacy.sols_skipped}",
            _items("legacy sols where ours < old (unexplained)", legacy.unexplained),
        ]
    verdict = "OK" if report.ok else f"{report.unexplained_count} unexplained difference(s)"
    lines.append(f"Result: {verdict}")
    return "\n".join(lines)


def _sols(title: str, sols: list[int]) -> str:
    return f"  {title}: {len(sols)}" + (f" [{compact_ranges(sols)}]" if sols else "")


def _items(title: str, items: list[Any]) -> str:
    head = f"  {title}: {len(items)}"
    shown = [
        "    " + ", ".join(f"{key}={value}" for key, value in asdict(item).items())
        for item in items[:LIST_LIMIT]
    ]
    if len(items) > LIST_LIMIT:
        shown.append(f"    ... {len(items) - LIST_LIMIT} more (see --output)")
    return "\n".join([head, *shown])
