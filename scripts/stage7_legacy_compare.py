"""Stage 7: compare an archived old-API manifest with our per-sol counts by sample_type.

Read-only. Prints, per sol era, how the old count relates to our full / all-frames counts,
plus which count variant (full, all, all minus some sample types) matches the old one best.
"""

import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import asyncpg

DSN = "postgresql://mars:mars@localhost:5433/mars"


async def main(rover: str, path: str, era: int) -> None:
    text = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    manifest = json.loads(text)
    legacy = {int(p["sol"]): int(p["total_photos"]) for p in manifest["photo_manifest"]["photos"]}
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT sol, sample_type, count(*) n FROM photos WHERE rover_id=$1 GROUP BY 1,2",
            rover,
        )
    finally:
        await conn.close()
    ours: dict[int, Counter] = defaultdict(Counter)
    types: set[str] = set()
    for r in rows:
        ours[r["sol"]][r["sample_type"]] = r["n"]
        types.add(r["sample_type"])
    variants = {"full": lambda c: c["full"], "all": lambda c: sum(c.values())}
    for t in sorted(types - {"full"}):
        variants[f"full+{t}"] = lambda c, t=t: c["full"] + c[t]
        variants[f"all-{t}"] = lambda c, t=t: sum(c.values()) - c[t]
    top = max(legacy)
    for lo in range(0, top + 1, era):
        sols = [s for s in legacy if lo <= s < lo + era]
        if not sols:
            continue
        leg = sum(legacy[s] for s in sols)
        line = [f"sols {lo}-{lo + era - 1}: n={len(sols)} old={leg}"]
        stats = []
        for name, fn in variants.items():
            eq = sum(fn(ours[s]) == legacy[s] for s in sols)
            lower = sum(fn(ours[s]) < legacy[s] for s in sols)
            stats.append((eq, -lower, name, sum(fn(ours[s]) for s in sols), lower))
        stats.sort(reverse=True)
        for eq, _, name, total, lower in stats[:3]:
            line.append(f"{name}: sum={total} eq={eq} lower={lower}")
        print(" | ".join(line))
    missing = sorted(s for s in legacy if s not in ours)
    print(f"old sols absent in ours: {len(missing)} {missing[:15]}")
    extra = [s for s in ours if s not in legacy and s <= top]
    print(f"our sols <= old max absent in old: {len(extra)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 500))
