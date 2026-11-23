# ruff: noqa: E501, SIM115, UP031, B905
"""Daily ground-temperature amplitude from local DER_TIRS (no network).

Usage: python -I weather_ground_published.py <weather_raw/meda dir> <first sol> <last sol>
T = (UPWARD_LW_IRRADIANCE / sigma) ** 0.25; quality filter as in weather_ground_check.py.
Prints per-sol max/min of hourly LTST medians and of raw 1st/99th percentiles, then means.
Input files are untrusted data; standard library only.
"""

import csv
import glob
import statistics
import sys
from collections import defaultdict

SIGMA = 5.670374419e-8


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def main() -> None:
    root, s0, s1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    res = []
    for sol in range(s0, s1 + 1):
        s4 = f"{sol:04d}"
        fs = glob.glob(f"{root}/data_derived_env/*/sol_{s4}/WE__{s4}___________DER_TIRS*.CSV")
        if not fs:
            continue
        by_h = defaultdict(list)
        allv = []
        with open(fs[0], newline="") as f:
            for r in csv.DictReader(f):
                if (
                    r["ROVER_LOW_TILT"] != "1"
                    or r["WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV"] == "0"
                ):
                    continue
                try:
                    u = float(r["UPWARD_LW_IRRADIANCE"])
                except ValueError:
                    continue
                if u <= 0 or u >= 9e8:
                    continue
                t = (u / SIGMA) ** 0.25
                by_h[int(r["LTST"].split()[1][:2])].append(t)
                allv.append(t)
        if len(by_h) < 12:
            print(f"sol {sol}: only {len(by_h)} hours, skipped")
            continue
        hm = [statistics.median(v) for v in by_h.values()]
        row = (
            sol,
            max(hm),
            min(hm),
            max(hm) - min(hm),
            pct(allv, 0.99),
            pct(allv, 0.01),
            len(allv),
        )
        res.append(row)
        print(
            "sol %d: hourly-median max %.1f min %.1f amp %.1f | raw p99 %.1f p01 %.1f n=%d"
            % row[:1]
            + row[1:]
            if False
            else "sol %d: hmax %.1f hmin %.1f hamp %.1f | p99 %.1f p01 %.1f n=%d" % row
        )
    if res:
        print(
            "MEAN over %d sols: hmax %.1f hmin %.1f hamp %.1f | rawamp %.1f"
            % (
                len(res),
                statistics.mean(r[1] for r in res),
                statistics.mean(r[2] for r in res),
                statistics.mean(r[3] for r in res),
                statistics.mean(r[4] - r[5] for r in res),
            )
        )


if __name__ == "__main__":
    main()
