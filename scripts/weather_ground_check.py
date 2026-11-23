"""Compare Perseverance ground temperature estimates from MEDA TIRS (no network).

Usage: python -I weather_ground_check.py <cal_tirs dir> <weather_raw/meda dir> <sol> [<sol> ...]

Reads CAL_TIRS from <cal_tirs dir> (files named WE__NNNN___CAL_TIRS...CSV),
DER_TIRS and CAL_ATS from the local weather_raw copy. Prints hourly (LTST) medians:
  A  = CAL_TIRS GROUND_TEMP (instrument brightness temperature, 8-14 um channel)
  B1 = band-limited Planck inversion (6.5-30 um) of CAL_TIRS UPWARD_LW_IRRADIANCE, eps=1
  B2 = same inversion applied to DER_TIRS UPWARD_LW_IRRADIANCE
  S  = naive Stefan-Boltzmann of DER_TIRS UPWARD_LW_IRRADIANCE
  Ta = median of CAL_ATS ATS_LOCAL_TEMP1..3
Pure standard library; input files are treated as untrusted data.
"""

import csv
import glob
import math
import statistics
import sys
from collections import defaultdict

H = 6.62607015e-34
C = 2.99792458e8
KB = 1.380649e-23
SIGMA = 5.670374419e-8


def band_flux(t: float, lo: float = 6.5e-6, hi: float = 30e-6, n: int = 400) -> float:
    """pi * integral of Planck radiance over [lo, hi] (W/m2), Simpson."""
    h = (hi - lo) / n
    s = 0.0
    for i in range(n + 1):
        lam = lo + i * h
        b = 2 * H * C**2 / lam**5 / math.expm1(H * C / (lam * KB * t))
        s += b * (1 if i in (0, n) else 4 if i % 2 else 2)
    return math.pi * s * h / 3


TABLE = [(t, band_flux(t)) for t in range(100, 401)]


def invert_band(f: float) -> float | None:
    if not f or f <= 0 or f < TABLE[0][1] or f > TABLE[-1][1]:
        return None
    lo, hi = 0, len(TABLE) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if TABLE[mid][1] < f:
            lo = mid
        else:
            hi = mid
    (t0, f0), (_t1, f1) = TABLE[lo], TABLE[hi]
    return t0 + (f - f0) / (f1 - f0)


def fl(x: str) -> float | None:
    try:
        v = float(x)
    except ValueError:
        return None
    return None if v >= 9e8 else v


def hour(ltst: str) -> int:
    return int(ltst.split()[1][:2])


def find(pattern: str) -> str | None:
    m = sorted(glob.glob(pattern))
    return m[0] if m else None


def med(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def main() -> None:
    cal_dir, meda_dir = sys.argv[1], sys.argv[2]
    for sol in (int(s) for s in sys.argv[3:]):
        s4 = f"{sol:04d}"
        cal = find(f"{cal_dir}/WE__{s4}___________CAL_TIRS*.CSV")
        der = find(f"{meda_dir}/data_derived_env/*/sol_{s4}/WE__{s4}___________DER_TIRS*.CSV")
        ats = find(f"{meda_dir}/data_calibrated_env/*/sol_{s4}/WE__{s4}___________CAL_ATS*.CSV")
        a, b1, b2, s, ta = (defaultdict(list) for _ in range(5))
        with open(cal, newline="") as f:
            for r in csv.DictReader(f):
                if (
                    r["ROVER_LOW_TILT"] != "1"
                    or r["WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV"] == "0"
                ):
                    continue
                h = hour(r["LTST"])
                g = fl(r["GROUND_TEMP"])
                if g:
                    a[h].append(g)
                u = fl(r["UPWARD_LW_IRRADIANCE"])
                t = invert_band(u) if u else None
                if t:
                    b1[h].append(t)
        with open(der, newline="") as f:
            for r in csv.DictReader(f):
                if (
                    r["ROVER_LOW_TILT"] != "1"
                    or r["WHEEL_OUTSIDE_TIRS_DOWNWARD_LOOKING_FOV"] == "0"
                ):
                    continue
                h = hour(r["LTST"])
                u = fl(r["UPWARD_LW_IRRADIANCE"])
                if u and u > 0:
                    s[h].append((u / SIGMA) ** 0.25)
                    t = invert_band(u)
                    if t:
                        b2[h].append(t)
        if ats:
            with open(ats, newline="") as f:
                for r in csv.DictReader(f):
                    v = [fl(r[f"ATS_LOCAL_TEMP{i}"]) for i in (1, 2, 3)]
                    if all(v):
                        ta[hour(r["LTST"])].append(statistics.median(v))
        print(f"== sol {sol}: cal={cal and cal.split('/')[-1]}")
        print("LTST_h   n_A    A      B1     B2     S      Ta")
        rows = []
        for h in range(24):
            m = [med(d[h]) for d in (a, b1, b2, s, ta)]
            if m[0] is None and m[1] is None:
                continue
            rows.append((h, m))
            fmt = " ".join(f"{x:6.1f}" if x else "   -  " for x in m)
            print(f"{h:5d} {len(a[h]):6d} {fmt}")
        for name, i in (("A", 0), ("B1", 1), ("B2", 2), ("S", 3), ("Ta", 4)):
            vals = [(m[i], h) for h, m in rows if m[i]]
            if vals:
                mx, mn = max(vals), min(vals)
                print(f"  {name}: max {mx[0]:.1f} K @h{mx[1]:02d}, min {mn[0]:.1f} K @h{mn[1]:02d}")


if __name__ == "__main__":
    main()
