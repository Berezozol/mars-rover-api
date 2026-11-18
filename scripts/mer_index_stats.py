"""Offline analysis of cached MER PDS indexes (data/mer/<volume>/index/edrindex.{lbl,tab}).

No network, no database. Verifies the stage-8 frame key
(``pid[:11] + pid[14:25]``, pick EFF > ESF > EDN, then highest version), counts
frames per rover / camera / chosen type, and can write small real samples for
qa-engineer and pick a spread of browse-JPEG URLs to HEAD-check.

Usage:
    uv run python scripts/mer_index_stats.py [--samples DIR] [--head-sample FILE]
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "data" / "mer"
BASE = "https://planetarydata.jpl.nasa.gov/img/data/mer"
ROVERS = {"1": "opportunity", "2": "spirit"}
INSTR = ("no", "ho", "po", "mo", "do")
KEEP = ("EFF", "ESF", "EDN")
RANK = {"EFF": 0, "ESF": 1, "EDN": 2}
CAMERA = {
    "NAVCAM_LEFT": "NAVCAM",
    "NAVCAM_RIGHT": "NAVCAM",
    "FRONT_HAZCAM_LEFT": "FHAZ",
    "FRONT_HAZCAM_RIGHT": "FHAZ",
    "REAR_HAZCAM_LEFT": "RHAZ",
    "REAR_HAZCAM_RIGHT": "RHAZ",
    "PANCAM_LEFT": "PANCAM",
    "PANCAM_RIGHT": "PANCAM",
    "MI": "MI",
    "DESCAM": "ENTRY",
}


def label_columns(lbl: Path) -> tuple[list[str], int]:
    """Field names in tab order (ITEMS expanded as NAME#i) and ROWS."""
    text = lbl.read_text(encoding="ascii", errors="replace")
    names = []
    for body in re.findall(r"OBJECT\s*=\s*COLUMN(.*?)END_OBJECT\s*=\s*COLUMN", text, re.S):
        name = re.search(r"^\s*NAME\s*=\s*(\S+)", body, re.M).group(1)
        items = re.search(r"^\s*ITEMS\s*=\s*(\d+)", body, re.M)
        n = int(items.group(1)) if items else 1
        names += [name] if n == 1 else [f"{name}#{i}" for i in range(n)]
    rows = int(re.search(r"^\s*ROWS\s*=\s*(\d+)", text, re.M).group(1))
    return names, rows


def read_rows(vol: str):
    d = ROOT / vol / "index"
    names, rows = label_columns(d / "edrindex.lbl")
    with (d / "edrindex.tab").open("rb") as fh:
        for raw in fh:
            f = [
                c.strip().strip('"').strip() for c in raw.decode("ascii").rstrip("\r\n").split("\t")
            ]
            if len(f) != len(names):
                yield None, raw
                continue
            yield dict(zip(names, f, strict=True)), raw


def browse_url(r: dict, vol: str) -> str:
    path = r.get("PATH_NAME") or f"/{vol}/data/sol{int(r['PLANET_DAY_NUMBER']):04d}/edr/"
    fname = r.get("FILE_NAME") or r["PRODUCT_ID"].lower() + ".img"
    return BASE + path.replace("/data/", "/browse/") + fname + ".jpg"


def analyze_volume(vol: str, report: dict, samples: dict | None, heads: list) -> None:
    names, declared = label_columns(ROOT / vol / "index" / "edrindex.lbl")
    rows = bad = 0
    types: Counter[str] = Counter()
    pid_len: Counter[int] = Counter()
    instr: Counter[str] = Counter()
    groups: dict[str, list[dict]] = defaultdict(list)
    by_sclk: dict[tuple, set] = defaultdict(set)
    rmc_shapes: Counter[str] = Counter()
    raw_by_pid: dict[str, list[bytes]] = defaultdict(list)
    all_sols: list[int] = []
    for r, raw in read_rows(vol):
        rows += 1
        if r is None:
            bad += 1
            continue
        pid = r["PRODUCT_ID"]
        pid_len[len(pid)] += 1
        t = pid[11:14]
        types[t] += 1
        instr[r["INSTRUMENT_ID"]] += 1
        sol = int(r["PLANET_DAY_NUMBER"])
        all_sols.append(sol)
        if samples is not None:
            raw_by_pid[pid].append(raw)  # duplicate PRODUCT_IDs keep every row
        rmc = [r.get(f"ROVER_MOTION_COUNTER#{i}", "") for i in range(5)]
        rmc_shapes[",".join("n" if x.lstrip("-").isdigit() else (x or "-") for x in rmc)] += 1
        if t not in KEEP:
            continue
        rec = {
            "pid": pid,
            "t": t,
            "v": pid[25:],
            "sol": sol,
            "instr": r["INSTRUMENT_ID"],
            "sclk": r["SPACECRAFT_CLOCK_START_COUNT"],
            "start": r["START_TIME"],
            "seq": r["SEQUENCE_ID"],
            "lines": r["LINES"],
            "samples": r["LINE_SAMPLES"],
            "url": browse_url(r, vol),
        }
        groups[pid[:11] + pid[14:25]].append(rec)
        by_sclk[(r["INSTRUMENT_ID"], r["SPACECRAFT_CLOCK_START_COUNT"], r["FILTER_NAME"])].add(
            pid[:11] + pid[14:25]
        )

    # --- frame-key verification
    multi_sol = diff_sclk = diff_start = diff_instr = diff_seq = same_type_dupe = 0
    combos: Counter[str] = Counter()
    chosen: Counter[str] = Counter()
    cam_frames: Counter[str] = Counter()
    cam_full: Counter[str] = Counter()
    cam_sols: dict[str, list[int]] = defaultdict(lambda: [10**6, -1])
    examples: dict[str, list] = defaultdict(list)
    for g in groups.values():
        combos["+".join(sorted({x["t"] for x in g}, key=RANK.get))] += 1
        checks = {
            "multi_sol": len({x["sol"] for x in g}) > 1,
            "diff_sclk": len({x["sclk"] for x in g}) > 1,
            "diff_start": len({x["start"] for x in g}) > 1,
            "diff_instr": len({x["instr"] for x in g}) > 1,
            "diff_seq": len({x["seq"] for x in g}) > 1,
            "same_type_dupe": len(g) != len({x["t"] for x in g}),
        }
        for k, v in checks.items():
            if v and len(examples[k]) < 3:
                examples[k].append([(x["pid"], x["sclk"], x["start"], x["sol"]) for x in g])
        multi_sol += checks["multi_sol"]
        diff_sclk += checks["diff_sclk"]
        diff_start += checks["diff_start"]
        diff_instr += checks["diff_instr"]
        diff_seq += checks["diff_seq"]
        same_type_dupe += checks["same_type_dupe"]
        best = min(g, key=lambda x: (RANK[x["t"]], -ord(x["v"][-1]), [-ord(c) for c in x["pid"]]))
        chosen[best["t"]] += 1
        cam = CAMERA.get(best["instr"], "?" + best["instr"])
        cam_frames[cam] += 1
        if best["t"] == "EFF":
            cam_full[cam] += 1
        lo_hi = cam_sols[cam]
        lo_hi[0] = min(lo_hi[0], best["sol"])
        lo_hi[1] = max(lo_hi[1], best["sol"])
    # copies that failed to merge: same instrument+full SCLK+filter under >1 key
    split = {k: v for k, v in by_sclk.items() if len(v) > 1}
    for k, v in list(split.items())[:3]:
        examples["split_same_sclk"].append([k, sorted(v)])

    kept_rows = sum(len(g) for g in groups.values())
    report[vol] = {
        "rows": rows,
        "declared_rows": declared,
        "unparsed": bad,
        "fields": len(names),
        "pid_len": dict(pid_len),
        "types": dict(types.most_common()),
        "instr": dict(instr),
        "sols": [min(all_sols), max(all_sols)],
        "image_rows": kept_rows,
        "frames": len(groups),
        "chosen": dict(chosen),
        "combos": dict(combos.most_common()),
        "cam_frames": dict(cam_frames),
        "cam_full": dict(cam_full),
        "cam_sols": {k: v for k, v in cam_sols.items()},
        "key_checks": {
            "multi_sol": multi_sol,
            "diff_sclk": diff_sclk,
            "diff_start": diff_start,
            "diff_instr": diff_instr,
            "diff_seq": diff_seq,
            "same_type_dupe": same_type_dupe,
            "split_same_sclk": len(split),
        },
        "rmc_shapes": dict(rmc_shapes.most_common(4)),
        "examples": dict(examples),
    }

    rng = random.Random(8)
    # --- HEAD sample candidates: per type, early / mid / late sol
    by_type: dict[str, list] = defaultdict(list)
    for g in groups.values():
        for x in g:
            by_type[x["t"]].append(x)
    for t, xs in by_type.items():
        xs.sort(key=lambda x: x["sol"])
        for pos in (0.02, 0.5, 0.98):
            x = xs[int(pos * (len(xs) - 1))]
            heads.append(
                {"vol": vol, "t": t, "sol": x["sol"], "instr": x["instr"], "url": x["url"]}
            )

    # --- qa samples: ~30 rows incl. EFF/ESF/EDN/ETH and multi-product frames
    if samples is not None:
        pick: list[str] = []
        multi = [g for g in groups.values() if len({x["t"] for x in g}) > 1]
        dup_ver = [g for g in groups.values() if len(g) != len({x["t"] for x in g})]
        for gs in (multi[:: max(1, len(multi) // 4)][:4], dup_ver[:2]):
            for g in gs:
                pick += [x["pid"] for x in g]
                eth = g[0]["pid"][:11] + "ETH" + g[0]["pid"][14:]
                pick += [p for p in raw_by_pid if p[:11] == eth[:11] and p[11:14] == "ETH"][:1]
        for t in ("EFF", "ESF", "EDN", "ETH", "ERP", "EHG", "ERS", "ECS"):
            cand = [p for p in raw_by_pid if p[11:14] == t]
            if cand:
                pick += rng.sample(cand, min(3, len(cand)))
        seen = []
        for p in pick:
            if p not in seen:
                seen.append(p)
        seen = seen[:34]
        samples[vol] = seen
        out = samples["_dir"] / vol
        out.mkdir(parents=True, exist_ok=True)
        (out / "edrindex.lbl").write_bytes((ROOT / vol / "index" / "edrindex.lbl").read_bytes())
        order = {p: i for i, p in enumerate(raw_by_pid)}
        (out / "edrindex.tab").write_bytes(
            b"".join(b"".join(raw_by_pid[p]) for p in sorted(seen, key=order.get))
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=Path, help="write ~30 real rows + label per volume here")
    p.add_argument("--json", type=Path, default=ROOT / "stats.json")
    args = p.parse_args()
    report: dict = {}
    heads: list = []
    samples = {"_dir": args.samples} if args.samples else None
    for rv in ("1", "2"):
        for ins in INSTR:
            vol = f"mer{rv}{ins}_0xxx"
            analyze_volume(vol, report, samples, heads)
            r = report[vol]
            print(
                f"{vol}: rows={r['rows']}/{r['declared_rows']} unparsed={r['unparsed']} "
                f"sols={r['sols']} pid_len={r['pid_len']} frames={r['frames']} "
                f"chosen={r['chosen']} checks={r['key_checks']}"
            )
    report["_heads"] = heads
    args.json.write_text(json.dumps(report, indent=1, default=list))
    print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
