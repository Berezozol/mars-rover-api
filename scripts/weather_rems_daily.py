# ruff: noqa: E501, SIM115, UP031, B905
"""Daily per-sol summary of REMS RMD tables from local raw files (no network).

Usage: python -I weather_rems_daily.py <weather_raw dir> <out.csv>
Columns are taken by position (label MODRDR6.FMT, field order from the .xml label).
"""

import glob
import math
import re
import sys
import time
from multiprocessing import Pool

NBINS = 148
NA = -999.0
# 0-based column indexes (field_number - 1)
HWS, WDIR, WSM = 3, 5, 6
BT, GTSM = 7, 10
AMB, AMBM = 15, 16
UVABC, UVM = 20, 29
RH, HSM = 30, 35
P, PSM = 37, 39


class Acc:
    __slots__ = ("mn", "mx", "s", "n", "hs", "hn")

    def __init__(self):
        self.mn, self.mx, self.s, self.n = 1e30, -1e30, 0.0, 0
        self.hs, self.hn = [0.0] * 25, [0] * 25

    def add(self, v, h):
        if v < self.mn:
            self.mn = v
        if v > self.mx:
            self.mx = v
        self.s += v
        self.n += 1
        self.hs[h] += v
        self.hn[h] += 1

    def out(self, pre, d, nd=2):
        if not self.n:
            return
        d[pre + "_min"] = round(self.mn, nd)
        d[pre + "_max"] = round(self.mx, nd)
        d[pre + "_mean"] = round(self.s / self.n, nd)
        hm = [a / b for a, b in zip(self.hs, self.hn) if b]
        d[pre + "_hmean"] = round(sum(hm) / len(hm), nd)
        d[pre + "_hours"] = len(hm)
        d[pre + "_n"] = self.n


def m(r, i):
    return r[i].strip().strip('"').strip()


def do_file(path):
    path = path.replace("\\", "/")
    sol = int(re.search(r"SOL(\d{5})/", path).group(1))
    d = {"sol": sol, "file": path.split("/")[-1][-24:]}
    keys = [
        "amb_all",
        "amb_good",
        "bt_all",
        "bt_good",
        "p_all",
        "p_good",
        "rh",
        "uv",
        "wind_all",
        "wind_good",
    ]
    a = {k: Acc() for k in keys}
    bins = set()
    nrow = off = 0
    sx = sy = 0.0
    nd = 0
    with open(path) as f:
        for line in f:
            r = line.split(",")
            if len(r) != 40:
                continue
            nrow += 1
            lm = m(r, 1)
            if int(lm[:5]) != sol:
                off += 1
                continue
            t = int(lm[6:8]) * 3600 + int(lm[9:11]) * 60 + float(lm[12:])
            h = int(t // 3600)
            bins.add(int(t // 600))
            v = float(r[AMB])
            if v != NA:
                a["amb_all"].add(v, h)
                k = m(r, AMBM)
                if len(k) == 9 and k[7] == "1" and k[8] == "1":
                    a["amb_good"].add(v, h)
            v = float(r[BT])
            if v != NA:
                a["bt_all"].add(v, h)
                k = m(r, GTSM)
                if k[:2] == "11":
                    a["bt_good"].add(v, h)
            v = float(r[P])
            if v != NA:
                a["p_all"].add(v, h)
                k = m(r, PSM)
                if k[1:2] == "1":
                    a["p_good"].add(v, h)
            v = float(r[RH])
            if v != NA and 0 <= v <= 100:
                a["rh"].add(v, h)
            v = float(r[UVABC])
            if v != NA and v >= 0:
                a["uv"].add(v, h)
            v = float(r[HWS])
            if v != NA:
                a["wind_all"].add(v, h)
                k = m(r, WSM)
                if len(k) == 6 and k[2] == "1" and k[5] == "1":
                    a["wind_good"].add(v, h)
                dd = float(r[WDIR])
                if dd != NA:
                    sx += math.sin(math.radians(dd))
                    sy += math.cos(math.radians(dd))
                    nd += 1
    d["rows"] = nrow
    d["offsol"] = off
    d["cov"] = round(len(bins) / NBINS, 3)
    for k, ac in a.items():
        ac.out(k, d, 3 if k == "rh" else 2)
    if nd:
        d["wdir_mean"] = round(math.degrees(math.atan2(sx, sy)) % 360, 1)
        d["wdir_R"] = round(math.hypot(sx, sy) / nd, 3)
    return d


if __name__ == "__main__":
    root, out = sys.argv[1], sys.argv[2]
    files = sorted(glob.glob(f"{root}/rems/DATA/SOL_*/SOL*/*RMD*.TAB"))
    t0 = time.time()
    with Pool(12) as p:
        res = p.map(do_file, files, chunksize=1)
    cols = []
    for r in res:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(out, "w") as f:
        f.write(",".join(cols) + "\n")
        for r in res:
            f.write(",".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + "\n")
    print(f"files={len(res)} time={time.time() - t0:.1f}s")
