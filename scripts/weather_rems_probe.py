# ruff: noqa: E501, SIM115, UP031, B905
"""Probe REMS RMD TAB files: mask patterns, fill, ranges (local only)."""

import collections
import sys

MASK = {"ws": 6, "gts": 10, "boom1": 12, "boom2": 14, "amb": 16, "uv": 29, "hs": 35, "ps": 39}
NUM = {
    "hws": 3,
    "vws": 4,
    "wdir": 5,
    "bt": 7,
    "b1": 11,
    "b2": 13,
    "amb": 15,
    "uva": 17,
    "uvabc": 20,
    "rh": 30,
    "hst": 31,
    "vmr": 33,
    "p": 37,
    "pu": 38,
}
for path in sys.argv[1:]:
    cnt = collections.defaultdict(collections.Counter)
    vals = collections.defaultdict(list)
    n = 0
    dts = collections.Counter()
    prev = None
    for line in open(path):
        r = line.split(",")
        if len(r) != 40:
            print("bad row", len(r))
            continue
        n += 1
        t = int(r[0])
        if prev is not None:
            dts[t - prev] += 1
        prev = t
        for k, i in MASK.items():
            cnt[k][r[i].strip().strip('"').strip()] += 1
        for k, i in NUM.items():
            v = float(r[i])
            if v != -999.0:
                vals[k].append(v)
    print("==", path.split("/")[-1], "rows", n, "dt", dts.most_common(3))
    for k in MASK:
        print("  mask", k, cnt[k].most_common(4))
    for k in NUM:
        v = sorted(vals[k])
        if v:
            print(f"  {k}: valid={len(v)} min={v[0]} med={v[len(v) // 2]} max={v[-1]}")
        else:
            print(f"  {k}: no valid")
