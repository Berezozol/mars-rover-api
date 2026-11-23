"""Perseverance ground-temperature completeness rule on the full local data (no network).

Usage: uv run python scripts/weather_ground_rule.py [output.json]

Reads the DER_TIRS files of the local weather copy (paths from the weather_files table, read-only)
with the application reader, so the samples are exactly those the parser uses. For each sol it
finds the LMST hours that have filtered samples and how the current rule (>= 18 hours) and
candidate rules classify it. Writes per-sol numbers as JSON when a path is given.
"""

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import asyncpg

from app.core.config import get_settings
from app.services.weather_sync import raw_root
from app.sources import weather as src


def read_sol(args: tuple[int, str]) -> tuple[int, dict[int, list[float]]]:
    sol, path = args
    hours: dict[int, list[float]] = defaultdict(list)
    for lmst_s, _param, value in src.iter_meda_tirs(Path(path), sol):
        hours[min(int(lmst_s // 3600), 23)].append(value)
    return sol, hours


async def paths() -> list[tuple[int, str]]:
    root = raw_root(get_settings())
    conn = await asyncpg.connect("postgresql://mars:mars@localhost:5433/mars")
    rows = await conn.fetch(
        "select sol, path from weather_files where rover_id='perseverance' "
        "and product='DER_TIRS' and status='parsed' order by sol"
    )
    await conn.close()
    return [(r["sol"], str(root / "meda" / r["path"])) for r in rows]


def main() -> None:
    items = asyncio.run(paths())
    with ProcessPoolExecutor(12) as pool:
        results = dict(pool.map(read_sol, items, chunksize=8))
    per_sol = {}
    for sol, hours in results.items():
        means = {h: statistics.fmean(v) for h, v in hours.items()}
        per_sol[sol] = {
            "hours": sorted(hours),
            "n": sum(len(v) for v in hours.values()),
            "min": min(means.values()) if means else None,
            "max": max(means.values()) if means else None,
            "n_pre": sum(len(hours.get(h, ())) for h in (3, 4, 5, 6)),
            "n_noon": sum(len(hours.get(h, ())) for h in (11, 12, 13, 14)),
        }
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(per_sol))
    print(len(per_sol), "sols with a DER_TIRS file")


if __name__ == "__main__":
    main()
