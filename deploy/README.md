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

Files (templates in `deploy/laptop/`, live copy outside the repository in
`C:\Users\<user>\mars-server\`):

| File | What |
|---|---|
| `compose.yml` | project `mars-server`: `db` (Postgres 16, volume, no published ports), `app` (`mars-rover-api:local`, `127.0.0.1:8000`), restart `unless-stopped`, log rotation 5 x 10 MB |
| `.env` | from `.env.example`: `POSTGRES_PASSWORD`, `ADMIN_TOKEN`, Telegram, optional tuning. Never committed |
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

When `compose.yml` itself changes in the repository, copy it to `mars-server` and run
`docker compose up -d` (it recreates only the changed services; the database volume stays).
Stage 9 changes it: the db service gets the `./backups:/backups` mount that `backup.ps1` needs,
and `FORWARDED_ALLOW_IPS` is no longer `*`. Run the client-IP checks of section 3 afterwards.

### 6. Backups and restore

Scheduled task `MarsServerBackup`, daily 03:30. To (re)create it:

```
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$env:USERPROFILE\mars-server\backup.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 03:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName MarsServerBackup -Action $action -Trigger $trigger -Settings $settings
```

`backup.ps1` parameters: `-ComposeDir` (default: its own directory), `-ProjectName`
(`mars-server`), `-Service` (`db`), `-BackupDir` (`<ComposeDir>\backups`), `-ContainerBackupDir`
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
Docker Desktop shows the same in Containers -> `mars-server`. Sync runs are also visible via
admin: `GET /api/v2/admin/sync/runs` with the `X-Admin-Token` header.

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

## Smoke test

From any machine with the repository and uv (outside the laptop is best: it also checks Funnel):

```
uv run python deploy/smoke.py https://mars-api.sungrazing-menkent.ts.net
```

13 requests (10 count against the per-IP limit of 60/min), redirects are not followed. Checks:
`/health` 200; `/` 200 HTML; `/health/sync` 200 or 503 (body printed, 503 is a warning, not a
failure); v1 photos Curiosity sol 1000 FHAZ = 2 photos dated 2015-05-30; v1 rovers = 4; v1
Perseverance manifest; v2 rovers = 4; v2 photo `per_page=1` and its `/image` redirect `302`;
`/api/v1/rovers/curiosity/` -> `307` with an `https://` `Location` (proves `X-Forwarded-Proto`
is trusted); `If-None-Match` -> `304`; unknown rover -> `400 {"errors": "Invalid Rover Name"}`.
Exit code 0 when all pass, 1 otherwise.
