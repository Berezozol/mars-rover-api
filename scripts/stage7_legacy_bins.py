"""Stage 7: per-bin relation of old-API counts to our full / all-frame counts (read-only)."""

import asyncio
import json
import sys
from pathlib import Path

import asyncpg

DSN = "postgresql://mars:mars@localhost:5433/mars"


async def main(rover: str, path: str, lo: int, hi: int, step: int) -> None:
    text = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    manifest = json.loads(text)
    legacy = {int(p["sol"]): int(p["total_photos"]) for p in manifest["photo_manifest"]["photos"]}
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        "SELECT sol, total_photos, full_photos FROM sol_summaries WHERE rover_id=$1", rover
    )
    await conn.close()
    ours = {r["sol"]: (r["total_photos"], r["full_photos"]) for r in rows}
    print("bin: n | old=full | old<full | full<old<=all | old>all | old_sum full_sum all_sum")
    for b in range(lo, hi + 1, step):
        sols = [s for s in legacy if b <= s < b + step]
        if not sols:
            continue
        c = [0, 0, 0, 0]
        for s in sols:
            a, f = ours.get(s, (0, 0))
            o = legacy[s]
            c[0 if o == f else 1 if o < f else 2 if o <= a else 3] += 1
        sums = (
            sum(legacy[s] for s in sols),
            sum(ours.get(s, (0, 0))[1] for s in sols),
            sum(ours.get(s, (0, 0))[0] for s in sols),
        )
        print(f"{b}-{b + step - 1}: {len(sols)} | {c[0]} | {c[1]} | {c[2]} | {c[3]} | {sums}")
    for s in map(int, sys.argv[6:]):
        print(f"sol {s}: old={legacy.get(s)} ours(all,full)={ours.get(s)}")


if __name__ == "__main__":
    a = sys.argv
    asyncio.run(main(a[1], a[2], int(a[3]), int(a[4]), int(a[5])))
