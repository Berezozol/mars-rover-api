# Deploy

The application image is built from the root `Dockerfile`. Production hosting (compose for the
whole stack, proxy, HTTPS, backups, monitoring) is planned for stage 9; this file only covers
building and running the image locally.

## Build

```
docker build -t mars-rover-api .
```

The image: `python:3.13-slim` (pinned), runtime dependencies only (`uv sync --frozen --no-dev`),
non-root user `app` (uid 10001), port 8000, `HEALTHCHECK` on `/health` (unhealthy when the
database is unreachable). It contains `app/`, `migrations/` and `alembic.ini`; no `.env`, no
tests.

## Run against the local dev database

Start Postgres first: `docker compose up -d db` (host port 5433). From a container the host is
`host.docker.internal`.

```
docker run --rm -p 8000:8000 -e DATABASE_URL=postgresql+asyncpg://mars:mars@host.docker.internal:5433/mars mars-rover-api
curl http://localhost:8000/health
```

## Migrations

The container never migrates on start. Apply migrations as a separate step before starting a
new version, with the same image:

```
docker run --rm -e DATABASE_URL=... mars-rover-api alembic upgrade head
```

## Environment variables

All settings come from the environment (`app/core/config.py`; defaults and comments in
`.env.example`). The `.env` file is not part of the image.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://user:password@host:port/db` |
| `ENVIRONMENT` | no | `dev` / `test` / `prod` |
| `LOG_LEVEL` | no | default `INFO` |
| `SYNC_ENABLED` | no | `true` starts the in-process sync scheduler. With several replicas set it on **one** replica only |
| `SYNC_INTERVAL_MINUTES`, `SYNC_OVERLAP_SOLS`, `SYNC_CHUNK_SOLS` | no | sync tuning |
| `UPSTREAM_*`, `M2020_*`, `MSL_*` | no | upstream feeds; defaults work |
| `ADMIN_TOKEN` | no | enables `/api/v2/admin/*`; empty = admin routes return 404. Pass as a secret |
| `CORS_ORIGINS` | no | JSON list, default `["*"]` |
| `DB_CONNECT_TIMEOUT_SECONDS` | no | default 5 |
| `FORWARDED_ALLOW_IPS` | no | uvicorn trusts `X-Forwarded-*` only from these addresses (default `127.0.0.1`); set to the reverse proxy address behind a proxy |

The server runs a single uvicorn process (no `--workers`): the scheduler lives in-process.
