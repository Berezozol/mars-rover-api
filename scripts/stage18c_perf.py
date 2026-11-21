"""Stage 18C AC-20: cold timings through the ASGI app and EXPLAIN of stats / batch SQL.

Dev database only, read-only. Restarts the db container before each case.

    uv run python scripts/stage18c_perf.py
"""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
import sys
import time
from pathlib import Path

os.environ["RATE_LIMIT_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


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


async def prepare() -> tuple[list[str], list[int]]:
    engine = create_async_engine(get_settings().database_url)
    rnd = random.Random(18)
    async with engine.connect() as conn:
        cams = [
            r[0]
            for r in await conn.execute(
                text("SELECT code FROM cameras GROUP BY code ORDER BY count(*) DESC, code LIMIT 10")
            )
        ]
        rovers = (
            await conn.execute(text("SELECT rover_id, min(id), max(id) FROM photos GROUP BY 1"))
        ).all()
        ids: list[int] = []
        per = 100 // len(rovers)
        for k, (rid, lo, hi) in enumerate(rovers):
            n = per + (1 if k < 100 - per * len(rovers) else 0)
            while n:
                row = await conn.execute(
                    text(
                        "SELECT id FROM photos WHERE rover_id=:r AND id >= :x ORDER BY id LIMIT 1"
                    ),
                    {"r": rid, "x": rnd.randint(lo, hi)},
                )
                v = row.scalar()
                if v is not None and v not in ids:
                    ids.append(v)
                    n -= 1
    await engine.dispose()
    rnd.shuffle(ids)
    return cams, ids


async def run_case(name: str, url: str) -> None:
    restart_db()
    app = create_app()
    captured: list[tuple[str, tuple]] = []
    async with app.router.lifespan_context(app):
        event.listen(
            app.state.engine.sync_engine,
            "before_cursor_execute",
            lambda c, cur, st, p, ctx, many: captured.append((st, p)),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            t = time.perf_counter()
            r = await client.get(url)
            cold = (time.perf_counter() - t) * 1000
            warm = []
            for _ in range(3):
                t = time.perf_counter()
                await client.get(url)
                warm.append((time.perf_counter() - t) * 1000)
    print(
        f"=== {name}: status {r.status_code}, bytes {len(r.content)}, cold {cold:.0f} ms, "
        f"warm {min(warm):.0f}-{max(warm):.0f} ms, statements {len(captured)}"
    )
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        seen = set()
        for st, p in captured[: len(captured) // 4 or len(captured)]:
            if not st.lstrip().upper().startswith("SELECT") or st in seen:
                continue
            seen.add(st)
            res = await conn.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS) " + st, p)
            lines = [x[0] for x in res]
            print("--- " + st[:110].replace("\n", " "))
            print("\n".join(lines))
    await engine.dispose()


async def main() -> None:
    cams, ids = await prepare()
    cam_s = ",".join(cams)
    print("cameras:", cam_s, "| ids:", len(ids))
    cases = {
        "stats month all rovers": "/api/v2/photos/stats?group_by=month",
        "stats camera x10 + full": (
            f"/api/v2/photos/stats?group_by=camera&camera={cam_s}&sample_type=full"
        ),
        "batch 100 ids": "/api/v2/photos/batch?ids=" + ",".join(map(str, ids)),
    }
    for name, url in cases.items():
        await run_case(name, url)


asyncio.run(main())
