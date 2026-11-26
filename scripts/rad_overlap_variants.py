"""Stage 22 C5: how many observations of a sol conflict in time, and what each rule would do.

Usage: uv run python -I scripts/rad_overlap_variants.py FILE [FILE ...]
Read-only, no network. For every file: the used observations (as ``combine_sol`` keeps them),
the neighbour pairs that break the ADR-7 rule (V0), the number of observations a greedy pass
would have to drop to leave no overlap (V1: drop the later one of every conflicting pair) and
the largest overlap, in seconds.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rad_overlap_probe import classify, load  # noqa: E402

from app.services.rad_sync import (  # noqa: E402
    OVERLAP_TOLERANCE_S,
    _duration,
    combine_hours,
    merge_files_counted,  # noqa: E402
)


def report(path: Path) -> None:
    parsed = load(path)
    merged, _ = merge_files_counted([(path.name, parsed)])
    kept, _ = classify(merged)
    timed = sorted((o for o in kept if o.start_utc), key=lambda o: o.start_utc)
    pairs = 0
    worst = 0.0
    for a, b in zip(timed, timed[1:], strict=False):
        over = _duration(a) - (b.start_utc - a.start_utc).total_seconds()
        if over > OVERLAP_TOLERANCE_S:
            pairs += 1
        worst = max(worst, over)
    dropped = 0
    last = None
    for obs in timed:
        if last is not None and (obs.start_utc - last.start_utc).total_seconds() < (
            _duration(last) - OVERLAP_TOLERANCE_S
        ):
            dropped += 1
            continue
        last = obs
    file_sol = parsed.start_sol
    result = combine_hours(kept, file_sol)
    print(
        f"{path.name}: sol={file_sol} used={len(kept)} v0_reject={result.reject} "
        f"bad_pairs={pairs} greedy_dropped={dropped} ({100 * dropped / max(len(kept), 1):.1f}%) "
        f"worst_overlap_s={worst:.0f} first={timed[0].start_utc_raw if timed else None} "
        f"last={timed[-1].start_utc_raw if timed else None}"
    )


if __name__ == "__main__":
    for name in sys.argv[1:]:
        report(Path(name))
