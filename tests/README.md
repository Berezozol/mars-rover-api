# Running the tests

Start the database first: `docker compose up -d db`. Do not run two test processes against the
same Postgres at once unless they use xdist (each worker then has its own database).

    uv run pytest -q -p no:cacheprovider            # sequential, database `mars_test`
    uv run pytest -q -p no:cacheprovider -n 12      # parallel (recommended)
    uv run pytest -q -m unit                         # fast layer, no database

Under pytest-xdist every worker uses its own database `mars_test_<worker>` (`mars_test_gw0`, ...),
cloned with `CREATE DATABASE ... TEMPLATE mars_test_template`. The template is migrated once per
run under a Postgres advisory lock; leftovers of earlier runs are dropped and recreated, worker
databases are dropped at the end of the session. The Postgres user needs CREATEDB.

Timings (2650 tests, 16 threads, local Postgres in Docker):

| run | time |
|-----|------|
| sequential | 959 s (16 min) |
| `-n 8` | 227 s |
| `-n 12` | 167 s |
| `-n auto` (16) | 166 s |

Beyond ~12 workers there is no gain (Postgres and the CPU are the limit).
