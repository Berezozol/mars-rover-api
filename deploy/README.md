# Deploy

The application image is built from the root `Dockerfile`. Production today runs on the owner's
laptop behind Tailscale Funnel (section "Laptop + Tailscale Funnel"); a VPS with its own domain
comes later.

Contents:

- [Build](#build), [Run against the local dev database](#run-against-the-local-dev-database),
  [Migrations](#migrations), [Environment variables](#environment-variables)
- [Laptop + Tailscale Funnel](#laptop--tailscale-funnel): install, first data transfer, Funnel,
  autostart, updates, backups and restore, logs
- [Monitoring](#monitoring): external uptime monitor, Telegram alerts, alert test
- [Smoke test](#smoke-test)

## Build

```
docker build -t mars-rover-api .
```

The image: `python:3.13-slim` (pinned), runtime dependencies only (`uv sync --frozen --no-dev`),
non-root user `app` (uid 10001), port 8000, `HEALTHCHECK` on `/health` (unhealthy when the
database is unreachable). It contains `app/`, `migrations/`, `alembic.ini` and the built web
interface (`/app/frontend/dist`); no `.env`, no tests, no Node.

The interface is built inside the image: a `node:26` stage runs `npm ci` and `npm run build` in
`frontend/` and only `dist/` is copied into the runtime stage (`FRONTEND_DIST_DIR`, set to
`/app/frontend/dist` in the image).

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

On the laptop this is step 4 of `update.ps1` (`docker compose run --rm app alembic upgrade head`).

## Environment variables

All settings come from the environment (`app/core/config.py`; defaults and comments in
`.env.example`, for the laptop in `deploy/laptop/.env.example`). The `.env` file is not part of
the image.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://user:password@host:port/db` |
| `ENVIRONMENT` | no | `dev` / `test` / `prod` |
| `LOG_LEVEL` | no | default `INFO` |
| `SYNC_ENABLED` | no | `true` starts the in-process sync scheduler. With several replicas set it on **one** replica only |
| `SYNC_INTERVAL_MINUTES`, `SYNC_OVERLAP_SOLS`, `SYNC_CHUNK_SOLS` | no | sync tuning |
| `UPSTREAM_*`, `M2020_*`, `MSL_*` | no | upstream feeds; defaults work. `M2020_FEED_URL` / `MSL_FEED_URL` are the feed URLs (used by the alert test) |
| `ADMIN_TOKEN` | no | enables `/api/v2/admin/*`; empty = admin routes return 404. At least 16 characters. Pass as a secret |
| `CORS_ORIGINS` | no | JSON list, default `["*"]` |
| `DB_CONNECT_TIMEOUT_SECONDS` | no | default 5 |
| `FORWARDED_ALLOW_IPS` | no | uvicorn trusts `X-Forwarded-*` only from these addresses (default `127.0.0.1`); set to the reverse proxy address behind a proxy; comma-separated addresses and CIDR networks. Avoid `*` (the client then controls its own IP). Laptop: Docker gateway ranges (see below) |
| `RATE_LIMIT_ENABLED` | no | default `true`: per-IP limit on `/api/v1`, `/mars-photos/api/v1`, `/api/v2` |
| `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_HOUR` | no | defaults 60 and 2000; exceeding either gives `429` with `Retry-After` |
| `ADMIN_AUTH_MAX_FAILURES`, `ADMIN_AUTH_WINDOW_MINUTES` | no | defaults 10 and 15: after that many `401` from one IP in the window, admin answers `429` to that IP until the window ends |
| `SYNC_STALE_HOURS` | no | default 24: `/health/sync` reports a rover stale when its last successful sync is older |
| `HEALTH_SYNC_CACHE_SECONDS` | no | default 30: in-memory cache of the `/health/sync` result (0 = no cache); the answer may lag the database by up to this long |
| `PHOTOS_STALE_DAYS` | no | default 21: stale when the newest photo is older (Perseverance raw images are published with a delay of over 10 days; solar conjunction can pause data for ~2 weeks) |
| `TELEGRAM_BOT_TOKEN` | no | **secret**. Application alerts are on only when both it and `TELEGRAM_CHAT_ID` are set |
| `TELEGRAM_CHAT_ID` | no | chat (user or group) that receives alerts |
| `TELEGRAM_API_BASE` | no | default `https://api.telegram.org` |
| `ALERT_CHECK_INTERVAL_MINUTES` | no | default 60: how often stale data is checked; failed or partial syncs alert at once |
| `MER_IMAGE_CACHE_DIR` | no | default `data/mer-images` (`/app/data/mer-images` in the image, writable by `app`): disk cache of Spirit/Opportunity full-size JPEGs converted from PDS `.img`. Laptop: a named volume is mounted there, keep the default |
| `MER_IMAGE_CACHE_MAX_MB` | no | default 5120 (5 GB): cache size limit. The cache can be deleted at any time, it is rebuilt on demand |
| `MER_CONVERT_CONCURRENCY` | no | default 2: simultaneous conversions (CPU and memory) |
| `MER_CONVERT_TIMEOUT_SECONDS` | no | default 60: time limit of one conversion |
| `MER_CONVERT_QUEUE` | no | default 8: conversions waiting for a free slot; beyond that the request is refused |
| `MER_PDS_MAX_RPS` | no | default 2: requests per second to NASA PDS from all conversions together |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | no | defaults 10 and 5: connection pool of the HTTP engine (at most size + overflow connections). Startup refuses a pool above 50 combined (Postgres `max_connections` is 100; the rest is for the worker, `pg_dump` and admin sessions). The laptop does not set them, the defaults are right |
| `HEAVY_QUERY_CONCURRENCY` | no | default 2: heavy v2 searches running at once; extra ones get `503 service_busy` (`Retry-After: 5`) immediately. Must be at least 1 and below pool size + overflow. Not set on the laptop |

The server runs a single uvicorn process (no `--workers`): the scheduler, the rate-limit counters
and the alert state live in-process.

## Laptop + Tailscale Funnel

Owner's decision (2026-11-18): free start on a home laptop. Windows 11 Home, Docker Desktop
(WSL2), Wi-Fi. Tailscale Funnel gives the public HTTPS address
`https://mars-api.sungrazing-menkent.ts.net` and proxies it to `http://127.0.0.1:8000`.

```
Internet --HTTPS--> Tailscale Funnel (TLS, on the laptop) --HTTP--> 127.0.0.1:8000
    --> Docker Desktop port forward --> app container (uvicorn) --> db container (Postgres 16)
```

Names: `mars-server` is only the directory on the laptop; the Compose project is `mars`
(`name: mars` in `compose.yml`, `-ProjectName mars` in the scripts) and the database volume is
`mars_pgdata` (explicit `name:`). Neither may be changed.

Files (templates in `deploy/laptop/`, live copy outside the repository in
`C:\Users\<user>\mars-server\`):

| File | What |
|---|---|
| `compose.yml` | project `mars`: `db` (Postgres 16, volume, no published ports), `app` (`mars-rover-api:local`, `127.0.0.1:8000`), restart `unless-stopped`, log rotation 5 x 10 MB |
| `.env` | from `.env.example`: `POSTGRES_PASSWORD`, `PUBLIC_BASE_URL`, `ADMIN_TOKEN`, Telegram, optional tuning. Never committed |
| `backup.ps1` | `pg_dump -Fc` in the db container, `pg_restore --list` check, keeps 7 copies in `backups\`, log `backups\backup.log` |
| `update.ps1` | pull, build, backup, migrate, restart, `/health` check |

Fixed in `compose.yml`: `ENVIRONMENT=prod`, `SYNC_ENABLED=true` (the only instance, so the only
scheduler), `FORWARDED_ALLOW_IPS=127.0.0.1,172.16.0.0/12,192.168.65.0/24`.

Why that list: requests arrive through Docker Desktop's port forwarding, so uvicorn sees the
gateway of the compose network as the peer (`172.19.0.1` in a test on Docker Desktop 29.9,
WSL2), not `127.0.0.1`; with the default `127.0.0.1` it would ignore `X-Forwarded-Proto` and
redirects would point to `http://`. `192.168.65.0/24` is Docker Desktop's own VM network. The
list is deliberately not `*`: with a list uvicorn takes the right-most `X-Forwarded-For` entry
that is not a trusted address (the one the proxy added), while with `*` it takes the left-most
entry, which the client can send itself. The port is published on `127.0.0.1` only, so the sole
peer in these ranges is the local Tailscale proxy. Never publish it on `0.0.0.0`.

All commands below are PowerShell, run in `C:\Users\<user>\mars-server` unless stated otherwise
(`docker compose` there picks up `compose.yml` and `.env`).

### 1. Install

1. Docker Desktop (WSL2 backend). Settings -> General: "Start Docker Desktop when you sign in".
2. Git, and a clone of the repository (for example `C:\Users\<user>\PycharmProjects\mars-rover-api`).
3. Create the server directory and copy the templates:
   ```
   mkdir $env:USERPROFILE\mars-server
   cd $env:USERPROFILE\mars-server
   copy <repo>\deploy\laptop\compose.yml, <repo>\deploy\laptop\backup.ps1, <repo>\deploy\laptop\update.ps1 .
   copy <repo>\deploy\laptop\.env.example .env
   notepad .env
   ```
   Both secrets are empty in the template. `POSTGRES_PASSWORD` is required (compose refuses
   to start without it); `ADMIN_TOKEN` stays empty unless you want the admin API (empty = off).
   Generate each value separately (PowerShell 5.1, one line):
   ```
   $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b).TrimEnd('=').Replace('+','-').Replace('/','_')
   ```
4. Build the image and start the database:
   ```
   docker build -t mars-rover-api:local <repo>
   docker compose up -d --wait db
   ```
5. Either restore the data (step 2) or start from an empty database:
   `docker compose run --rm app alembic upgrade head`, then `docker compose up -d app`.
   An empty database fills only from the sync window onward; full history needs
   `python -m app.cli backfill` / `import-mer` (hours of upstream requests), so prefer the dump.

PowerShell may refuse to run local scripts; run them as
`powershell -NoProfile -ExecutionPolicy Bypass -File .\backup.ps1` or allow signed/local scripts
once with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### 2. First data transfer (dump from the development database)

On the development machine (dev stack from the root `docker-compose.yml`, container
`mars-rover-db`):

```
docker exec mars-rover-db pg_dump -U mars -d mars -Fc -f /tmp/mars-initial.dump
docker exec mars-rover-db pg_restore --list /tmp/mars-initial.dump > $null; $LASTEXITCODE   # 0 = archive is readable
docker cp mars-rover-db:/tmp/mars-initial.dump .
docker exec mars-rover-db rm /tmp/mars-initial.dump
```

The full database (about 1.7 million photos) gives a dump of a few GB; this one-off copy goes
through the container's `/tmp` (the dev stack has no backups mount), so it needs that much free
space in Docker Desktop's disk image for a moment, and the image file does not shrink by itself
afterwards. Copy the dump to the laptop into `mars-server\backups\` (mounted into the db
container as `/backups`), then restore it as in "Restore" below with
`/backups/mars-initial.dump` (it drops and recreates the empty `mars` database). `--no-owner --role=mars` makes every restored object belong to the
`mars` role whatever the owner was in the source. Then bring the schema up to the code and start
the app:

```
docker compose run --rm app alembic upgrade head
docker compose up -d app
```

### 3. Tailscale Funnel

Already set up on the laptop; for a reinstall:

1. Install Tailscale for Windows and sign in. It runs as a Windows service, so it starts at boot
   without a login.
2. Machine name `mars-api`: admin console https://login.tailscale.com/admin/machines -> the
   machine -> "Edit machine name" (or `tailscale set --hostname=mars-api`).
3. Admin console -> DNS (https://login.tailscale.com/admin/dns): enable MagicDNS and
   "HTTPS Certificates".
4. Funnel must be allowed for the node in the tailnet policy (Access controls):
   ```
   "nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]
   ```
   The first `tailscale funnel` command prints a link that adds this for you if it is missing.
5. Start Funnel in the background (the setting survives reboots):
   ```
   tailscale funnel --bg 8000
   tailscale funnel status
   ```
   Expected: `https://mars-api.sungrazing-menkent.ts.net (Funnel on)` -> `proxy http://127.0.0.1:8000`.
   Turn it off: `tailscale funnel --https=443 off` (or `tailscale funnel reset`).
6. Check from a device outside the tailnet (phone on mobile data):
   `https://mars-api.sungrazing-menkent.ts.net/health`, then the smoke test (below).

The first request after a while may take a few seconds (DNS and certificate). Funnel serves only
ports 443, 8443 and 10000 and has bandwidth limits; that is enough for a JSON API.

**Client IP for the rate limit.** The limit is per client IP taken from `X-Forwarded-For`, so
check two things from a machine outside the tailnet (verified on Tailscale 1.104.7, 2026-11-19:
Funnel replaces a client-sent `X-Forwarded-For` with the real address; **repeat this check after
every Tailscale update**):

1. Real address: after any external request, `docker compose logs --tail 20 app` shows the
   client's public IP in the access lines, not a `172.x`/`192.168.65.x`/`127.0.0.1` address.
   If it shows the gateway, the peer is outside `FORWARDED_ALLOW_IPS`: add the shown address to
   that line in `compose.yml` and `docker compose up -d app`. If it shows `127.0.0.1`, Funnel
   sent no `X-Forwarded-For` and all users share one counter: raise
   `RATE_LIMIT_PER_MINUTE`/`RATE_LIMIT_PER_HOUR` or set `RATE_LIMIT_ENABLED=false` in `.env`.
2. No spoofing:
   ```
   curl -H "X-Forwarded-For: 203.0.113.7" https://mars-api.sungrazing-menkent.ts.net/api/v2/rovers
   ```
   The access line of this request must show the real public IP, never `203.0.113.7`. If it
   shows `203.0.113.7`, any client can pick its own rate-limit key: report it and set
   `RATE_LIMIT_ENABLED=false` until it is solved.

**Public base URL (production checklist).** Set `PUBLIC_BASE_URL=https://<funnel-host>` in `.env`
(no trailing slash, the exact Funnel URL), then `docker compose up -d app`. Without it the app
trusts the `Host` header for links in `/developers`, `/llms.txt`, v2 image links and RSS (cache
poisoning, container address leaks). Verify from outside the tailnet:

```
curl -H "Host: evil.example" https://<funnel-host>/llms.txt
```

The links in the output must show `<funnel-host>`, never `evil.example`. The smoke test also
checks that `/llms.txt` contains the base URL it was called with.

### 4. Autostart and keeping the laptop up

The chain after a reboot: Windows auto sign-in -> Docker Desktop starts -> containers with
`restart: unless-stopped` start (db first, the app waits for its healthcheck) -> the screen
locks. Tailscale and Funnel start as a service on their own.

1. Automatic sign-in (Windows 11 Home): Settings -> Accounts -> Sign-in options -> turn off
   "For improved security, only allow Windows Hello sign-in", then `netplwiz` -> clear
   "Users must enter a user name and password". Alternative: Sysinternals Autologon (stores the
   password encrypted in LSA).
2. Lock right after sign-in: Task Scheduler -> Create Task -> Trigger "At log on" of your user,
   Action `rundll32.exe user32.dll,LockWorkStation`, delay 1 minute.
3. Power: plugged in -> never sleep, never hibernate; Control Panel -> Power Options -> "Choose
   what closing the lid does" -> "Do nothing" (plugged in). Wi-Fi: "Connect automatically".
4. Windows Update: set active hours; after an update reboot the chain above brings everything
   back. Check with the external monitor (it will report the downtime).

### 5. Update

From the repository checkout (scripts there are the reviewed versions):

```
powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\deploy\laptop\update.ps1 -RepoDir <repo>
```

`-ComposeDir` defaults to `%USERPROFILE%\mars-server`. Steps: `git pull --ff-only` ->
`docker build -t mars-rover-api:local` -> start db if needed and run a fresh backup ->
`docker compose run --rm app alembic upgrade head` (the old version keeps serving) ->
`docker compose up -d app` -> wait for `200` on `http://127.0.0.1:8000/health` (120 s).
Any failed step stops the script with `UPDATE FAILED at step: ...`; nothing is rolled back.
`-SkipPull` builds what is checked out.

If the new version does not start: `docker compose logs --tail 200 app`. To go back, check out
the previous commit and run `update.ps1 -SkipPull`. Migrations are not downgraded automatically;
if a migration broke the data, restore the backup made by this run (step 3 of the script).

When `compose.yml` itself changes in the repository, apply the change to the live copy in `mars-server`
(for the MER stage not by copying the whole file, see "MER image cache") and run
`docker compose up -d` (it recreates only the changed services; the database volume stays).
Stage 9 changes it: the db service gets the `./backups:/backups` mount that `backup.ps1` needs,
and `FORWARDED_ALLOW_IPS` is no longer `*`. Run the client-IP checks of section 3 afterwards.

#### Release 18B-2: migration 0013 and `backfill-meta`

Migration 0013 adds `photos.local_time_s`, `width`, `height` and three partial indexes
(`CREATE INDEX CONCURRENTLY`). The columns are then filled once from `extra` by
`python -m app.cli backfill-meta` (~1.3M rows, no network; ~2 min on dev, but ~33 min on the laptop).

1. Before updating: check free disk on the laptop. The backfill rewrites ~1.3M rows once, so the
   `photos` table may grow by ~1-1.5 GB of dead tuples until autovacuum reuses the space
   (do not run `VACUUM FULL`: it locks the table). Keep at least ~5 GB free (indexes + the backup of step 3).
2. Run `update.ps1` as usual: `alembic upgrade head` is its step 4 (index builds take a while; the old version keeps serving).
3. Pause the scheduler for the backfill: concurrent sync upserts can deadlock with the backfill
   or rewrite overlapping sols once. Simplest: `docker compose -p mars stop app` (the API is down
   for the whole run: ~33 min on the laptop, measured at the 18B-2 release). Alternative: skip the pause, run the backfill right after the update
   and accept a possible retry. Note `docker compose run` containers do not start the scheduler, so
   only the running `app` matters; `SYNC_ENABLED` is fixed to `"true"` in `compose.yml`.
4. Run the backfill in a one-off container (rerun is idempotent: the second run reports 0 updates):
   ```
   docker compose -p mars run --rm app python -m app.cli backfill-meta
   ```
   Optional `--rover`, `--from-sol`, `--to-sol`, `--batch-sols N` to run it in pieces. If it fails
   on a deadlock, just run it again.
5. Refresh statistics (`max_parallel_maintenance_workers=0` because of the small Docker `/dev/shm`):
   ```
   docker compose -p mars exec db psql -U <db-user> -d <db-name> -c "SET max_parallel_maintenance_workers=0" -c "VACUUM ANALYZE photos"
   ```
   (Separate `-c` options share one session, so the `SET` applies, and `VACUUM` is not wrapped in a transaction.)
6. Re-enable the scheduler: `docker compose -p mars up -d app` and check `GET /health/sync`.

#### Release 18B-3: migration 0014 (near-search index)

Migration 0014 builds `ix_photos_near_cover` (`CREATE INDEX CONCURRENTLY`, ~8 s and ~79 MB on dev,
1.7M rows), then drops `ix_photos_rover_id_site_drive` (`DROP INDEX CONCURRENTLY`). It is
rerun-safe: an INVALID leftover is dropped and rebuilt, a VALID index is never rebuilt, and the old
index is dropped only after the new one is valid. The API keeps serving during the build (no pause
needed); only sync and the scheduler must be stopped, because `CREATE INDEX CONCURRENTLY` waits for
older transactions and with `lock_timeout = 5s` it can fail late (then just rerun). Expect ~50 MB
more disk for the index; the old one is freed at the end.

`update.ps1` starts the new app right after the migration, but the `VACUUM` must come between them,
so run the steps by hand (same commands as the script), from `%USERPROFILE%\mars-server`:

1. Prepare: `git pull --ff-only` in the repository, then `docker build -t mars-rover-api:local <repo>`
   (the old container keeps running on the old image) and a backup:
   `powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\deploy\laptop\backup.ps1 -ComposeDir %USERPROFILE%\mars-server`.
2. Pause the scheduler: `docker compose -p mars stop app` (the API is down from here to step 6, a few
   minutes). `docker compose run` containers do not start the scheduler.
3. Migrate: `docker compose -p mars run --rm app alembic upgrade head` (head becomes `0014`). If it
   fails on the lock timeout, run it again.
4. Visibility map for index-only scans (`PARALLEL 0` avoids the small Docker `/dev/shm`; the db has
   `shm_size: 256m`; the `-c` options share one session and `VACUUM` is not in a transaction):
   ```
   docker compose -p mars exec db psql -U <db-user> -d <db-name> -c "VACUUM (ANALYZE, PARALLEL 0) photos"
   ```
5. Check the index:
   ```
   docker compose -p mars exec db psql -U <db-user> -d <db-name> -c "SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indrelid='photos'::regclass"
   ```
   `ix_photos_near_cover` is `t`, `ix_photos_rover_id_site_drive` is absent.
6. Start the new version: `docker compose -p mars up -d app`, wait for `200` on
   `http://127.0.0.1:8000/health`, check `GET /health/sync`.
7. Smoke test (section 3) plus the near check, on `http://127.0.0.1:8000`:
   - `GET /api/v2/photos?rovers=perseverance&near=18.4447,77.4508&radius_km=1` -> `200`, `total > 0`;
   - `GET /api/v2/photos?rovers=spirit&near=18.4447,77.4508&radius_km=1` -> `422` (no site data).

Rollback: `docker compose -p mars stop app`, then
`docker compose -p mars run --rm app alembic downgrade 0013` (rebuilds the old index, also
rerun-safe, with the new image still tagged `:local`), check out the previous commit (`4768f5c`),
`docker build -t mars-rover-api:local <repo>`, `docker compose -p mars up -d app`. Run the
`pg_index` check of step 5 again (the old index valid, `ix_photos_near_cover` gone). The data is
not touched by either direction.

#### Release: overload protection (migration 0015)

Migration 0015 rebuilds `ix_photos_near_cover` with
`INCLUDE (sol, taken_at, id, sample_type, camera_id)` (+2 columns; same key and predicate), so counts
by stop and near searches with `sample_type` or a camera run as index-only scans. It builds
`ix_photos_near_cover_swap` with `CREATE INDEX CONCURRENTLY`, drops the old index, renames the swap
(~4 s and ~102 MB on dev, +23 MB over the old index; keep ~150 MB free until the old one is dropped).
Rerun-safe: the state is read from the catalog, a valid finished index is left alone, the table is never
without an index, and a leftover INVALID swap index is rebuilt. Same constraints as 18B-3: the API keeps
serving during the build, but sync and the scheduler must be stopped (`lock_timeout = 5s` can fail
late: just rerun).

New settings (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `HEAVY_QUERY_CONCURRENCY`, see the table above) have
defaults that suit the laptop: the live `.env` and `compose.yml` need no change. API connections now
carry `application_name` `mars-api`, the sync worker `mars-worker`.

Same manual sequence as 18B-3 (`update.ps1` would start the app before the `VACUUM`), from
`%USERPROFILE%\mars-server`:

1. Prepare: `git pull --ff-only`, `docker build -t mars-rover-api:local <repo>` (the old container
   keeps running), and a backup:
   `powershell -NoProfile -ExecutionPolicy Bypass -File <repo>\deploy\laptop\backup.ps1 -ComposeDir %USERPROFILE%\mars-server`.
   Note the current commit (`3f3c02f`) for rollback.
2. Pause the scheduler: `docker compose -p mars stop app` (API down until step 6).
3. Migrate: `docker compose -p mars run --rm app alembic upgrade head` (head becomes `0015`).
4. Visibility map (needed for index-only scans):
   ```
   docker compose -p mars exec db psql -U <db-user> -d <db-name> -c "VACUUM (ANALYZE, PARALLEL 0) photos"
   ```
5. Check the index (valid, 5 INCLUDE columns, no `_swap`):
   ```
   docker compose -p mars exec db psql -U <db-user> -d <db-name> -c "SELECT indexrelid::regclass, indisvalid, indnatts - indnkeyatts AS include_cols FROM pg_index WHERE indrelid='photos'::regclass"
   ```
   `ix_photos_near_cover` is `t` with `5`; `ix_photos_near_cover_swap` is absent.
6. Start the new version: `docker compose -p mars up -d app`, wait for `200` on
   `http://127.0.0.1:8000/health`, check `GET /health/sync`.
7. Post-deploy checks:
   - connection labels (expect `mars-api` up to 15 connections and `mars-worker`):
     ```
     docker compose -p mars exec db psql -U <db-user> -d <db-name> -c "SELECT application_name, count(*) FROM pg_stat_activity GROUP BY 1"
     ```
   - smoke test (section "Smoke test") and the 18B-3 near check;
   - `GET http://127.0.0.1:8000/api/v2/rovers/curiosity/photos?date_min=2012-08-06&sample_type=full` -> `200`;
   - one heavy request per address: send three such requests in parallel from one machine; at least
     one must answer `503` with body `{"error": {"code": "service_busy", ...}}` and `Retry-After: 5`. Run it
     from outside the laptop to also pass Funnel, or from the laptop on `127.0.0.1`; mind the per-IP
     limit of 60/min and wait a minute before repeating;
   - `GET /api/v2/rovers/nosuch/photos` -> `404`.

Rollback: `docker compose -p mars stop app`, then
`docker compose -p mars run --rm app alembic downgrade 0014` (rebuilds the old 3-column INCLUDE
index, rerun-safe, with the new image still tagged `:local`), check out `3f3c02f`,
`docker build -t mars-rover-api:local <repo>`, `docker compose -p mars up -d app`. Rerun the
`pg_index` check of step 5 (`include_cols` is `3`, no `_swap`). Data is not touched in either direction.

### MER image cache

`GET /api/v2/photos/{id}/image?size=full` for Spirit and Opportunity converts the PDS3 `.img`
from NASA PDS to JPEG on the fly and keeps the result on disk.

- Where: named volume `mer_images` (`mars_mer_images`), mounted in the `app`
  container at `/app/data/mer-images`. It survives container recreation and `update.ps1`
  (which never touches volumes). The image creates the directory owned by `app` (uid 10001);
  a named volume, unlike a Windows bind mount, keeps that owner.
- Limit: `MER_IMAGE_CACHE_MAX_MB` (default 5120 = 5 GB), see `.env.example`.
- Size: `docker compose exec app du -sh /app/data/mer-images`, or
  `docker system df -v` (look for the volume `mars_mer_images`).
- Clear: the cache is only a copy, deleting it loses nothing (images are converted again on
  request, so the first request of each image is slow):
  `docker compose exec app sh -c "rm -rf /app/data/mer-images/*"`, or stop the app and remove the
  volume: `docker compose down` then `docker volume rm mars_mer_images`
  (never `docker compose down -v`: it would also delete `mars_pgdata`).
- The cache is not part of the backups.

Move to the laptop for this stage:

1. `git pull` in the repository.
2. Do not copy `compose.yml` over the live one. Carry over only two blocks from
   `deploy\laptop\compose.yml`: in `app`, `volumes: - mer_images:/app/data/mer-images`, and in the
   top-level `volumes:`, `mer_images: {name: mars_mer_images}`. The live file keeps `name: mars`
   and `pgdata: {name: mars_pgdata}`: with another project name or volume name Compose creates an
   empty database volume. Optional `MER_*` lines go to `.env` (see `.env.example`).
3. Check before `update.ps1`, in `mars-server`:
   `docker compose ls` (project `mars`), `docker volume ls` (`mars_pgdata` exists, no new
   `*_pgdata`), `docker compose config` (volumes `mars_pgdata` and `mars_mer_images`, no errors).
4. `update.ps1 -SkipPull` (build, backup, migrate, restart).

### 6. Backups and restore

Scheduled task `MarsServerBackup`, daily 03:30. To (re)create it:

```
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$env:USERPROFILE\mars-server\backup.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 03:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName MarsServerBackup -Action $action -Trigger $trigger -Settings $settings
```

`backup.ps1` parameters: `-ComposeDir` (default: its own directory), `-ProjectName`
(`mars`), `-Service` (`db`), `-BackupDir` (`<ComposeDir>\backups`), `-ContainerBackupDir`
(`/backups`), `-Keep` (7). `compose.yml` bind-mounts `.\backups` into the db container as
`/backups`, and `pg_dump` writes `mars-<yyyyMMdd-HHmmss>.dump` straight there: no temporary copy
inside Docker Desktop's disk image (which would need twice the space and never shrinks). Free
space needed: one dump on the Windows disk on top of the kept copies. A `-BackupDir` that is not
the mounted directory fails with a clear message. On any error the script exits `1` and logs
`BACKUP FAILED: ...`. Check:
`Get-Content backups\backup.log -Tail 5` and the task's "Last Run Result" (0x0). The backups
live on the same disk as the database: copy one to another disk or cloud storage now and then.

**Restore** (also used for the first transfer). It replaces the whole `mars` database:

```
docker compose stop app
docker compose exec -T db dropdb -U mars --if-exists mars
docker compose exec -T db createdb -U mars -O mars mars
docker compose exec -T db pg_restore -U mars -d mars --no-owner --role=mars --exit-on-error /backups/mars-<stamp>.dump
docker compose run --rm app alembic upgrade head
docker compose up -d app
```

The dump is read from the `/backups` mount, so it must be in `mars-server\backups\`. For a big
dump add `--jobs 4` to `pg_restore`. Check:
`Invoke-RestMethod http://127.0.0.1:8000/health` and
`Invoke-RestMethod http://127.0.0.1:8000/api/v2/rovers | % items | ft id,total_photos`.

Practice the restore now and then into a scratch project so it is known to work:
copy `compose.yml` to another directory, change `name:` and the host port, and run the steps
there (then `docker compose down -v`). Set `SYNC_ENABLED` to `"false"` in that copy, otherwise
it starts syncing from the upstream feeds.

### 7. Logs

```
docker compose ps                       # state and health of db and app
docker compose logs -f --tail 200 app   # application and access log
docker compose logs --since 24h app | Select-String "Sync finished|ERROR|WARNING"
docker compose logs --tail 100 db
```

Logs rotate at 5 x 10 MB per container (they survive restarts, not `docker compose down`).
Docker Desktop shows the same in Containers -> `mars`. Sync runs are also visible via
admin: `GET /api/v2/admin/sync/runs` with the `X-Admin-Token` header.

### 8. Database tuning (shm_size, temp_file_limit)

The `db` service in `deploy/laptop/compose.yml` sets two things:

- `shm_size: 256m`: Docker gives containers 64 MB of `/dev/shm` by default. Heavy parallel
  queries use it for dynamic shared memory and fail with `could not resize shared memory
  segment ... No space left on device`. 256 MB removes that failure.
- `-c temp_file_limit=2GB`: caps temporary files (sorts, hashes spilling to disk) per
  session, so one runaway query errors out instead of filling the disk.

Apply: copy the updated `compose.yml` to `%USERPROFILE%\mars-server`, then
`docker compose up -d db`. This recreates the db container (a few seconds of downtime, the
app reconnects); the `pgdata` volume is untouched. Check:
`docker compose exec db psql -U mars -d mars -c "show temp_file_limit"` and
`docker compose exec db df -h /dev/shm`.

The root `docker-compose.yml` (local development) is not changed here; it needs the same
`shm_size` if the problem shows up locally (owner: backend-dev).

## Monitoring

Two independent channels, both to Telegram:

1. **External monitor** (UptimeRobot): notices when the laptop, Wi-Fi, Docker, Tailscale or the
   app is down, which the app itself cannot report.
2. **Application alerts** (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`): failed or partial sync
   runs (at once) and stale data (checked every `ALERT_CHECK_INTERVAL_MINUTES`).

   Repeats: a scheduled sync that keeps failing alerts after every run, that is once per
   `SYNC_INTERVAL_MINUTES` (6 h by default) per rover, until a run succeeds. Stale data alerts
   only when a rover's state changes (and once more after an app restart). Syncs started from
   the CLI (`python -m app.cli sync` / `backfill`) send no alerts: watch their output.

### External monitor: UptimeRobot (free plan)

1. Sign up at https://uptimerobot.com.
2. Alert contact: My Settings / Integrations -> Telegram -> follow the link it shows, which opens
   the UptimeRobot bot in Telegram; press Start (or add the bot to a group). Send a test
   notification. If Telegram is not offered on the free plan at that moment, use e-mail as the
   contact; the application alerts still go to Telegram.
3. Monitors (type HTTP(s), alert contact = the Telegram contact):

   | Name | URL | HTTP method | Interval | Up when |
   |---|---|---|---|---|
   | mars-api health | `https://mars-api.sungrazing-menkent.ts.net/health` | GET or HEAD | 5 min | `200` (`503` = database down) |
   | mars-api data freshness | `https://mars-api.sungrazing-menkent.ts.net/health/sync` | GET or HEAD | 60 min | `200` (`503` = sync or photos stale) |

   Both endpoints answer `HEAD` with the same status as `GET`, so whichever method the monitor
   uses (UptimeRobot's default or the one set in the monitor's advanced settings) works.

   Neither path is rate limited; `/health/sync` is `no-store`, so no cache hides a change.
4. Test: `docker compose stop app`, wait for the "down" message (up to 5 minutes plus
   confirmation), `docker compose up -d app`, wait for "up".

### Application alerts in Telegram

1. In Telegram open @BotFather -> `/newbot` -> name and username -> it replies with the bot
   token (`123456789:AA...`). The token is a secret: whoever has it controls the bot.
2. Send any message to the new bot (or add it to a group and write there): a bot cannot write
   first.
3. Get the chat id (PowerShell, so the token does not end up in browser history):
   ```
   $token = Read-Host 'Bot token'
   (Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates").result.message.chat | ft id,type,username,title
   ```
   A private chat id is a positive number, a group id is negative. Empty result: send the bot
   another message and repeat.
4. Put both into `mars-server\.env` (uncomment the lines):
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<id>
   ```
   then `docker compose up -d app` (recreates the container with the new environment).
   Changing `.env` needs `up -d`, a plain `restart` keeps the old environment.

Messages start with `[mars-rover-api]`. The bot token never appears in the logs (redacted).

### Alert test (deliberate breakage)

Break the Perseverance feed so a sync fails, check the message, repair:

1. In `.env` add `M2020_FEED_URL=https://feed.invalid/rss/api/` (`.invalid` never resolves).
2. `docker compose up -d app`.
3. Start a sync instead of waiting for the scheduler (on the laptop):
   ```
   $h = @{ 'X-Admin-Token' = '<ADMIN_TOKEN>' }
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v2/admin/sync -Headers $h -ContentType 'application/json' -Body '{"rover": "perseverance"}'
   ```
   `202` with a `run_id`. After the retries (a minute or two) the run ends `failed`
   (`GET /api/v2/admin/sync/runs?rover=perseverance`), and Telegram gets a message about the
   failed Perseverance sync.
4. Repair: remove the `M2020_FEED_URL` line, `docker compose up -d app`, run the same `POST`
   again; the run must end `success`.

The stale-data path (`/health/sync` -> `503`, freshness alert) needs no breakage to check:
temporarily set `SYNC_STALE_HOURS=1` while the last success is older than an hour, or just
watch it after a missed day. Revert afterwards.

## Web interface (Sol Index)

`update.ps1` rebuilds the image together with the interface (`docker build` runs the npm build
stage), so Node is not needed on the laptop. The npm layer is cached while
`frontend/package.json` and `package-lock.json` are unchanged; a frontend-only change rebuilds
just the `npm run build` step.

Addresses: `/` is the interface, `/developers` the page for developers (API links, examples),
`/docs` the Swagger UI; `/api/*`, `/mars-photos/*`, `/health` are unchanged.

Check after a rollout: `uv run python deploy/smoke.py <url>` (covers `/` and `/developers`), then
open `/` in a browser and load a sol. If `/` answers 404 or the old developers page, the image
has no `dist/`: `docker compose exec app ls /app/frontend/dist` must show `index.html` and
`assets/`.

## Smoke test

From any machine with the repository and uv (outside the laptop is best: it also checks Funnel):

```
uv run python deploy/smoke.py https://mars-api.sungrazing-menkent.ts.net
```

17 requests (13 count against the per-IP limit of 60/min), redirects are not followed. Checks:
`/health` 200; `/` 200 HTML containing `id="app"` (the Sol Index interface, not the old
developers page) and one `/assets/*.js` from it with `Cache-Control: immutable`; `/developers`
200 HTML without `id="app"`; `/llms.txt` 200 containing the base URL (`PUBLIC_BASE_URL` is set); `/health/sync` 200 or 503 (body printed, 503 is a warning, not a
failure); v1 photos Curiosity sol 1000 FHAZ = 2 photos dated 2015-05-30; v1 rovers = 4; v1
Perseverance manifest; v2 rovers = 4; v2 photo `per_page=1` and its `/image` redirect `302`;
`/api/v1/rovers/curiosity/` -> `307` with an `https://` `Location` (proves `X-Forwarded-Proto`
is trusted); `If-None-Match` -> `304`; unknown rover -> `400 {"errors": "Invalid Rover Name"}`;
an Opportunity sol 1 photo whose `images.full` points to our `/api/v2/photos/{id}/image?size=full`.
The flag `--with-mer-image` adds a `GET` of that image (`200 image/jpeg`): it makes the server
download and convert a PDS file, so use it after deploying the MER stage, not routinely.
Exit code 0 when all pass, 1 otherwise.
