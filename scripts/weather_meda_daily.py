# ruff: noqa: E501, SIM115, UP031, B905
"""Daily per-sol summary of MEDA weather from local raw files (no network).

Usage: python -I weather_meda_daily.py <weather_raw dir> <out.csv> [max_sols]
Pure stdlib; one process per sol (multiprocessing). Algorithm is documented in
docs/reports/stage-17-weather-analysis.md.
"""

import glob
import math
import os
import re
import sys
import time
from multiprocessing import Pool

FILL = "999999999"
CAD_BIN = 600  # coverage bin, seconds of local sol time
NBINS = 148  # 88775 s / 600 s


def find(root, coll, sol, prod):
    pat = f"{root}/meda/{coll}/sol_*/sol_{sol:04d}/WE__{sol:04d}___________{prod}_*.CSV"
    fs = sorted(glob.glob(pat))
    return fs[-1] if fs else None  # highest P0V


def lmst(s):
    return int(s[:5]), int(s[6:8]) * 3600 + int(s[9:11]) * 60 + float(s[12:])


class Acc:
    """min/max/sum/n + per-hour sums/counts."""

    __slots__ = ("mn", "mx", "s", "n", "hs", "hn", "vals")

    def __init__(self, keep=False):
        self.mn, self.mx, self.s, self.n = 1e30, -1e30, 0.0, 0
        self.hs, self.hn = [0.0] * 25, [0] * 25
        self.vals = [] if keep else None

    def add(self, v, h):
        if v < self.mn:
            self.mn = v
        if v > self.mx:
            self.mx = v
        self.s += v
        self.n += 1
        self.hs[h] += v
        self.hn[h] += 1
        if self.vals is not None:
            self.vals.append(v)

    def out(self, pre, d, nd=2):
        if not self.n:
            return
        d[pre + "_min"] = round(self.mn, nd)
        d[pre + "_max"] = round(self.mx, nd)
        d[pre + "_mean"] = round(self.s / self.n, nd)
        hm = [a / b for a, b in zip(self.hs, self.hn) if b]
        d[pre + "_hmean"] = round(sum(hm) / len(hm), nd)  # mean of hourly means
        d[pre + "_hours"] = len(hm)
        d[pre + "_n"] = self.n
        if self.vals:
            v = sorted(self.vals)
            d[pre + "_p01"] = round(v[int(len(v) * 0.01)], nd)
            d[pre + "_p99"] = round(v[min(len(v) - 1, int(len(v) * 0.99))], nd)


def read(path):
    with open(path, newline="") as f:
        f.readline()
        for line in f:
            yield line.rstrip("\r\n").split(",")


def header(path):
    with open(path) as f:
        return f.readline().rstrip("\r\n").split(",")


def ls_of(path):
    x = path[:-3] + "xml"
    try:
        t = open(x, encoding="utf-8").read(20000)
    except OSError:
        return None, None
    a = re.search(r"start_solar_longitude[^>]*>([\d.]+)<", t)
    b = re.search(r"stop_solar_longitude[^>]*>([\d.]+)<", t)
    return (float(a.group(1)) if a else None), (float(b.group(1)) if b else None)


def do_sol(args):
    root, sol = args
    d = {"sol": sol}
    f = find(root, "data_calibrated_env", sol, "CAL_ATS")
    if f:
        d["ats_ver"] = os.path.basename(f)[-8:-4]
        d["ls_start"], d["ls_stop"] = ls_of(f)
        a = [Acc() for _ in range(5)]
        med = Acc(keep=True)
        bins = set()
        hrs = [0] * 25
        off = 0
        nrow = 0
        bad = 0
        for r in read(f):
            nrow += 1
            try:
                s, t = lmst(r[1])
                if s != sol:
                    off += 1
                    continue
                h = int(t // 3600)
                v = [float(x) if x not in ("", FILL) else None for x in r[3:8]]
            except (ValueError, IndexError):
                bad += 1
                continue
            bins.add(int(t // CAD_BIN))
            hrs[h] += 1
            for i, x in enumerate(v):
                if x is not None and 100 < x < 400:
                    a[i].add(x, h)
            w = [x for x in v[:3] if x is not None and 100 < x < 400]
            if len(w) == 3:
                med.add(sorted(w)[1], h)
        d["ats_rows"] = nrow
        d["ats_offsol"] = off
        d["ats_bad"] = bad
        d["ats_cov"] = round(len(bins) / NBINS, 3)
        d["ats_hours_cov"] = sum(1 for x in hrs if x)
        for i in range(5):
            a[i].out(f"t{i + 1}", d)
        med.out("tmed", d)
    f = find(root, "data_calibrated_env", sol, "CAL_WS")
    if f:
        d["ws_ver"] = os.path.basename(f)[-8:-4]
        sp = Acc(keep=True)
        sp_still = Acc()
        sect = [0.0] * 16
        sx = sy = 0.0
        n_dir = 0
        bins = set()
        nrow = 0
        nstill = 0
        nbad = 0
        for r in read(f):
            nrow += 1
            try:
                s, t = lmst(r[1])
                if s != sol:
                    continue
                h = int(t // 3600)
                still = r[17] == "1"
                vals = []
                dirs = []
                for b, o in ((r[15], 3), (r[16], 9)):
                    if b != "1":
                        continue
                    if r[o] in ("", FILL) or r[o + 4] in ("", FILL):
                        continue
                    vals.append(float(r[o]))
                    dirs.append(float(r[o + 4]))
            except (ValueError, IndexError):
                nbad += 1
                continue
            if not vals:
                continue
            bins.add(int(t // CAD_BIN))
            v = sum(vals) / len(vals)
            sp.add(v, h)
            if still:
                nstill += 1
                sp_still.add(v, h)
            for dd in dirs:
                th = math.radians(dd)
                sx += math.sin(th)
                sy += math.cos(th)
                n_dir += 1
                sect[int(((dd + 11.25) % 360) // 22.5)] += 1
        d["ws_rows"] = nrow
        d["ws_bad"] = nbad
        d["ws_cov"] = round(len(bins) / NBINS, 3)
        d["ws_still_frac"] = round(nstill / max(sp.n, 1), 3)
        sp.out("wind", d)
        if n_dir:
            d["wind_dir_mean"] = round(math.degrees(math.atan2(sx, sy)) % 360, 1)
            d["wind_dir_R"] = round(math.hypot(sx, sy) / n_dir, 3)
            d["wind_dir_mode16"] = max(range(16), key=lambda i: sect[i])
        sp_still.out("windstill", d)
    for prod, col, key, lo, hi in (
        ("DER_PS", "PRESSURE", "p", 100, 2000),
        ("DER_RHS", "LOCAL_RELATIVE_HUMIDITY", "rh", 0, 100),
    ):
        f = find(root, "data_derived_env", sol, prod)
        if not f:
            continue
        ci = header(f).index(col)
        ac = Acc(keep=True)
        bins = set()
        nrow = nfill = nout = 0
        for r in read(f):
            nrow += 1
            try:
                s, t = lmst(r[1])
                if s != sol:
                    continue
                x = r[ci]
                if x == "" or x == FILL:
                    nfill += 1
                    continue
                x = float(x)
            except (ValueError, IndexError):
                nfill += 1
                continue
            if not (lo <= x <= hi):
                nout += 1
                continue
            bins.add(int(t // CAD_BIN))
            ac.add(x, int(t // 3600))
        d[key + "_rows"] = nrow
        d[key + "_fill"] = nfill
        d[key + "_outrange"] = nout
        d[key + "_cov"] = round(len(bins) / NBINS, 3)
        ac.out(key, d, 3 if key == "rh" else 2)
    return d


if __name__ == "__main__":
    root, out = sys.argv[1], sys.argv[2]
    mx = int(sys.argv[3]) if len(sys.argv) > 3 else None
    sols = sorted(
        {
            int(m.group(1))
            for p in glob.glob(f"{root}/meda/data_calibrated_env/sol_*/sol_*/*CAL_ATS*.CSV")
            for m in [re.search(r"WE__(\d{4})_", os.path.basename(p))]
        }
    )
    if mx:
        sols = sols[:mx]
    t0 = time.time()
    with Pool(14) as p:
        res = p.map(do_sol, [(root, s) for s in sols], chunksize=1)
    cols = []
    for r in res:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(out, "w") as f:
        f.write(",".join(cols) + "\n")
        for r in res:
            f.write(",".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + "\n")
    print(f"sols={len(res)} cols={len(cols)} time={time.time() - t0:.0f}s")
