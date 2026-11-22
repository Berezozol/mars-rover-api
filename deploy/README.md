# Deploy

The application image is built from the root `Dockerfile`. Production moves to a Contabo VPS with
Caddy and its own domain (section "VPS (Contabo) + Caddy"); until the move is done and checked,
the owner's laptop behind Tailscale Funnel (section "Laptop + Tailscale Funnel") is the
production, and afterwards the fallback.

Contents:

- [Build](#build), [Run against the local dev database](#run-against-the-local-dev-database),
  [Migrations](#migrations), [Environment variables](#environment-variables)
- [VPS (Contabo) + Caddy](#vps-contabo--caddy): decisions, install, update and rollback, backups
  and restore, admin through an SSH tunnel, monitoring, checks after a rollout, migration from
  the laptop, incidents
- [Laptop + Tailscale Funnel](#laptop--tailscale-funnel) (fallback): install, first data transfer, Funnel,
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
| `FORWARDED_ALLOW_IPS` | no | uvicorn trusts `X-Forwarded-*` only from these addresses (default `127.0.0.1`); set to the reverse proxy address behind a proxy; comma-separated addresses and CIDR networks. Avoid `*` (the client then controls its own IP). Laptop: Docker gateway ranges (see below). VPS: `172.28.0.1,127.0.0.1` (fixed compose subnet, see "VPS (Contabo) + Caddy") |
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

## VPS (Contabo) + Caddy

Production target after the move off the laptop (owner's decision, 2026-11-22). The laptop
section below stays as the fallback until the VPS has served for a while; `deploy/laptop/` is
not changed. No Tailscale: SSH is open on port 22 (key only), the admin API is reached through
an SSH tunnel.

```
Internet --HTTPS--> Caddy (host network, Let's Encrypt) --HTTP--> 127.0.0.1:8000
    --> Docker port forward --> app container (uvicorn, SYNC_ENABLED) --> db container (Postgres 16)
```

Server: Contabo Cloud VPS 6 (6 vCPU, 12 GB RAM, 200 GB SSD), Ubuntu 24.04, IPv4
`<VPS_IP>`, IPv6 `<VPS_IPV6>` (the real addresses are in the owner's notes, not in the repository). Contabo panel firewall `mars-web`: TCP 22,
80, 443 (UDP 443 is not open, so HTTP/3 is off in the Caddyfile). Domain `solindex.net` (DNS at
Contabo): A/AAAA for the apex and `*`, CAA `letsencrypt.org`.

| Host | Serves |
|---|---|
| `api.solindex.net` | the API (v1, v2, `/health`, `/developers`, `/llms.txt`) |
| `solindex.net` | Sol Index (the app serves `frontend/dist` at `/`); the API paths work here too |
| `www.solindex.net` | `301` to `https://solindex.net{uri}` |

Done once by hand on 2026-11-22 and recorded in `deploy/vps/bootstrap.sh` (idempotent): user
`deploy` (key, sudo without password, group `docker`); root login, password login and the
default `ubuntu` user disabled; `ufw` 22/tcp, 80/tcp, 443/tcp, 443/udp; unattended security
updates with a reboot at 05:00 UTC; `fail2ban` for sshd; time zone UTC; 2 GB swap; Docker CE 29
with compose v5 (`json-file` 10 MB x 5, `live-restore`); directory `/srv/mars`.

Layout on the server:

| Path | What |
|---|---|
| `/srv/mars/repo/` | git clone of the project (scripts and units run from here) |
| `/srv/mars/compose.yml`, `Caddyfile`, `db-roles.sql` | live copies; `update.sh` refreshes them from `deploy/vps/` |
| `/srv/mars/.env` | from `deploy/vps/.env.example`, `chmod 600`, never committed |
| `/srv/mars/secrets/pg_owner_password` | password of the schema owner `mars` (file in a `chmod 700` directory); not in `.env`, so the app container never holds it |
| `/srv/mars/backups/` | `mars-<UTC stamp>.dump`, `backup.log` |

Files in `deploy/vps/`:

| File | What |
|---|---|
| `compose.yml` | project `mars`: `db` (Postgres 16 tuned for 12 GB, memory limit 4 GB), `app` (`mars-rover-api:<commit>`, `127.0.0.1:8000`, 3 GB, read-only root file system), `migrate` (one-off Alembic as the owner role), `caddy` (host network, minimal capabilities); fixed subnet `172.28.0.0/24`; volumes `mars_pgdata`, `mars_mer_images`, `mars_caddy_data` |
| `db-roles.sql` | idempotent creation of the restricted role `mars_app` and its grants |
| `Caddyfile` | HTTPS, one upstream for both hosts, `www` redirect, admin closed (404), URL/header/body limits, timeouts, HSTS and protective headers, no `Server`/`Via`, no compression, JSON logs without credentials |
| `.env.example` | all variables of the VPS `.env` |
| `update.sh` | pull, build, copy compose/Caddyfile, backup, migrate, restart, health check |
| `backup.sh`, `restore.sh`, `verify-backup.sh` | weekly dump (4 kept) and pre-update dump (3 kept) with `.sha256`, restore, weekly restore test |
| `pull-backup.ps1` | runs on the owner's Windows computer: downloads the newest verified dump (size and SHA-256 checked), keeps 12 |
| `host-check.sh` | disk, memory, containers, `/health`, HTTPS, backup age -> Telegram |
| `systemd/` | timers: `mars-backup` (Sunday 02:30 UTC), `mars-verify-backup` (Sunday 03:30 UTC), `mars-hostcheck` (every 5 min) |
| `bootstrap.sh` | server preparation (already applied), installs the units |
| `lib.sh` | helpers sourced by the scripts |

### Decisions

- **`FORWARDED_ALLOW_IPS=172.28.0.1`** (default in `compose.yml`). Caddy runs on the host
  network and connects to `127.0.0.1:8000`; Docker forwards that to the container. With the
  userland proxy (default) the container sees the connection coming from the gateway of its
  network, `172.28.0.1` (the subnet is fixed for this reason). That is the host itself, so no
  remote client can be mistaken for the proxy, and the list is a single address (the laptop
  trusts whole ranges). This is derived, not yet measured: the rehearsal must show it ("Checks
  after a rollout", real client IP). **If the app log shows another peer** (for example
  `127.0.0.1`, when Docker forwards by iptables instead of the userland proxy): set
  `FORWARDED_ALLOW_IPS=<that exact address>` in `.env`, run `docker compose up -d app`, repeat
  the check. Add a second address only if both really occur; never `*`, never a range.
- **`PUBLIC_BASE_URL=https://api.solindex.net`.** The app builds absolute links from it in v2
  photo `images.*` links, the RSS feed, `/developers` and `/llms.txt`; only one origin can be
  configured. These links are for API clients, so the API host is the right one. Sol Index calls
  the API with same-origin relative URLs (`VITE_API_BASE` empty), so it is unaffected.
  Redirects of the framework (`/api/v1/rovers/curiosity` -> `.../`) keep the host the client
  used, because Caddy passes `Host` through and the `https` scheme comes from
  `X-Forwarded-Proto`.
- **Real client IP.** Caddy replaces `X-Forwarded-For` with the connecting address
  (`header_up X-Forwarded-For {remote_host}`): a client-sent chain is never forwarded. The
  rate limiter is per IP and relies on this.
- **No compression in Caddy.** The app gzips itself, and the rate limit prices v1 responses by
  their compressed size; a second layer would change the price and double-encode.
- **HSTS** starts at `max-age=300`. Raise it to `31536000` after a week without HTTPS
  problems; `includeSubDomains`/`preload` only when every subdomain is on HTTPS.
- **Two database roles.** `mars` owns the schema: the db container, migrations (service
  `migrate`, run by `update.sh` and `restore.sh`), backups and restores use it; its password is
  only in `secrets/pg_owner_password`. The running app connects as `mars_app` (password
  `APP_DB_PASSWORD` in `.env`): `LOGIN` only, no superuser/createdb/createrole, `SELECT/INSERT/
  UPDATE/DELETE` on the tables and `USAGE` on sequences of schema `public`, no `CREATE`, no
  `TRUNCATE`, no access to `alembic_version`. `ALTER DEFAULT PRIVILEGES FOR ROLE mars` makes
  tables of future migrations usable at once. `db-roles.sql` does it all, idempotently: the
  postgres image runs it on the first start (empty volume), `update.sh` and `restore.sh` run
  it again after migrations and restores (a restored dump carries no grants: dumps are made
  with `--no-acl`). A check of `app/` found no runtime DDL, `TRUNCATE`, `VACUUM`/`ANALYZE` or
  `REFRESH MATERIALIZED VIEW`; advisory locks need no privilege. Verified locally against
  Postgres 16: `create table` and `truncate` are refused, a table created later by `mars`
  is writable by `mars_app`, the app answers `/health` and `/api/v2/rovers`.
  Consequence: a future release that needs owner rights at runtime (a `CREATE`, a
  `TRUNCATE`) fails with `permission denied` in the app log; the fix belongs in the code or in
  `db-roles.sql`, not in giving the app the owner password.
- **Container hardening.** `app`: read-only root file system (writable: the MER cache volume and
  `/tmp`), all capabilities dropped, `no-new-privileges`, CPU/PID/memory limits. `caddy`:
  read-only, only `NET_BIND_SERVICE`, `no-new-privileges`, writable `/data`, `/config`, `/tmp`.
  `db`: `no-new-privileges`, limits. Caddy runs as root inside its container (with few capabilities); a
  container escape is still a risk (accepted, see below).
- **Protective headers** on both public hosts (Caddy sets them only when the app did not):
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
  `Content-Security-Policy: frame-ancestors 'none'`, plus HSTS above. The Caddy access log
  deletes the `X-Admin-Token`, `Authorization` and `Cookie` request headers.
- **Admin API** (`/api/v2/admin*`) answers `404` on both public hosts and is reachable only
  through the SSH tunnel to `127.0.0.1:8000`.
- **One scheduler.** `SYNC_ENABLED=true` in `.env` (read by `compose.yml`) on the only app
  instance. With several replicas it must be `true` on one replica only; migrations are never
  run by a replica at start (separate step in `update.sh`).
- **Subnet.** `172.28.0.0/24` must not clash with other networks on the host
  (`docker network ls`, `ip route`).

### 1. Install from scratch

Server preparation (`bootstrap.sh`) is done. What remains, as `deploy` over SSH:

1. Clone the repository: `git clone <repository URL> /srv/mars/repo`. If it is private, create a
   read-only deploy key on the server (`ssh-keygen -t ed25519 -f ~/.ssh/mars-deploy-key`), add
   the public key to the repository (Settings -> Deploy keys, read-only) and use the SSH URL.
2. Secrets. `cp /srv/mars/repo/deploy/vps/.env.example /srv/mars/.env && chmod 600 /srv/mars/.env`,
   then fill in `APP_DB_PASSWORD`, `ADMIN_TOKEN` (or leave it empty), Telegram variables
   ("Monitoring"); leave `APP_TAG` empty (`update.sh` sets it). Generate each password with
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` (URL-safe: it goes into
   `DATABASE_URL`). The owner password goes into a file, not into `.env`:
   `umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" > /srv/mars/secrets/pg_owner_password`
   (`update.sh` then sets the modes the containers need: directory `700`, file `644`).
3. Check DNS from the server: `getent hosts solindex.net api.solindex.net www.solindex.net`
   (must give `<VPS_IP>`). Caddy cannot get certificates before that.
4. `sudo bash /srv/mars/repo/deploy/vps/bootstrap.sh` (idempotent; this time it also installs
   the systemd units from the clone).
5. `/srv/mars/repo/deploy/vps/update.sh` (first run: builds the image, creates the empty
   database with both roles, applies migrations, starts everything; the first backup is of the
   empty schema).
   `cd /srv/mars && docker compose ps` shows three healthy containers (`compose.yml` and `.env`
   are picked up there).
6. Load the data: "Migration from the laptop" below (or let the sync fill the database from the
   feeds; Spirit and Opportunity come only from the one-time PDS import, which is in the dump).
7. Enable the timers:
   `sudo systemctl enable --now mars-backup.timer mars-verify-backup.timer mars-hostcheck.timer`
   and check `systemctl list-timers 'mars-*'`.
8. Run the checks in "Checks after a rollout".

**Pin images** (once, after the first successful start; third-party tags can be re-pushed).
On the server, read the digests of what runs now:
`docker buildx imagetools inspect caddy:2.10-alpine | head -n 3` (the line `Digest: sha256:...`)
and the same for `postgres:16.16-alpine`. Put them into `/srv/mars/.env`:
`CADDY_IMAGE=caddy:2.10-alpine@sha256:<digest>` and
`POSTGRES_IMAGE=postgres:16.16-alpine@sha256:<digest>`, then `update.sh --skip-pull`. Update the
digests deliberately (read the release notes), not by pulling a moving tag. Until then both
images are pinned by tag only. Check the Caddyfile once on the server before the first start:
`docker run --rm -v /srv/mars/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile`.

### 2. Update and rollback

```
/srv/mars/repo/deploy/vps/update.sh            # git pull --ff-only (branch master), build, backup, migrate, restart
/srv/mars/repo/deploy/vps/update.sh --skip-pull
```

Each step must succeed before the next; on failure the script names the step, sends a Telegram
message and rolls nothing back. The old app keeps serving until step 6 (migrations run as the
owner role in a one-off container with the new image, then `db-roles.sql` is applied). Old images are kept: the newest 3.

Rollback of the app (only when the new migrations are backward compatible, otherwise restore
the pre-update backup, "Backups and restore"):

```
cd /srv/mars
docker image ls mars-rover-api                        # pick the previous tag
sed -i 's/^APP_TAG=.*/APP_TAG=<previous-tag>/' .env
docker compose up -d app
```

Changing the Caddyfile alone: edit it in the repository, run `update.sh --skip-pull` (copies it
and reloads Caddy after `caddy validate`), or by hand
`docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

### 3. Backups and restore

`mars-backup.timer` runs `backup.sh` weekly, Sunday 02:30 UTC (`Persistent=true`: a run missed
while the server was off starts after boot), and `update.sh` runs it before every migration as
`BACKUP_KIND=pre-update`: `pg_dump -Fc` streams from the db container straight to
`/srv/mars/backups/mars-<stamp>.dump.partial`, `pg_restore --list` must read it, then it is
renamed. Rotation is separate per kind: the 4 newest weekly `mars-<stamp>.dump` (about a month)
and the 3 newest `preupdate-<stamp>.dump`, so frequent releases never push out the weekly
copies; `pre-restore-*` dumps are not rotated (delete by hand). Weekly means up to a week of
data between copies: the sync can refill recent photos from the feeds, but take an extra
`backup.sh` before risky work; log `backups/backup.log`; on failure a Telegram message
and a failed unit (`systemctl status mars-backup.service`). The backups are on the same disk as
the database: they protect against mistakes and bad migrations, not against losing the server.

**Off-server copy: the owner's computer pulls it** (decision: the computer fetches, the server
never connects out). `backup.sh` writes `mars-<stamp>.dump.sha256` next to each dump, only after
`pg_restore --list` read the archive. `deploy/vps/pull-backup.ps1` (Windows PowerShell 5.1, the
system OpenSSH client with your key in the Windows ssh-agent) downloads the newest dump that is
not yet local to `%USERPROFILE%\mars-backups`, compares size and SHA-256, keeps the newest 12 (about three months),
logs to `pull-backup.log` there and touches `.last-pulled` on the server; `host-check.sh` warns
in Telegram when that mark is older than 9 days (only once the first pull has happened).
Set it up once, in PowerShell on the Windows computer:

```
# 1. The ssh-agent holds the key, and the host key is known (accept the fingerprint after
#    comparing it with the one in the provider's console):
Get-Service ssh-agent | Set-Service -StartupType Automatic; Start-Service ssh-agent
ssh-add $env:USERPROFILE\.ssh\<your-private-key>
ssh deploy@<VPS_IP> exit
# 2. Copy the script (from the repository checkout) and try it by hand:
New-Item -ItemType Directory -Force $env:USERPROFILE\mars-backups | Out-Null
Copy-Item <repo>\deploy\vps\pull-backup.ps1 $env:USERPROFILE\mars-backups\
& $env:USERPROFILE\mars-backups\pull-backup.ps1 -HostName <VPS_IP>
# 3. Scheduled task: weekly on Sunday 09:00 local time and at every logon; a missed run starts
#    when the computer wakes up or the user signs in (StartWhenAvailable), only with a
#    network. -Force replaces an existing MarsPullBackup task:
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$env:USERPROFILE\mars-backups\pull-backup.ps1`" -HostName <VPS_IP>"
$t = @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9:00), (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME))
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName MarsPullBackup -Action $a -Trigger $t -Settings $set -Force
```

Check: `Get-Content $env:USERPROFILE\mars-backups\pull-backup.log -Tail 5` and the task's
"Last Run Result" (0x0). The task runs as you, only while you are signed in (no stored
password); the key never leaves the computer. A dump is a full copy of the data: keep that
folder on an encrypted disk (BitLocker). Optionally `BACKUP_REMOTE` (rclone upload to a bucket)
adds a second off-server copy: set it in `.env` after `sudo apt-get install rclone` and
`rclone config` as `deploy`; without it that step is skipped and the log says so.

**Restore** (replaces the whole `mars` database; needs an explicit decision):

```
/srv/mars/repo/deploy/vps/restore.sh mars-<stamp>.dump
```

It asks you to type `restore`, checks the dump with `pg_restore --list`, stops the app, makes a
safety dump of the current state (`pre-restore-*.dump`, ignored by the rotation; delete it
later), drops and creates the database, runs `pg_restore --jobs 4`, `alembic upgrade head`
(owner role), re-applies `db-roles.sql`, starts the app and waits for `/health`. If a step
fails after the database was dropped it says so, sends a Telegram message and names the
safety dump. Options: `--yes`, `--no-safety-dump`, `--jobs N`. A dump
from another machine goes to `/srv/mars/backups/` first (the db container sees that directory
read-only).

**Restore test.** `mars-verify-backup.timer` (Sunday 03:30 UTC) runs `verify-backup.sh`: it
restores the newest dump into a scratch database `mars_verify` in the running db container,
compares row counts of `photos`, `rovers`, `cameras`, `sync_runs` with the live database, drops
the scratch database; a failure sends a Telegram message. Run it by hand any time:
`/srv/mars/repo/deploy/vps/verify-backup.sh`. It needs free disk space of about twice the
database size (checked first).

### 4. Admin API through an SSH tunnel

```
ssh -L 8000:127.0.0.1:8000 deploy@<VPS_IP>
```

While the tunnel is open, on your own machine `http://127.0.0.1:8000/api/v2/admin/...` with the
header `X-Admin-Token` reaches the app directly (port 8000 busy locally: use
`-L 18000:127.0.0.1:8000`). On the public hosts the same path answers `404`.

### 5. Monitoring on the VPS

- `mars-hostcheck.timer` runs `host-check.sh` every 5 minutes: disk >= 85%, available memory
  < 10%, containers `db`/`app`/`caddy` running and healthy, `/health` on `127.0.0.1:8000`,
  `https://api.solindex.net/health` through Caddy with certificate validation (resolved to
  localhost, `HEAD` only), newest weekly backup older than 8 days. A Telegram message when the set of
  problems changes, a reminder every 6 h, "OK again" when it clears. Uses `TELEGRAM_BOT_TOKEN`
  and `TELEGRAM_CHAT_ID` from `.env` (the same bot as the application alerts).
- The external monitor (UptimeRobot, "Monitoring" below) watches what the server cannot report
  about itself: monitors for `https://api.solindex.net/health` (5 min) and
  `https://api.solindex.net/health/sync` (60 min).
- Application alerts (failed sync, stale data): "Monitoring" below; the alert test works the
  same, with the admin request sent through the tunnel.
- Test the host alert on purpose: `DISK_WARN_PCT=1 /srv/mars/repo/deploy/vps/host-check.sh`
  (a message about the disk), or `HOSTCHECK_TEST=1 ...host-check.sh` (a fake problem; the next
  normal run sends "OK again"). A real breakage: `docker compose stop app`, wait up to 5 min
  for the message, `docker compose up -d app`.
- Logs: `docker compose logs --tail 200 app|db|caddy` (Caddy: JSON, one line per request).

### 6. Checks after a rollout

Run once after the first install (the "rehearsal") and after any Caddyfile or compose change.
From a machine outside the server, repeated for IPv4 and IPv6 (`curl -4`, `curl -6`) where the
machine has both.

| Check | Command | Expected |
|---|---|---|
| Real client IP | `curl -4 -s https://api.solindex.net/health`, then on the server `docker compose logs --tail 5 app` | the access log shows your public address (not `172.28.0.1`, not `127.0.0.1`); same for `curl -6` and for the Caddy log field `request.remote_ip`. If it shows the gateway, the peer is not trusted: see "Decisions", `FORWARDED_ALLOW_IPS` |
| Forged `X-Forwarded-For` | `curl -s -H 'X-Forwarded-For: 6.6.6.6' https://api.solindex.net/health`, then the logs | still your address, never `6.6.6.6` |
| Rate limit | `for i in $(seq 1 70); do curl -s -o /dev/null -w '%{http_code}\n' https://api.solindex.net/api/v2/rovers; done \| sort \| uniq -c` | about 60 x `200`, then `429` (with `Retry-After`). Then check from a second address (mobile network) that it is NOT limited: a limit shared by all clients means the proxy address is counted |
| HSTS and headers | `curl -sI https://api.solindex.net/health` and `curl -sI https://solindex.net/` | `strict-transport-security: max-age=300`, `x-content-type-options: nosniff`, `referrer-policy`, `content-security-policy: frame-ancestors 'none'`; no `Server`, no `Via` |
| One compression layer | `curl -sI -H 'Accept-Encoding: gzip' https://api.solindex.net/api/v1/rovers` | a single `content-encoding: gzip` (from the app) |
| Long URL | `curl -s -o /dev/null -w '%{http_code}\n' "https://api.solindex.net/x?$(head -c 5000 /dev/zero \| tr '\0' a)"` | `414` |
| Big header / body | `curl -s -o /dev/null -w '%{http_code}\n' -H "X-Big: $(head -c 30000 /dev/zero \| tr '\0' a)" https://api.solindex.net/` and a `POST` of 100 KB | `431` and `413` |
| `www` redirect | `curl -sI 'https://www.solindex.net/foo?a=1'` | `301`, `location: https://solindex.net/foo?a=1` |
| HTTP -> HTTPS | `curl -sI http://api.solindex.net/health` | `308` to `https://...` |
| Admin closed | `curl -s -o /dev/null -w '%{http_code}\n' https://api.solindex.net/api/v2/admin/sync/runs` (also `//api/v2/ADMIN/...`) | `404`; through the tunnel with the token `200` |
| Redirect scheme | `curl -sI https://api.solindex.net/api/v1/rovers/curiosity` | `307`, `Location` starts with `https://api.solindex.net/` |
| Admin token not logged | `curl -s -o /dev/null -H 'X-Admin-Token: probe-token-1234567' https://api.solindex.net/api/v2/admin/x`, then `docker compose logs --tail 3 caddy \| grep -c probe-token` | `0` |
| Database role | `docker compose exec app python -c "import os;print(os.environ['DATABASE_URL'].split(':')[1])"` and `docker compose exec db psql -U mars -d mars -c '\du mars_app'` | `//mars_app`; not superuser, no create role/db; the app container has no owner password (`env \| grep -i owner` is empty) |
| Read-only containers | `docker compose logs --tail 100 app \| grep -i 'read-only'`; `docker compose ps` | no `Read-only file system` errors, all healthy. If the app needs another path, add a tmpfs/volume in `compose.yml` |
| Ports | `ss -tlnp` on the server | 8000 on `127.0.0.1` only, nothing on 5432; Docker port publishing bypasses `ufw`, so this check matters |
| Certificate | `curl -vI https://solindex.net 2>&1 \| grep -i issuer` | Let's Encrypt, valid for `solindex.net`, `api.solindex.net`, `www.solindex.net` |
| Smoke test | `uv run python deploy/smoke.py https://api.solindex.net` | all pass (`/llms.txt` contains `https://api.solindex.net`) |

The Caddyfile was exercised locally against a stub upstream (admin `404` in three spellings,
`414` at 5000 characters and `200` at 4000, `431`, `413`, forged `X-Forwarded-For` replaced,
no `Server`/`Via`, no `content-encoding`); real addresses, certificates and the peer address
seen by uvicorn can only be checked on the server.

### 7. Migration from the laptop

The new domain means no DNS cut-over: the laptop keeps answering on its Funnel URL until the
owner turns it off, while the VPS is prepared and checked. What must be carried over is the
database (it holds the one-time Spirit/Opportunity import, locations and samples); everything
else the sync fetches again.

1. VPS: install and check (steps 1-5 above and "Checks after a rollout"), with the empty
   database. `SYNC_ENABLED=true` is fine: the VPS syncs from the feeds into its own database;
   the two servers do not share anything.
2. Laptop (PowerShell, `mars-server`): `.\backup.ps1` makes `backups\mars-<stamp>.dump`
   (`pg_restore --list` already checked). Copy it to the machine you work from if it is not that
   one.
3. Upload, from that machine (Windows OpenSSH has `scp`):
   `scp mars-<stamp>.dump deploy@<VPS_IP>:/srv/mars/backups/` (the file name keeps to
   letters, digits, `.`, `_`, `-`).
4. VPS: `/srv/mars/repo/deploy/vps/restore.sh mars-<stamp>.dump` (type `restore`; a safety dump
   of the empty database is made, delete it afterwards). It also runs `alembic upgrade head`
   if the laptop schema is older, and re-applies the grants of `mars_app` (the laptop dump has
   none).
5. Compare: `curl -s https://api.solindex.net/api/v2/rovers` against the laptop's
   `total_photos`; `/health/sync` after the first sync (it fills the gap since the dump,
   `SYNC_OVERLAP_SOLS`).
6. Switch clients: v1 clients change only the host; Sol Index and links move to `solindex.net`.
   Update the UptimeRobot monitors. Keep the laptop stack for a few weeks as a fallback, but
   stop its `app` container (`docker compose stop app`) so that two schedulers do not hit NASA;
   its data then ages, so fall back by restoring a fresh VPS dump there.
7. Later: raise HSTS, remove the Funnel (`tailscale funnel reset`), archive the laptop stack.

### Accepted risks and TODO (owner's decisions, from the security review)

- Branch `master` is not protected: whoever can push there controls what `update.sh` builds
  and runs as root-equivalent (docker group). TODO: branch protection, signed commits.
- `deploy` has `sudo` without a password and is in the `docker` group (root-equivalent): the
  SSH key of the owner is the single point of trust. Keep it in the ssh-agent, protected.
- DNS has a wildcard `*` to the server: any subdomain reaches Caddy (which answers only the
  three configured names). Take this into account for every future subdomain.
- HSTS is short (`max-age=300`) until it is raised on purpose: TODO after a quiet week,
  `includeSubDomains`/`preload` only when all subdomains are on HTTPS.
- Caddy and the containers run with the default Docker isolation (no user namespaces, no
  seccomp tuning); the images are pinned by digest only after "Pin images" is done.

### 8. Incidents (quick reference)

| Symptom | First look | Notes |
|---|---|---|
| Telegram "container X is ..." | `docker compose ps`, `docker compose logs --tail 200 X` | restart only after the cause is read: `docker compose up -d X` |
| Disk >= 85% | `df -h /`, `docker system df`, `du -sh /srv/mars/backups`, size of volume `mars_mer_images` | the MER image cache can be deleted at any time; old images: `docker image prune -a` (keeps the running one) |
| Certificate or HTTPS fails | `docker compose logs --tail 100 caddy` | DNS must still point here and TCP 80/443 must be open in the panel and `ufw` |
| Backup is stale | `systemctl status mars-backup.service`, `journalctl -u mars-backup -n 50`, `tail backups/backup.log` | run `backup.sh` by hand after fixing the cause |
| Wrong client IPs (everyone limited together) | app access log | fix `FORWARDED_ALLOW_IPS` to the peer address seen there |
| App log: `permission denied for ...` | `docker compose logs --tail 100 app` | the restricted role lacks a grant: run `update.sh --skip-pull` (re-applies `db-roles.sql`); if it persists, the new code needs owner rights: a code or `db-roles.sql` change, not the owner password |

## Laptop + Tailscale Funnel

**Fallback.** This was the production setup until the move to the VPS (section above); it stays
documented and untouched in `deploy/laptop/` as the way back. The scheduled task
`MarsServerBackup` on the laptop is disabled: after the move the laptop data is stale, so
its backups are of no use (a fallback restores a fresh VPS dump there, see "Migration from the
laptop", step 6).

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

(Laptop addresses below; on the VPS use `https://api.solindex.net/health` and `/health/sync`, and
see "Monitoring on the VPS" above for the host checks.)

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
