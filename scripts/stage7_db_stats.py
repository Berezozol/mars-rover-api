"""Stage 7: compact read-only stats of the `mars` DB (totals, runs, dropped, odd rows)."""

import asyncio
import sys

import asyncpg

DSN = "postgresql://mars:mars@localhost:5433/mars"

QUERIES = {
    "rovers": "SELECT id, max_sol, total_photos, full_total_photos, full_max_sol FROM rovers"
    " ORDER BY id",
    "photos_by_rover": "SELECT rover_id, count(*), count(*) FILTER (WHERE sol IS NULL) AS no_sol,"
    " count(*) FILTER (WHERE sample_type='full') AS full, min(sol), max(sol)"
    " FROM photos GROUP BY 1",
    "runs": "SELECT rover_id, mode, status, count(*), sum(photos_upserted) AS upserted,"
    " sum(source_total) AS src_total FROM sync_runs GROUP BY 1,2,3 ORDER BY 1,2,3",
    "dropped_sols": "SELECT rover_id, sol, received, dropped, source_total FROM sol_source_counts"
    " WHERE dropped > 0 ORDER BY rover_id, sol",
    "sample_types": "SELECT rover_id, sample_type, count(*) FROM photos GROUP BY 1,2 ORDER BY 1,2",
    "largest_sols": "SELECT rover_id, sol, total_photos, full_photos FROM ("
    " SELECT *, row_number() OVER (PARTITION BY rover_id ORDER BY total_photos DESC) rn"
    " FROM sol_summaries) s WHERE rn <= 2 ORDER BY 1, 3 DESC",
    "largest_full_sols": "SELECT rover_id, sol, full_photos FROM ("
    " SELECT *, row_number() OVER (PARTITION BY rover_id ORDER BY full_photos DESC) rn"
    " FROM sol_summaries) s WHERE rn <= 1 ORDER BY 1",
    "table_sizes": "SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid))"
    " FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 8",
}


async def main(names: list[str]) -> None:
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("SET default_transaction_read_only = on")
        for name, sql in QUERIES.items():
            if names and name not in names:
                continue
            rows = await conn.fetch(sql)
            print(f"== {name} ({len(rows)})")
            for row in rows[:40]:
                print("  " + " | ".join(str(v) for v in row.values()))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
