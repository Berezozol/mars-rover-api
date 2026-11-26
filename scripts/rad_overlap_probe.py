"""Stage 22 C5 diagnostics: why combine_hours rejects a sol as ``overlap``.

Usage: uv run python -I scripts/rad_overlap_probe.py FILE [FILE ...]
Reads local RAD daily files with the project parser, prints every observation with the
filters of ``combine_sol`` and the pairs that break the ADR-7 rule. Read-only, no network.
"""

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.rad_sync import (  # noqa: E402
    OVERLAP_TOLERANCE_S,
    _is_complete,
    _is_plausible,
    _rate,
    combine_sol,
    merge_files_counted,
)
from app.sources.rad import RadParser, parse_qc  # noqa: E402


def load(path: Path):
    parser = RadParser()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            parser.feed(chunk)
    return parser.result()


def classify(observations):
    """Reproduce combine_sol and return the kept observations with the reason of the others."""
    rows = []
    passed = []
    for obs in observations:
        qc = parse_qc(obs.qc_raw)
        if qc is None:
            rows.append((obs, "invalid_qc"))
        elif qc & 1:
            rows.append((obs, "qc1"))
        elif not _is_complete(obs):
            rows.append((obs, "incomplete"))
        else:
            passed.append(obs)
    median = statistics.median(_rate(o) for o in passed) if passed else 0
    kept = []
    for obs in passed:
        if _rate(obs) < 0.5 * median:
            rows.append((obs, "low_rate"))
        elif not _is_plausible(obs):
            rows.append((obs, "implausible"))
        else:
            kept.append(obs)
            rows.append((obs, "USED"))
    return kept, rows


def main() -> None:
    for name in sys.argv[1:]:
        path = Path(name)
        parsed = load(path)
        merged, repeated = merge_files_counted([(path.name, parsed)])
        kept, rows = classify(merged)
        summary = combine_sol(merged, 0.5, repeated=repeated)
        print(
            f"\n=== {path.name} start_sol={parsed.start_sol} obs={len(parsed.observations)} "
            f"merged={len(merged)} repeated={repeated} used={len(kept)} "
            f"summary_used={summary.used if summary else None}"
        )
        rows.sort(key=lambda r: (r[0].start_utc is None, r[0].start_utc))
        prev = None
        for obs, why in rows:
            step = (
                (obs.start_utc - prev.start_utc).total_seconds() if prev and obs.start_utc else None
            )
            print(
                f"{obs.index:4d} {obs.start_utc_raw} dur={obs.obs_duration_s} live={obs.live_s} "
                f"qc={obs.qc_raw} step={step} {why}"
            )
            if obs.start_utc:
                prev = obs
        ordered = sorted((o for o in kept if o.start_utc), key=lambda o: o.start_utc)
        viol = []
        for a, b in zip(ordered, ordered[1:], strict=False):
            step = (b.start_utc - a.start_utc).total_seconds()
            dur = a.obs_duration_s or a.live_s or 0
            if step < dur - OVERLAP_TOLERANCE_S:
                viol.append((a.index, b.index, step, dur, dur - step))
        steps = [
            (b.start_utc - a.start_utc).total_seconds()
            for a, b in zip(ordered, ordered[1:], strict=False)
        ]
        durs = [o.obs_duration_s for o in ordered]
        print(f"violations={len(viol)} of {len(steps)} pairs")
        if steps:
            print(f"step min/median/max = {min(steps)}/{statistics.median(steps)}/{max(steps)}")
        if durs and None not in durs:
            print(f"dur min/median/max = {min(durs)}/{statistics.median(durs)}/{max(durs)}")
        print(f"live median = {statistics.median(o.live_s for o in ordered) if ordered else None}")
        for v in viol[:8]:
            print("  viol", v)


if __name__ == "__main__":
    main()
