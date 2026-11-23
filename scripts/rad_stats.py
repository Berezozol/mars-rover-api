# ruff: noqa: E501  (one-off survey script, long diagnostic f-strings)
"""Stage 21 phase A: streaming statistics of a RAD RDR daily .TXT (stdlib only, no network).

Usage: python -I rad_stats.py FILE
"""

import re
import sys
import time
from collections import Counter, defaultdict

path = sys.argv[1]
hdr = re.compile(r"^\[([A-Z0-9_]+)(?::\s*(\d+))?\]\s*$")
kv = re.compile(r"^([A-Z0-9_]+)=(.*)$")
obs = {}
cur_obs = None
sec = "FILE"
fileh = {}
doses = {"B": [], "E": []}
dose_tokens = Counter()
pha_rows = Counter()
l1 = defaultdict(lambda: defaultdict(float))  # name -> field sums over full obs
l1_n = 0
l2 = defaultdict(float)
l2_logic = {}
hist = {"NEUTRAL_D": [], "NEUTRAL_E": [], "LET_B_A1": [], "LET_B_A2": []}
hist_edges = {}
row_idx = 0
minus2 = Counter()
t0 = time.time()
with open(path, "rb") as f:
    for raw in f:
        line = raw.decode("ascii", "replace").rstrip("\r\n")
        m = hdr.match(line)
        if m:
            sec = m.group(1)
            n = int(m.group(2)) if m.group(2) else None
            row_idx = 0
            if sec == "OBSERVATION":
                cur_obs = n
                obs[n] = {"sections": set(), "pha": 0}
            elif sec != "FILE" and cur_obs is not None:
                obs[cur_obs]["sections"].add(sec)
            continue
        if sec == "FILE":
            k = kv.match(line)
            if k:
                fileh[k.group(1)] = k.group(2)
            continue
        if sec == "OBSERVATION":
            k = kv.match(line)
            if k:
                obs[cur_obs][k.group(1)] = k.group(2).strip('"')
            continue
        if not line.strip():
            continue
        if sec in ("DOSIMETRY_TOTAL_DOSE_B", "DOSIMETRY_TOTAL_DOSE_E"):
            key = sec[-1]
            try:
                v = float(line)
                doses[key].append((cur_obs, v))
            except ValueError:
                dose_tokens[line.strip()] += 1
        elif sec == "PHA":
            if not line.lstrip().startswith(("sclk", "---")):
                obs[cur_obs]["pha"] += 1
        elif sec == "COUNTER_L1":
            p = line.split('"')
            if len(p) == 3 and not line.lstrip().startswith(("ch", "---")):
                name = p[1].strip()
                nums = p[2].split()
                if name != "N/A":
                    l1[name]["slow"] += float(nums[1])
                    l1[name]["dur"] += float(nums[2])
                    l1[name]["alive"] += float(nums[3])
                    l1[name]["n"] += 1
                    if float(nums[0]) == -1:
                        l1[name]["fast_off"] += 1
        elif sec == "COUNTER_L2":
            p = line.split()
            if p and p[0].isdigit():
                l2[int(p[0])] += float(p[1])
                l2_logic[int(p[0])] = line.split('"')[-2] if line.count('"') >= 2 else ""
                l2_logic[int(p[0])] = (
                    line.rsplit('"', 1)[-1] if False else line[-60:].strip().strip('"')
                )
        elif sec.startswith(("HISTOGRAM_", "DOSIMETRY_LET")):
            key = sec.replace("HISTOGRAM_", "")
            if key in hist and not line.startswith(("NUM_", "---")):
                try:
                    vals = [float(x) for x in line.split()]
                except ValueError:
                    continue
                row_idx += 1
                if row_idx == 1:
                    hist_edges[key] = vals
                elif row_idx == 2:
                    hist_edges[key + "_w"] = vals
                elif row_idx == 3:
                    hist[key].append(vals)
print(f"parse {time.time() - t0:.1f}s")
print("FILE header:", fileh)
print("n observation blocks:", len(obs))
kinds = Counter()
sols = Counter()
qc = Counter()
durs = []
secsets = Counter()
for o in obs.values():
    kinds[o.get("EDR_FILENAME", "?").split("_")[1][-3:]] += 1
    sols[o.get("START_OBS_MARS", "?").split()[0]] += 1
    qc[o.get("QC")] += 1
    secsets[tuple(sorted(o["sections"]))] += 1
    durs.append(int(o.get("OBS_DURATION", -1)))
print("EDR code (3 letters) by obs:", dict(kinds))
print("sols by START_OBS_MARS:", dict(sols))
print("QC:", dict(qc))
print("OBS_DURATION min/max/sum:", min(durs), max(durs), sum(durs))
print("section sets:", {len(k): v for k, v in secsets.items()}, "distinct", len(secsets))
for k, v in secsets.items():
    print("  ", v, k)
print(
    "PHA rows per obs: min/max/total",
    min(o["pha"] for o in obs.values()),
    max(o["pha"] for o in obs.values()),
    sum(o["pha"] for o in obs.values()),
)
for k in "BE":
    vs = [v for _, v in doses[k]]
    print(
        f"dose {k}: n={len(vs)} min={min(vs)} max={max(vs)} mean={sum(vs) / len(vs):.4f}; non-numeric tokens={dict(dose_tokens)}"
    )
print("dose obs idx with negative:", [(k, i, v) for k in "BE" for i, v in doses[k] if v < 0][:5])
print(
    "sample row for first/last obs:",
    {
        n: (obs[n].get("START_OBS_MARS"), obs[n].get("START_OBS_UTC"), obs[n].get("OBS_DURATION"))
        for n in (0, 2, max(obs))
    },
)
print(
    "ROVER_* distinct:",
    {
        k: len({o.get(k) for o in obs.values()})
        for k in ("ROVER_LAT", "ROVER_LON", "ROVER_ELEV", "ROVER_AZIMUTH", "ROVER_ZENITH")
    },
)
print("firmware:", Counter(o.get("RAD_FIRMWARE_VERSION") for o in obs.values()))
print(
    "\nL1 per channel (over full obs): mean slow count/obs, mean dur, mean alive%, fast-off share"
)
for name, d in l1.items():
    print(
        f"  {name:7s} slow/obs={d['slow'] / d['n']:10.1f} dur={d['dur'] / d['n']:6.1f} rate/s={d['slow'] / d['dur']:8.2f} alive={d['alive'] / d['n']:6.2f}"
    )
print("\nL2 summed counts per entry:", {k: int(v) for k, v in l2.items()})
print("L2 logic:", l2_logic)
for key, rows in hist.items():
    if rows:
        e = hist_edges[key]
        w = hist_edges.get(key + "_w")
        tot = [sum(r) for r in rows]
        print(
            f"\n{key}: n_obs={len(rows)} bins={len(e)} edge0..last={e[0]}..{e[-1]} width0..last={w[0]}..{w[-1]}; sum over bins: min={min(tot):.3f} max={max(tot):.3f} mean={sum(tot) / len(tot):.3f}; neg values={sum(1 for r in rows for x in r if x < 0)}"
        )
        print("  row0:", rows[0][:14])
