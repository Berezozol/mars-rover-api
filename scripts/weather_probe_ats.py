# ruff: noqa: E501, SIM115, UP031, B905
"""Probe CAL_ATS / CAL_WS files for a few sols (local only)."""

import csv
import glob
import sys

root = sys.argv[1]


def load(prod, sol):
    f = glob.glob(
        f"{root}/meda/data_calibrated_env/sol_*/sol_{sol:04d}/WE__{sol:04d}___________{prod}_*.CSV"
    )[0]
    return f, list(csv.reader(open(f)))


for sol in map(int, sys.argv[2:]):
    for prod in ("CAL_ATS", "CAL_WS"):
        f, rows = load(prod, sol)
        h, d = rows[0], rows[1:]
        print(f"--- {f.split('/')[-1]} rows={len(d)}")
        t = [float(r[0]) for r in d]
        dts = [round(b - a, 2) for a, b in zip(t, t[1:])]
        from collections import Counter

        print(
            " dt top:",
            Counter(dts).most_common(4),
            " gaps>60s:",
            sum(1 for x in dts if x > 60),
            " sum gaps>60:",
            round(sum(x for x in dts if x > 60)),
        )
        print(" first/last LMST", d[0][1], d[-1][1], " sol set:", {r[1][:5] for r in d})
        for i in range(3, len(h)):
            vals = [r[i] for r in d]
            empty = sum(1 for v in vals if v == "")
            fill = sum(1 for v in vals if v == "999999999")
            num = [float(v) for v in vals if v not in ("", "999999999")]
            if num:
                num.sort()
                print(
                    f"  {h[i]}: empty={empty} fill={fill} min={num[0]} p1={num[len(num) // 100]} med={num[len(num) // 2]} p99={num[-len(num) // 100 - 1]} max={num[-1]}"
                )
            else:
                print(f"  {h[i]}: empty={empty} fill={fill} (no numbers)")
