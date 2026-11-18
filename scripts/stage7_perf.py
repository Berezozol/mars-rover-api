"""Stage 7 performance: EXPLAIN (ANALYZE, BUFFERS) of the SQL behind key endpoints, and
real HTTP latency against a local uvicorn.

  uv run python scripts/stage7_perf.py explain [--plans PATH]
  uv run python scripts/stage7_perf.py http [--runs 5]

`explain` calls the app in-process (httpx ASGITransport), captures every SQL statement an
endpoint runs, then explains each one (read-only transaction) on a separate connection.
`http` starts uvicorn on 127.0.0.1:8099 (SYNC_ENABLED=false) as a child process, measures
each endpoint, and stops the child at the end. Read-only: the DB is never modified.
"""

import argparse
import asyncio
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import asyncpg
import httpx

DSN = "postgresql://mars:mars@localhost:5433/mars"
ENDPOINTS = [
    ("v1 photos sol (cur 0)", "/api/v1/rovers/curiosity/photos?sol=0"),
    ("v1 photos sol page (cur 0)", "/api/v1/rovers/curiosity/photos?sol=0&page=1"),
    ("v1 photos sol (per 1717)", "/api/v1/rovers/perseverance/photos?sol=1717"),
    ("v1 photos sol page (per 1717)", "/api/v1/rovers/perseverance/photos?sol=1717&page=1"),
    ("v1 photos earth_date (cur)", "/api/v1/rovers/curiosity/photos?earth_date=2012-08-06"),
    ("v1 manifest (cur)", "/api/v1/manifests/curiosity"),
    ("v1 manifest (per)", "/api/v1/manifests/perseverance"),
    ("v1 latest (cur)", "/api/v1/rovers/curiosity/latest_photos"),
    ("v1 latest (per)", "/api/v1/rovers/perseverance/latest_photos"),
    ("v2 photos sol (cur 0)", "/api/v2/rovers/curiosity/photos?sol=0"),
    ("v2 photos sol (per 1717)", "/api/v2/rovers/perseverance/photos?sol=1717&page=20"),
    ("v2 photos camera (cur NAV_RIGHT_B)", "/api/v2/rovers/curiosity/photos?camera=NAV_RIGHT_B"),
    ("v2 photos camera (per MCZ_LEFT)", "/api/v2/rovers/perseverance/photos?camera=MCZ_LEFT"),
    (
        "v2 photos sample_type (cur subframe)",
        "/api/v2/rovers/curiosity/photos?sample_type=subframe",
    ),
    ("v2 photos sample_type (cur full)", "/api/v2/rovers/curiosity/photos?sample_type=full"),
    ("v2 photos sample_type (per full)", "/api/v2/rovers/perseverance/photos?sample_type=full"),
    ("v2 photos no filter (cur)", "/api/v2/rovers/curiosity/photos"),
    ("v2 manifest (cur)", "/api/v2/rovers/curiosity/manifest"),
    ("v2 manifest (per)", "/api/v2/rovers/perseverance/manifest"),
    ("v2 cameras (cur)", "/api/v2/rovers/curiosity/cameras"),
    ("v2 cameras (per)", "/api/v2/rovers/perseverance/cameras"),
    ("v2 latest (cur)", "/api/v2/rovers/curiosity/photos/latest"),
    ("v2 latest (per)", "/api/v2/rovers/perseverance/photos/latest"),
]


def _node_summary(plan: list[str]) -> str:
    text = "\n".join(plan)
    scans = sorted(
        set(
            re.findall(
                r"(Seq Scan|Index Only Scan|Index Scan|Bitmap Heap Scan)"
                r"(?: using \w+)? on (\w+)",
                text,
            )
        )
    )
    buffers = next((line for line in plan if "Buffers:" in line), "")
    hit = sum(int(x) for x in re.findall(r"shared hit=(\d+)", buffers))
    read = sum(int(x) for x in re.findall(r"read=(\d+)", buffers))
    ex = re.search(r"Execution Time: ([\d.]+)", text)
    sc = ",".join(f"{k.replace(' Scan', '')}:{t}" for k, t in scans if t != "rovers")
    return f"{float(ex.group(1)) if ex else -1:8.1f} ms  buf hit={hit} read={read}  {sc}"


async def explain(plans_path: Path | None) -> None:
    os.environ["SYNC_ENABLED"] = "false"
    from sqlalchemy import event

    from app.main import create_app

    app = create_app()
    captured: list[tuple[str, tuple]] = []

    @event.listens_for(app.state.engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured.append((statement, tuple(parameters or ())))

    conn = await asyncpg.connect(DSN)
    out: list[str] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for name, path in ENDPOINTS:
            captured.clear()
            resp = await client.get(path)
            print(f"== {name}: HTTP {resp.status_code}, {len(resp.content)} B, {len(captured)} SQL")
            out.append(f"\n######## {name}  {path}  HTTP {resp.status_code}")
            for stmt, params in captured:
                if not stmt.lstrip().upper().startswith(("SELECT", "WITH")):
                    continue
                async with conn.transaction(readonly=True):
                    rows = await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + stmt, *params)
                plan = [r[0] for r in rows]
                first = " ".join(stmt.split())[:90]
                print(f"   {_node_summary(plan)}  | {first}")
                out.append(
                    f"\n-- {' '.join(stmt.split())}\n-- params: {params}\n" + "\n".join(plan)
                )
    await conn.close()
    await app.state.engine.dispose()
    if plans_path:
        await asyncio.to_thread(plans_path.write_text, "\n".join(out), encoding="utf-8")
        print(f"plans saved to {plans_path}")


def http(runs: int) -> None:
    env = {**os.environ, "SYNC_ENABLED": "false", "ADMIN_ENABLED": "false", "LOG_LEVEL": "WARNING"}
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8099",
        "--log-level",
        "warning",
        "--no-access-log",
    ]
    proc = subprocess.Popen(cmd, env=env)
    try:
        with httpx.Client(base_url="http://127.0.0.1:8099", timeout=60) as client:
            for _ in range(60):
                try:
                    if client.get("/health").status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            print(
                f"{'endpoint':40} {'status':>6} {'bytes':>9} "
                f"{'p50 ms':>8} {'max ms':>8} {'cold ms':>8}"
            )
            for name, path in ENDPOINTS:
                times = []
                for _ in range(runs + 1):
                    t0 = time.perf_counter()
                    resp = client.get(path)
                    times.append((time.perf_counter() - t0) * 1000)
                cold, warm = times[0], times[1:]
                print(
                    f"{name:40} {resp.status_code:>6} {len(resp.content):>9} "
                    f"{statistics.median(warm):8.1f} {max(warm):8.1f} {cold:8.1f}"
                )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"uvicorn stopped (exit {proc.returncode})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["explain", "http"])
    p.add_argument("--plans", type=Path)
    p.add_argument("--runs", type=int, default=5)
    a = p.parse_args()
    if a.mode == "explain":
        asyncio.run(explain(a.plans))
    else:
        http(a.runs)
