"""Stage 7: EXPLAIN ANALYZE of v2 listing edge cases (rare sample_type, deep page). Read-only."""

import asyncio
import re

import asyncpg

DSN = "postgresql://mars:mars@localhost:5433/mars"
COLS = "photos.*"
ORDER = "ORDER BY sol DESC, taken_at DESC, id DESC LIMIT $3 OFFSET $4"
CASES = [
    (
        "list sample_type absent (per subframe)",
        f"SELECT {COLS} FROM photos WHERE rover_id=$1 AND sample_type=$2 {ORDER}",
        ("perseverance", "subframe", 25, 0),
    ),
    (
        "list sample_type rare (cur mixed)",
        f"SELECT {COLS} FROM photos WHERE rover_id=$1 AND sample_type=$2 {ORDER}",
        ("curiosity", "mixed", 100, 20000),
    ),
    (
        "list no filter deep page (cur off 700k)",
        f"SELECT {COLS} FROM photos WHERE rover_id=$1 AND $2::text IS NOT NULL {ORDER}",
        ("curiosity", "x", 100, 700000),
    ),
    (
        "list camera deep page (per MCZ_LEFT off 170k)",
        f"SELECT {COLS} FROM photos WHERE rover_id=$1 AND camera_id=(SELECT id FROM cameras "
        f"WHERE rover_id=$1 AND code=$2) {ORDER}",
        ("perseverance", "MCZ_LEFT", 100, 170000),
    ),
]


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        async with conn.transaction(readonly=True):
            for name, sql, params in CASES:
                plan = [
                    r[0] for r in await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + sql, *params)
                ]
                text = "\n".join(plan)
                ex = re.search(r"Execution Time: ([\d.]+)", text).group(1)
                nodes = sorted(
                    set(
                        re.findall(
                            r"((?:Parallel )?(?:Seq|Index Only|Index|Bitmap Heap)"
                            r" Scan(?: Backward)?)(?: using (\w+))? on photos",
                            text,
                        )
                    )
                )
                buf = next((ln.strip() for ln in plan if "Buffers:" in ln), "")
                print(f"{name:45} {ex:>9} ms  {buf[:48]:48} {nodes}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
