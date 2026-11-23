# ruff: noqa: E501, SIM115, UP031, B905
"""Compare MEDA inventories with CSV files on disk (local only, no network)."""

import collections
import csv
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
for coll in ("data_derived_env", "data_calibrated_env"):
    d = root / "meda" / coll
    inv = collections.defaultdict(set)
    for r in csv.reader(open(d / f"collection_meda_{coll}_inventory.csv")):
        m = re.search(r":we__(\d{4})_+([a-z_]+?)_+(p\w?)::", r[1])
        if m:
            inv[m.group(2)].add(int(m.group(1)))
    disk = collections.defaultdict(set)
    for f in d.rglob("*.CSV"):
        m = re.match(r"WE__(\d{4})_+([A-Z_]+?)_+P\w+\.CSV", f.name)
        if m:
            disk[m.group(2).lower()].add(int(m.group(1)))
    print(coll)
    for p in sorted(set(inv) | set(disk)):
        a, b = inv[p], disk[p]
        print(
            f"  {p}: inv={len(a)} disk={len(b)} missing={len(a - b)} extra={len(b - a)} {sorted(a - b)[:10]}"
        )
