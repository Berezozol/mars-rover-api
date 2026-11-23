# ruff: noqa: E501, SIM115, UP031, B905
"""Out-of-range and humidity>100 statistics per sol on local raw weather files (no network).

Usage: python -I weather_bounds_check.py <weather_raw dir> <out.json>
Counts, per parameter, sols having at least one value outside the physical bounds
of docs/proposals/stage-17-weather.md section 3 (fill values 999999999 / -999.00 / empty skipped).
"""

import csv
import glob
import json
import os
import sys
from multiprocessing import Pool

FILL = "999999999"
B = {"air": (150, 300), "ground": (120, 330), "pressure": (300, 1200), "uv": (0, 60)}


def rd(f):
    with open(f, newline="") as fh:
        rr = csv.reader(fh)
        head = [h.strip() for h in next(rr)]
        yield from ((head, r) for r in rr)


def meda_sol(args):
    root, sol = args
    out = {"sol": sol}
    pat = f"{root}/meda/%s/sol_*/sol_{sol:04d}/WE__{sol:04d}___________%s_*.CSV"
    f = sorted(glob.glob(pat % ("data_calibrated_env", "CAL_ATS")))
    if f:
        n = bad = 0
        for _head, r in rd(f[-1]):
            for k in range(3, 6):
                x = r[k].strip()
                if x and x != FILL:
                    n += 1
                    v = float(x)
                    if not 150 <= v <= 300:
                        bad += 1
        out["air"] = (n, bad)
    f = sorted(glob.glob(pat % ("data_derived_env", "DER_PS")))
    if f:
        n = bad = 0
        for head, r in rd(f[-1]):
            x = r[head.index("PRESSURE")].strip()
            if x and x != FILL:
                n += 1
                if not 300 <= float(x) <= 1200:
                    bad += 1
        out["pressure"] = (n, bad)
    f = sorted(glob.glob(pat % ("data_derived_env", "DER_RHS")))
    if f:
        n = neg = o100 = o200 = 0
        big = []
        for head, r in rd(f[-1]):
            x = r[head.index("LOCAL_RELATIVE_HUMIDITY")].strip()
            if x and x != FILL:
                n += 1
                v = float(x)
                if v < 0:
                    neg += 1
                if v > 100:
                    o100 += 1
                    big.append(v)
                    if v > 200:
                        o200 += 1
        out["hum"] = (n, neg, o100, o200, sorted(big)[:: max(1, len(big) // 50)] if big else [])
    return out


def rems_sol(f):
    stats = {"air": [0, 0], "ground": [0, 0], "pressure": [0, 0], "uv": [0, 0], "hum": [0, 0, 0, 0]}
    sol = None
    with open(f, newline="") as fh:
        for r in csv.reader(fh):
            r = [x.strip().strip('"') for x in r]
            sol = int(r[1][:5])
            for key, i, (lo, hi) in (
                ("air", 33, B["air"]),
                ("ground", 7, B["ground"]),
                ("pressure", 38, B["pressure"]),
                ("uv", 22, B["uv"]),
            ):
                v = float(r[i])
                if v == -999.0:
                    continue
                stats[key][0] += 1
                if not lo <= v <= hi:
                    stats[key][1] += 1
            v = float(r[34 + 7 + 0]) if False else None
    return {"sol": sol, "file": os.path.basename(f), **stats}


if __name__ == "__main__":
    root, outp = sys.argv[1], sys.argv[2]
    sols = sorted(
        int(os.path.basename(p)[4:])
        for p in glob.glob(f"{root}/meda/data_calibrated_env/sol_*/sol_*")
    )
    with Pool(max(1, os.cpu_count() - 2)) as p:
        meda = p.map(meda_sol, [(root, s) for s in sols], chunksize=4)
    json.dump({"meda": meda}, open(outp, "w"))
    print(len(meda))
