"""Stage 7: before/after plans of slow v2 count queries. Every trial index is created inside
a transaction that is rolled back, so nothing persists in the database."""

import asyncio
import re

import asyncpg

DSN = "postgresql://mars:mars@localhost:5433/mars"
CAM = (
    "SELECT count(photos.id) FROM photos JOIN cameras ON cameras.id = photos.camera_id "
    "WHERE photos.rover_id = $1 AND cameras.code IN ($2)"
)
CAM_STAR = (
    "SELECT count(*) FROM photos WHERE rover_id = $1 AND camera_id = "
    "(SELECT id FROM cameras WHERE rover_id = $1 AND code = $2)"
)
CAM_COUNTS = (
    "SELECT coalesce(sum(s.total_photos), 0) FROM camera_sol_counts s JOIN cameras c "
    "ON c.id = s.camera_id WHERE s.rover_id = $1 AND c.code = $2"
)
ST = "SELECT count(photos.id) FROM photos WHERE rover_id = $1 AND sample_type = $2"
ST_STAR = "SELECT count(*) FROM photos WHERE rover_id = $1 AND sample_type = $2"
LIST = (
    "SELECT photos.* FROM photos WHERE rover_id = $1 AND sample_type = $2 "
    "ORDER BY sol DESC, taken_at DESC, id DESC LIMIT $3 OFFSET $4"
)
CASES = [
    ("camera count, as now", CAM, ("perseverance", "MCZ_LEFT")),
    ("camera count(*) by camera_id", CAM_STAR, ("perseverance", "MCZ_LEFT")),
    ("camera from camera_sol_counts", CAM_COUNTS, ("perseverance", "MCZ_LEFT")),
    ("sample_type count, as now", ST, ("curiosity", "subframe")),
    ("sample_type count(*)", ST_STAR, ("curiosity", "subframe")),
    ("list sample_type absent (per)", LIST, ("perseverance", "subframe", 25, 0)),
    ("list sample_type rare (cur mixed)", LIST, ("curiosity", "mixed", 100, 20000)),
    ("list sample_type page1 (cur subfr)", LIST, ("curiosity", "subframe", 25, 0)),
]
TRIAL = (
    "CREATE INDEX trial_photos_rover_sample_type ON photos "
    "(rover_id, sample_type, sol DESC, taken_at DESC, id DESC)"
)


def summary(plan: list[str]) -> str:
    text = "\n".join(plan)
    nodes = sorted(
        set(
            re.findall(
                r"((?:Parallel )?(?:Seq|Index Only|Index|Bitmap Heap) Scan)"
                r"(?: using (\w+))? on (photos|camera_sol_counts)",
                text,
            )
        )
    )
    ex = re.search(r"Execution Time: ([\d.]+)", text)
    buf = next((ln.strip() for ln in plan if "Buffers:" in ln), "")
    return f"{ex.group(1) if ex else '?':>8} ms  {buf[:45]:45}  {nodes}"


async def run(conn: asyncpg.Connection, label: str) -> None:
    for name, sql, params in CASES:
        best = None
        for _ in range(3):  # warm: report the best of 3
            rows = await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + sql, *params)
            plan = [r[0] for r in rows]
            t = float(re.search(r"Execution Time: ([\d.]+)", "\n".join(plan)).group(1))
            if best is None or t < best[0]:
                best = (t, plan)
        print(f"[{label}] {name:32} {summary(best[1])}")


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        await run(conn, "before")
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SET LOCAL maintenance_work_mem = '256MB'")
            await conn.execute(TRIAL)
            await conn.execute("ANALYZE photos")
            await run(conn, "after ")
        finally:
            await tr.rollback()
            print("trial index rolled back")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
