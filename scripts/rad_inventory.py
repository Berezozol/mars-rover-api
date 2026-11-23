# ruff: noqa: E501  (one-off survey script, long diagnostic f-strings)
"""Stage 21 phase A: streaming inventory of a RAD RDR daily .TXT (stdlib only, no network).

Usage: python -I rad_inventory.py FILE [--examples N]
Prints per-section-type counts, byte shares and the first example (N lines) of each.
"""

import re
import sys
import time
from collections import Counter, defaultdict

path = sys.argv[1]
nex = int(sys.argv[2]) if len(sys.argv) > 2 else 8
hdr = re.compile(rb"^\[([A-Z0-9_]+)(?::\s*(\d+))?\]\s*$")
count = Counter()
size = Counter()
lines = Counter()
examples = defaultdict(list)
cur = "HEADER"
t0 = time.time()
total = 0
maxline = 0
order = []
with open(path, "rb") as f:
    for raw in f:
        total += len(raw)
        maxline = max(maxline, len(raw))
        m = hdr.match(raw.rstrip(b"\r\n"))
        if m:
            cur = m.group(1).decode()
            count[cur] += 1
            if cur not in order:
                order.append(cur)
        size[cur] += len(raw)
        lines[cur] += 1
        if count[cur] == 1 and len(examples[cur]) < nex:
            examples[cur].append(raw.decode("ascii", "replace").rstrip()[:170])
print(f"total bytes {total}, max line {maxline}, parse {time.time() - t0:.1f}s")
for k in order + ["HEADER"]:
    if k in size:
        print(
            f"{k:34s} n={count[k]:5d} bytes={size[k]:10d} {100 * size[k] / total:5.1f}% lines={lines[k]}"
        )
if True:  # always print examples
    for k in order:
        print("\n=====", k)
        print("\n".join(examples[k]))
