"""Live overload probe (AC-13, docs/proposals/overload-protection.md).

Starts a local uvicorn on the dev database (rate limit off, default heavy gate), hammers heavy
requests with 20 workers for DURATION seconds, probes light requests every 200 ms and samples
pg_stat_activity. Local only, no external requests. Output: JSON on stdout.

    uv run python scripts/overload_probe.py [duration_seconds]
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
DSN = "postgresql://mars:mars@localhost:5433/mars"
WORKERS = 20

HEAVY = [
    "/api/v2/rovers/curiosity/photos?date_min=2012-08-06&sample_type=full",
    "/api/v2/photos?rovers=curiosity,perseverance&sample_type=full&min_width=1"
    "&sort=-taken_at&page=100&per_page=100",
    "/api/v2/photos?rovers=perseverance&near=18.45,77.30&radius_km=50&camera=NAVCAM_LEFT",
    "/api/v2/photos?rovers=curiosity&near=-4.70,137.40&radius_km=50&camera=MAST&sort=-taken_at",
]
LIGHT = {
    "v2_sol": "/api/v2/rovers/curiosity/photos?sol=1000",
    "v1_sol": "/api/v1/rovers/curiosity/photos?sol=1000",
    "v2_rovers": "/api/v2/rovers",
}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * p))], 1)


async def timed(client: httpx.AsyncClient, url: str) -> tuple[int, float]:
    t = time.perf_counter()
    try:
        r = await client.get(url)
        code = r.status_code
    except httpx.HTTPError as exc:
        code = -1 if not isinstance(exc, httpx.TimeoutException) else -2
    return code, (time.perf_counter() - t) * 1000


async def run(duration: float) -> dict:
    stop = time.monotonic() + duration
    heavy: dict[str, list[tuple[int, float]]] = {u: [] for u in HEAVY}
    light: dict[str, list[tuple[int, float]]] = {k: [] for k in LIGHT}
    max_conn = 0
    limits = httpx.Limits(max_connections=60)
    async with (
        httpx.AsyncClient(base_url=BASE, timeout=60, limits=limits) as hc,
        httpx.AsyncClient(base_url=BASE, timeout=60) as lc,
    ):

        async def hworker(i: int) -> None:
            n = i
            while time.monotonic() < stop:
                url = HEAVY[n % len(HEAVY)]
                n += 1
                heavy[url].append(await timed(hc, url))

        async def lprobe() -> None:
            while time.monotonic() < stop:
                for k, u in LIGHT.items():
                    light[k].append(await timed(lc, u))
                await asyncio.sleep(0.2)

        async def sampler() -> None:
            nonlocal max_conn
            conn = await asyncpg.connect(DSN)
            try:
                while time.monotonic() < stop:
                    n = await conn.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname='mars' "
                        "AND pid <> pg_backend_pid() AND backend_type='client backend'"
                    )
                    max_conn = max(max_conn, n)
                    await asyncio.sleep(0.25)
            finally:
                await conn.close()

        await asyncio.gather(*(hworker(i) for i in range(WORKERS)), lprobe(), sampler())

    lall = [x for v in light.values() for x in v]
    hall = [x for v in heavy.values() for x in v]
    h503 = [ms for c, ms in hall if c == 503]
    return {
        "duration_s": duration,
        "light": {
            "requests": len(lall),
            "status": dict(Counter(c for c, _ in lall)),
            "p50_ms": pct([ms for _, ms in lall], 0.5),
            "p95_ms": pct([ms for _, ms in lall], 0.95),
            "max_ms": round(max((ms for _, ms in lall), default=0), 1),
            "by_kind": {
                k: {"p50": pct([ms for _, ms in v], 0.5), "p95": pct([ms for _, ms in v], 0.95)}
                for k, v in light.items()
            },
        },
        "heavy": {
            "requests": len(hall),
            "status": dict(Counter(c for c, _ in hall)),
            "503_p50_ms": pct(h503, 0.5),
            "503_p95_ms": pct(h503, 0.95),
            "503_max_ms": round(max(h503, default=0), 1),
            "200_p50_ms": pct([ms for c, ms in hall if c == 200], 0.5),
            "200_p95_ms": pct([ms for c, ms in hall if c == 200], 0.95),
            "by_url": {u: dict(Counter(c for c, _ in v)) for u, v in heavy.items()},
        },
        "max_app_connections": max_conn,
    }


def main() -> None:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://mars:mars@localhost:5433/mars",
        "RATE_LIMIT_ENABLED": "false",
        "DB_POOL_SIZE": "10",
        "DB_MAX_OVERFLOW": "5",
    }
    env.pop("HEAVY_QUERY_CONCURRENCY", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
    )
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{BASE}/api/v2/rovers", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise SystemExit("server did not start")
        result = asyncio.run(run(duration))
        result["heavy_query_concurrency"] = "default"
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
