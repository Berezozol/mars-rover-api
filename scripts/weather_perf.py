"""Stage 17 AC-40: weather endpoint timings and EXPLAIN of their queries (dev db, read-only).

    uv run python scripts/weather_perf.py

Goes through the ASGI app (no network). The db container is restarted before each cold case.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import statistics
import sys
import time
from pathlib import Path

os.environ["RATE_LIMIT_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from stage18c_perf_helpers import restart_db  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


async def case(name: str, urls: list[str], explain: bool = True) -> None:
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
            times = []
            for url in urls:
                t = time.perf_counter()
                r = await client.get(url, headers={"Accept-Encoding": "identity"})
                times.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, (url, r.status_code)
            raw = len(r.content)
            gz = len(gzip.compress(r.content, 6))
            n_cold = len(captured)
            print(
                f"=== {name}: first {times[0]:.0f} ms, rest p50 {statistics.median(times[1:]):.0f} "
                f"max {max(times[1:]):.0f} ms (n={len(times) - 1}); body {raw} B, gzip6 ~{gz} B; "
                f"statements first request {n_cold // len(urls) if len(urls) > 1 else n_cold}"
            )
    if not explain:
        return
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        seen = set()
        for st, p in captured:
            key = st.split(" FROM ")[-1][:200]
            if not st.lstrip().upper().startswith("SELECT") or key in seen:
                continue
            seen.add(key)
            res = await conn.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS) " + st, p)
            print("--- " + st[:60].replace("\n", " ") + " ... " + key[:120].replace("\n", " "))
            print("\n".join(x[0] for x in res))
    await engine.dispose()


async def existing(rover: str, count: int) -> list[int]:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT sol FROM weather_daily WHERE rover_id = $1 ORDER BY sol", (rover,)
        )
        sols = [r[0] for r in rows]
    await engine.dispose()
    step = max(1, len(sols) // count)
    return sols[::step][:count]


async def main() -> None:
    base = "/api/v2/rovers/{}/weather"
    sols = await existing("perseverance", 31)
    await case(
        "sol detail perseverance (31 distinct sols, first is cold)",
        [base.format("perseverance") + f"/{s}" for s in sols],
    )
    await case(
        "sol detail curiosity (distinct sols)",
        [base.format("curiosity") + f"/{s}" for s in await existing("curiosity", 21)],
        explain=False,
    )


asyncio.run(main())
