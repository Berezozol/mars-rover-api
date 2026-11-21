"""Cold-cache timings and EXPLAIN of the total/keys queries (docs/proposals/overload-protection.md).

Dev database only, read-only (EXPLAIN ANALYZE of SELECTs). Restarts the db container before each
case (cold Postgres shared_buffers; the OS cache of the Docker VM stays). SQL comes from the
repository functions, so it is what the API runs.

    uv run python scripts/overload_cold.py ac14            # one cold run per scan_light row
    uv run python scripts/overload_cold.py ac15 [label]    # EXPLAIN cold + warm: finding-1, near
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.repositories import photos as repo  # noqa: E402
from app.repositories.photos import PhotoFilter  # noqa: E402

CUR_NEAR = (-4.70, 137.40, 50.0)
PER_NEAR = (18.45, 77.30, 50.0)

CASES: dict[str, PhotoFilter] = {
    "sol=1000 + min_width": PhotoFilter("curiosity", sol=1000, min_width=1024),
    "sol=1000 + near r=50 (C)": PhotoFilter("curiosity", sol=1000, near=CUR_NEAR),
    "sol=1000 + local_time": PhotoFilter(
        "curiosity", sol=1000, local_time_ranges=((40000, 50000),)
    ),
    "sol=1000 + sample_type=thumbnail": PhotoFilter("curiosity", sol=1000, sample_type="thumbnail"),
    "earth_date alone": PhotoFilter("curiosity", earth_date=date(2015, 5, 30)),
    "earth_date + camera": PhotoFilter(
        "curiosity", earth_date=date(2015, 5, 30), camera_codes=["MAST"]
    ),
    "earth_date + min_width": PhotoFilter(
        "curiosity", earth_date=date(2015, 5, 30), min_width=1024
    ),
    "earth_date + sample_type=full": PhotoFilter(
        "curiosity", earth_date=date(2015, 5, 30), sample_type="full"
    ),
    "site=105 only": PhotoFilter("curiosity", site=105),
    "site=105 + drive range": PhotoFilter("curiosity", site=105, drive_from=0, drive_to=1000),
    "site_min/max 100..200": PhotoFilter("curiosity", site_min=100, site_max=200),
    "site_min/max 0..100000": PhotoFilter("curiosity", site_min=0, site_max=100000),
    "near only P r=50": PhotoFilter("perseverance", near=PER_NEAR),
    "near only C r=50": PhotoFilter("curiosity", near=CUR_NEAR),
    "near P r=50 + sol range 100..500": PhotoFilter(
        "perseverance", near=PER_NEAR, sol_min=100, sol_max=500
    ),
}

AC15 = {
    "finding-1 count (site 0..100000 + full)": PhotoFilter(
        "curiosity", site_min=0, site_max=100000, sample_type="full"
    ),
    "near P r=50 + full": PhotoFilter("perseverance", near=PER_NEAR, sample_type="full"),
}


def restart_db() -> None:
    subprocess.run(["docker", "compose", "restart", "db"], check=True, capture_output=True)
    for _ in range(60):
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "mars", "-d", "mars"],
            capture_output=True,
        )
        if r.returncode == 0:
            time.sleep(1)
            return
        time.sleep(1)
    raise RuntimeError("db did not come back")


def literal(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def queries(flt: PhotoFilter):
    count_stmt = (
        repo._near_count_query(flt)
        if flt.near
        else repo._filtered(
            repo.select(repo.func.count()).select_from(repo.Photo), flt, bound_taken_at=False
        )
    )
    keys_stmt = repo._keys_query(flt, "-sol", 25, 0, 0)
    return {"count": literal(count_stmt), "keys": literal(keys_stmt)}


async def session_conn(engine):
    conn = await engine.connect()
    await conn.execute(text("SET statement_timeout = '15s'"))
    await conn.execute(text("SET max_parallel_workers_per_gather = 0"))
    await conn.execute(text("SET jit = off"))
    return conn


async def time_query(sql: str) -> float:
    engine = create_async_engine(get_settings().database_url)
    try:
        conn = await session_conn(engine)
        t = time.perf_counter()
        await conn.execute(text(sql))
        ms = (time.perf_counter() - t) * 1000
        await conn.close()
        return ms
    finally:
        await engine.dispose()


async def explain(sql: str, runs: int = 2) -> list[tuple[float, str]]:
    engine = create_async_engine(get_settings().database_url)
    out = []
    try:
        conn = await session_conn(engine)
        for _ in range(runs):
            res = await conn.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql))
            lines = [r[0] for r in res]
            ms = next(float(x.split(":")[1].split()[0]) for x in lines if "Execution Time" in x)
            out.append((ms, "\n".join(lines)))
        await conn.close()
    finally:
        await engine.dispose()
    return out


def main() -> None:
    mode = sys.argv[1]
    if mode == "ac14":
        print("| case | count cold ms | keys cold ms |")
        for name, flt in CASES.items():
            q = queries(flt)
            res = {}
            for kind in ("count", "keys"):
                restart_db()
                res[kind] = asyncio.run(time_query(q[kind]))
            print(f"| {name} | {res['count']:.0f} | {res['keys']:.0f} |", flush=True)
    elif mode == "ac15":
        label = sys.argv[2] if len(sys.argv) > 2 else "run"
        for name, flt in AC15.items():
            q = queries(flt)
            for kind in ("count", "keys"):
                restart_db()
                runs = asyncio.run(explain(q[kind]))
                print(
                    f"=== [{label}] {name} / {kind}: "
                    f"cold {runs[0][0]:.0f} ms, warm {runs[1][0]:.0f} ms"
                )
                print("--- cold plan\n" + runs[0][1])
                print(
                    "--- warm plan (tail)\n" + "\n".join(runs[1][1].splitlines()[-6:]), flush=True
                )


if __name__ == "__main__":
    main()
