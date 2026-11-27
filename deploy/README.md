# Deploy

The application image is built from the root `Dockerfile`. Production is a Contabo VPS with
Caddy and its own domain (section "VPS (Contabo) + Caddy"). The owner's laptop behind Tailscale
Funnel (section "Laptop + Tailscale Funnel") is a switched-off fallback: its Funnel is off and
its admin API is disabled, see that section before turning anything back on.

Contents:

- [Build](#build), [Run against the local dev database](#run-against-the-local-dev-database),
  [Migrations](#migrations), [Environment variables](#environment-variables)
- [VPS (Contabo) + Caddy](#vps-contabo--caddy): decisions, install, update and rollback, backups
  and restore, admin through an SSH tunnel, monitoring, checks after a rollout, migration from
  the laptop, incidents, edge limits (nftables, concurrency ceiling), Content-Security-Policy
- [Laptop + Tailscale Funnel](#laptop--tailscale-funnel) (switched-off fallback): install, first
  data transfer, Funnel, autostart, updates, backups and restore, logs
- [Monitoring](#monitoring): external uptime monitor, Telegram alerts, alert test
- [Smoke test](#smoke-test)

## Build

```
docker build -t mars-rover-api .
```

The image: `python:3.13-slim` (pinned), runtime dependencies only (`uv sync --frozen --no-dev`),
non-root user `app` (uid 10001), port 8000, `HEALTHCHECK` on `/health` (unhealthy when the
database is unreachable; uvicorn's own saturation 503 does not count, see "Edge limits"). Base
images are pinned by tag; how to pin them by digest is in the comment at the top of the
`Dockerfile`. It contains `app/`, `migrations/`, `alembic.ini` and the built web
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
| `FORWARDED_ALLOW_IPS` | no | uvicorn trusts `X-Forwarded-*` only from these addresses (default `127.0.0.1`); set to the reverse proxy address behind a proxy; comma-separated addresses and CIDR networks. Avoid `*` (the client then controls its own IP). Laptop: Docker gateway ranges (see below). VPS: `172.28.0.1`, the default in `compose.yml` (gateway of the fixed compose subnet; see "Decisions" under "VPS (Contabo) + Caddy"); change it in `.env` only to the exact peer address the rollout check shows |
| `UVICORN_LIMIT_CONCURRENCY` | no | default 384 (image and VPS `compose.yml`): server-wide safety-net ceiling of open connections plus requests in flight; above it uvicorn answers `503 Service Unavailable` (text/plain) before the application sees the request. Read by uvicorn itself, not by `Settings`. See "Edge limits" |
| `RATE_LIMIT_ENABLED` | no | default `true`: per-IP limit on `/api/v1`, `/mars-photos/api/v1`, `/api/v2` (and nothing else: the web interface, `/docs`, `/health` are free of it) |
| `RATE_LIMIT_IPV6_SITE_FACTOR` | no | default 8: IPv6 clients are counted per `/64` and, with a budget this many times larger than one client's, per `/48` (a request counts in both); wrong tokens/keys, e-mails and heavy searches are counted per `/48` |
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
and the alert state live in-process, so a second replica would double the counters and needs
`SYNC_ENABLED=true` on one replica only.

## VPS (Contabo) + Caddy

Production, after the move off the laptop (owner's decision, 2026-11-22). The laptop section
below is the switched-off fallback; `deploy/laptop/` is kept as it was. No Tailscale: SSH is
open on port 22 (key only), the admin API is reached through an SSH tunnel.

```
Internet --HTTPS--> Caddy (host network, Let's Encrypt) --HTTP--> 127.0.0.1:8000
    --> Docker port forward --> app container (uvicorn, SYNC_ENABLED) --> db container (Postgres 16)
```

Server: Contabo Cloud VPS 6 (6 vCPU, 12 GB RAM, 200 GB SSD), Ubuntu 24.04, IPv4
`<VPS_IP>`, IPv6 `<VPS_IPV6>` (the real addresses are in the owner's notes, not in the repository). Contabo panel firewall `mars-web`: TCP 22,
80, 443 (UDP 443 is not open, so HTTP/3 is off in the Caddyfile; HTTP/2 is off as well, see "Edge limits"). Domain `solindex.net` (DNS at
Contabo): explicit A/AAAA for the apex, `api` and `www` (no wildcard since 2026-11-23), CAA
`letsencrypt.org`; mail for the domain is forwarded by ImprovMX (MX and SPF records only).

| Host | Serves |
|---|---|
| `api.solindex.net` | the API (v1, v2, `/health`, `/developers`, `/llms.txt`) |
| `solindex.net` | Sol Index (its files are served by Caddy from `/srv/mars/www`, the app serves them only as a fallback); the API paths work here too |
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
| `/srv/mars/www/<tag>/`, `www/current` | web interface files of each release (copied out of the image `mars-rover-api:<tag>`), `current` is a relative link to the active one; mounted read-only into Caddy at `/srv/www` |

Files in `deploy/vps/`:

| File | What |
|---|---|
| `compose.yml` | project `mars`: `db` (Postgres 16 tuned for 12 GB, memory limit 4 GB), `app` (`mars-rover-api:<commit>`, `127.0.0.1:8000`, 3 GB, read-only root file system), `migrate` (one-off Alembic as the owner role), `caddy` (host network, minimal capabilities); fixed subnet `172.28.0.0/24`; volumes `mars_pgdata`, `mars_mer_images`, `mars_caddy_data` |
| `db-roles.sql` | idempotent creation of the restricted role `mars_app` and its grants |
| `Caddyfile` | HTTPS, one upstream for both hosts, `www` redirect, admin closed (404), `/docs`, `/redoc`, `/openapi.json` of the site host redirected to the API host, unknown Host aborted (`strict_sni_host` and catch-all sites), URL/header/body limits, timeouts, HSTS, Content-Security-Policy per kind of response, no `Server`/`Via`, the web interface files served by Caddy itself (compressed there; nothing else is compressed by Caddy), JSON logs without credentials |
| `.env.example` | all variables of the VPS `.env` |
| `update.sh` | pull, build, copy compose/Caddyfile, stage the web interface files, backup, migrate, activate the files, restart, health check |
| `publish-frontend.sh` | `publish-frontend.sh <tag>`: Caddy serves the web interface of that image (after a rollback of the app); without arguments it lists the staged releases |
| `backup.sh`, `restore.sh`, `verify-backup.sh` | weekly dump (4 kept), pre-update dump (3 kept) and pre-import dump (2 kept) with `.sha256`, restore, weekly restore test |
| `pull-backup.ps1` | runs on the owner's Windows computer: downloads the newest verified dump (size and SHA-256 checked), keeps 12 |
| `host-check.sh` | disk, memory, containers, `/health`, HTTPS, backup age -> Telegram |
| `rad-import.sh`, `weather-import.sh`, `rad-sync.sh` | import of an export file (status, backup, import, status), monthly `rad sync` (7a) |
| `systemd/` | timers: `mars-backup` (Sunday 02:30 UTC), `mars-verify-backup` (Sunday 03:30 UTC), `mars-hostcheck` (every 5 min), `mars-rad-sync` (1st of the month, 06:00 UTC; enable after the RAD import, 7a); `mars-limits.service` (loads the nftables limits at boot, enabled by `limits.sh apply`) |
| `limits.sh` | per-client connection and new-connection limits on TCP 80/443 in nftables (`mars-limits.service` loads them at boot); `apply`, `check`, `status`, `remove`; see "Edge limits" |
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
- **HSTS** is `max-age=31536000` (1 year) since 2026-11-22, raised from 300 s on the owner's
  decision before the quiet week this text first asked for; `includeSubDomains`/`preload` only
  when every subdomain is on HTTPS. To back out, serve `max-age=0` until browsers have
  revisited.
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
  a `Content-Security-Policy` that depends on the kind of response ("Content-Security-Policy"
  below), plus HSTS above. The Caddy access log deletes the `X-Admin-Token`, `Authorization`
  and `Cookie` request headers.
- **Unknown Host.** Caddy used to answer a made-up `Host` with an empty `200` and `Server:
  Caddy`. Now `strict_sni_host` answers `421` when the `Host` header differs from the TLS server
  name (a real name in the SNI with a made-up `Host`). A request by IP address or with a made-up
  SNI normally fails in the TLS handshake (no certificate for that name), so the catch-all site
  `:443 { abort }` is only a backstop and almost unreachable. Plain HTTP with a made-up `Host`
  is what the `:80 { abort }` site closes. The automatic `http -> https` redirect and ACME of
  the three real names should be matched first; that could not be tried without Caddy, so
  check the redirect right after the reload ("Rolling out"); if it broke, remove the `:80`
  block. `Server: Caddy` still shows on the automatic port-80 redirects and on the `421`:
  Caddy adds it before any route can remove it.
- **`/docs`, `/redoc`, `/openapi.json` live on the API host only.** FastAPI's pages load scripts
  from `cdn.jsdelivr.net`, which must not run on the origin of the web interface. Sol Index
  links to `/docs` ("API docs"), so the site host answers `302` to `https://api.solindex.net`
  instead of `404`; `/openapi.json` is redirected too (plain JSON, but one canonical copy).
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
images are pinned by tag only. The base images of the app `Dockerfile` (python, uv, node) are
pinned by tag too; to add digests, run `docker buildx imagetools inspect <image:tag>` for each of
them (needs network), append `@sha256:<digest>` to the three `ARG ..._IMAGE=` defaults and let
Dependabot propose later updates. Check the Caddyfile once on the server before the first start:
`docker run --rm -v /srv/mars/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile`.

### 2. Update and rollback

```
/srv/mars/repo/deploy/vps/update.sh            # git pull --ff-only (branch master), build, backup, migrate, restart
/srv/mars/repo/deploy/vps/update.sh --skip-pull
```

Each step must succeed before the next; on failure the script names the step, sends a Telegram
message and rolls nothing back. The old app keeps serving until step 6 (migrations run as the
owner role in a one-off container with the new image, then `db-roles.sql` is applied). Old images are kept: the newest 3.

**The web interface and releases.** Caddy serves the Sol Index files itself, from
`/srv/mars/www/current`, a relative link to `/srv/mars/www/<tag>/`: the copy of
`/app/frontend/dist` taken out of the image `mars-rover-api:<tag>`. `update.sh` copies the new
release into `www/<commit>` in step 3 (nothing changes for visitors, a failure stops the update
before the database is touched), and in step 6 renames the link to it just before the app is
recreated. The rename is atomic and Caddy needs no reload; `index.html` and `/assets` always
come from one directory, so a release cannot leave Caddy serving assets of another version than
its `index.html`. Step 7 compares the `index.html` that Caddy serves with the one the app serves
and warns (log and Telegram) if they differ. Directories whose image has been deleted (the
newest 3 images are kept) are removed at the end of the update; the active one never is.
If `www/current/index.html` is missing (nothing published yet, a broken link), Caddy passes
everything to the app, which serves the interface itself: slower under load, but never an error.
A browser that holds an `index.html` of the previous release and asks for one of its old hashed
files after the switch gets the app's 404 for that file until it reloads the page (the same as
before this change: only the running release's files exist).

Rollback of the app (only when the new migrations are backward compatible, otherwise restore
the pre-update backup, "Backups and restore"). **Two steps, both needed**: the app image and the
web interface files of the same tag:

```
cd /srv/mars
docker image ls mars-rover-api                        # pick the previous tag
sed -i 's/^APP_TAG=.*/APP_TAG=<previous-tag>/' .env
docker compose up -d app
/srv/mars/repo/deploy/vps/publish-frontend.sh <previous-tag>   # Caddy serves that release's files
```

Forgetting the last line is not an outage (Caddy keeps serving a complete newer release of the
interface, which talks to the older API), but the two then differ; `publish-frontend.sh` without
arguments shows the active release, `curl -s http://127.0.0.1:8000/ | cmp - <(curl -sk --resolve solindex.net:443:127.0.0.1 https://solindex.net/)`
must print nothing.

Changing the Caddyfile alone: edit it in the repository, run `update.sh --skip-pull` (builds
the image again, which is fast from the cache; copies the Caddyfile and reloads Caddy after
`caddy validate`), or by hand
`docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

### 3. Backups and restore

`mars-backup.timer` runs `backup.sh` weekly, Sunday 02:30 UTC (`Persistent=true`: a run missed
while the server was off starts after boot), and `update.sh` runs it before every migration as
`BACKUP_KIND=pre-update`: `pg_dump -Fc` streams from the db container straight to
`/srv/mars/backups/mars-<stamp>.dump.partial`, `pg_restore --list` must read it, then it is
renamed. Rotation is separate per kind: the 4 newest weekly `mars-<stamp>.dump` (about a month)
and the 3 newest `preupdate-<stamp>.dump`, so frequent releases never push out the weekly
copies (data imports use `BACKUP_KIND=pre-import`, 2 newest `preimport-<stamp>.dump`); `pre-restore-*` dumps are not rotated (delete by hand). Weekly means up to a week of
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
| HSTS and headers | `curl -sI https://api.solindex.net/health` and `curl -sI https://solindex.net/` | `strict-transport-security: max-age=31536000`, `x-content-type-options: nosniff`, `referrer-policy`; `/health` has `content-security-policy: default-src 'none'; frame-ancestors 'none'`; `/` has `content-security-policy: frame-ancestors 'none'` and a `content-security-policy-report-only` with `default-src 'self'`; no `Server`, no `Via` |
| Unknown Host | `curl -sk --resolve evil.example:443:<VPS_IP> https://evil.example/ -o /dev/null -w '%{http_code}\n'`; `curl -sk https://<VPS_IP>/ -o /dev/null -w '%{http_code}\n'` (fails in the TLS handshake); `curl -s -H 'Host: evil.example' http://<VPS_IP>/ -o /dev/null -w '%{http_code}\n'`; `curl -sk --resolve solindex.net:443:<VPS_IP> -H 'Host: evil.example' https://solindex.net/ -o /dev/null -w '%{http_code}\n'` | `000` (connection closed, curl exit 52 or a handshake error), `000` (handshake error, curl exit 35), `000`, `421`; never `200` |
| Real hosts still work | `curl -sI http://api.solindex.net/health` and `curl -sI http://solindex.net/`, then `curl -sI https://api.solindex.net/health` | `308` to `https://...`, then `200`; no new certificate is requested (`docker compose logs caddy \| grep -i obtain` shows nothing new) |
| Docs only on the API host | `curl -sI https://solindex.net/docs`, `.../redoc`, `.../openapi.json`; then `curl -sI https://api.solindex.net/docs` | `302` with `location: https://api.solindex.net/...`; `200` with a `content-security-policy` that names `cdn.jsdelivr.net`. Open `https://api.solindex.net/docs` and `/redoc` in a browser: both render and "Try it out" works (a blank page means the policy blocks something: the console names it; `Caddyfile`, blocks `@csp_docs`/`@csp_redoc`) |
| One compression layer | `curl -sI -H 'Accept-Encoding: gzip' https://api.solindex.net/api/v1/rovers` | a single `content-encoding: gzip` (from the app) |
| Web interface from Caddy | `curl -sI -H 'Accept-Encoding: gzip' https://solindex.net/assets/<file>.js` (name from `docker compose exec caddy ls /srv/www/current/assets`), `curl -sI https://solindex.net/sol/100`, `curl -sI https://solindex.net/favicon.svg`; `docker compose logs --tail 5 app` after them | assets: `cache-control: public, max-age=31536000, immutable`, `content-encoding: gzip` (or `zstd`), `content-type: text/javascript; charset=utf-8`; `/sol/100` (any other path): `200`, `content-type: text/html; charset=utf-8`, `cache-control: no-cache`, an `etag` (a second request with `If-None-Match` gets `304`), plus the CSP headers of "Content-Security-Policy"; `favicon.svg`: `max-age=3600`. None of these requests appears in the app log |
| Web interface: app routes untouched | `curl -s -o /dev/null -w '%{http_code}\n' https://solindex.net/api/v2/nope`, `.../assets/nope.js`, `.../assets/`, `https://api.solindex.net/developers`, `.../llms.txt`, `.../health` | the app's answers (JSON `404` for the first three, `200` for the rest: they do appear in the app log) |
| Active release | `readlink /srv/mars/www/current`; `curl -s http://127.0.0.1:8000/ \| cmp - <(curl -sk --resolve solindex.net:443:127.0.0.1 https://solindex.net/)` | the tag of `APP_TAG` in `.env`; `cmp` prints nothing (the app and Caddy serve the same `index.html`) |
| HTTP/1.1 only | `curl -sI --http1.1 https://api.solindex.net/health \| head -1`; `curl -sIv --http2 https://api.solindex.net/health 2>&1 \| grep -i -E 'ALPN|HTTP/'` (a curl built with HTTP/2) | `HTTP/1.1 200`; ALPN `http/1.1` (the client offers h2, the server picks http/1.1), response `HTTP/1.1`. `openssl s_client -alpn h2 -connect api.solindex.net:443 </dev/null 2>&1 \| grep -i -E 'ALPN\|alert'` shows no accepted protocol / a `no application protocol` alert |
| No h2c on port 80 | `printf 'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n' \| nc -w 3 api.solindex.net 80 \| head -c 20 \| xxd` | no bytes come back (HTTP/2 `SETTINGS` bytes `00 00 .. 04` mean `servers :80 { protocols h1 }` is missing) |
| Client address and TLS state intact | `curl -s https://api.solindex.net/health`, then `docker compose logs --tail 2 caddy` and `docker compose logs --tail 2 app` | the Caddy access entry has `"tls":{"version":772,...,"server_name":"api.solindex.net"}` (not `version:0`, not an empty `server_name`) and your address; the app log shows the same address. `version:0` means h1-only mode (`protocols h1`) crept in: revert |
| Long URL | `curl -s -o /dev/null -w '%{http_code}\n' "https://api.solindex.net/x?$(head -c 5000 /dev/zero \| tr '\0' a)"` | `414` |
| Big header / body | `curl -s -o /dev/null -w '%{http_code}\n' -H "X-Big: $(head -c 30000 /dev/zero \| tr '\0' a)" https://api.solindex.net/` and a `POST` of 100 KB | `431` and `413` |
| `www` redirect | `curl -sI 'https://www.solindex.net/foo?a=1'` | `301`, `location: https://solindex.net/foo?a=1` |
| HTTP -> HTTPS | `curl -sI http://api.solindex.net/health` | `308` to `https://...` |
| Admin closed | `curl -s -o /dev/null -w '%{http_code}\n' https://api.solindex.net/api/v2/admin/sync/runs` (also `//api/v2/ADMIN/...`) | `404`; through the tunnel with the token `200` |
| Redirect scheme | `curl -sI https://api.solindex.net/api/v1/rovers/curiosity` | `307`, `Location` starts with `https://api.solindex.net/` |
| Admin token not logged | `curl -s -o /dev/null -H 'X-Admin-Token: probe-token-1234567' https://api.solindex.net/api/v2/admin/x`, then `docker compose logs --tail 3 caddy \| grep -c probe-token` | `0` |
| API key not logged (app down) | `docker compose stop app`, `curl -s -o /dev/null 'https://api.solindex.net/api/v2/rovers/?api_key=si_probe0123456789'`, `docker compose start app` | `502`; the Caddy error log and access log show `api_key=REDACTED` |
| API key not logged (query) | `curl -s -o /dev/null 'https://api.solindex.net/api/v2/rovers/?api_key=si_probe0123456789'` (trailing slash: the app redirects, `Location` carries the query) | `Location` in the log has `api_key=REDACTED` |
| API key not logged (`www`) | `curl -sI 'https://www.solindex.net/api/v2/rovers?api_key=si_probe0123456789'` | `301`; then `docker logs caddy 2>&1 \| grep -c si_` prints `0` (run it after all three checks; also with `API_KEY=` in upper case) |
| Database role | `docker compose exec app python -c "import os;print(os.environ['DATABASE_URL'].split(':')[1])"` and `docker compose exec db psql -U mars -d mars -c '\du mars_app'` | `//mars_app`; not superuser, no create role/db; the app container has no owner password (`env \| grep -i owner` is empty) |
| Read-only containers | `docker compose logs --tail 100 app \| grep -i 'read-only'`; `docker compose ps` | no `Read-only file system` errors, all healthy. If the app needs another path, add a tmpfs/volume in `compose.yml` |
| Ports | `ss -tlnp` on the server | 8000 on `127.0.0.1` only, nothing on 5432; Docker port publishing bypasses `ufw`, so this check matters |
| Certificate | `curl -vI https://solindex.net 2>&1 \| grep -i issuer` | Let's Encrypt, valid for `solindex.net`, `api.solindex.net`, `www.solindex.net` |
| Smoke test | `uv run python deploy/smoke.py https://api.solindex.net` (and once against `https://solindex.net`) | all pass (`/llms.txt` contains the base URL; `/openapi.json` has no `admin`; the admin path answers `404`) |
| Page limiter sees real clients | `docker compose logs --tail 20 app` after a few requests to `/openapi.json` from your own address; `docker compose exec app printenv FORWARDED_ALLOW_IPS` | the app log shows real client addresses. With a wrong `FORWARDED_ALLOW_IPS` every request seems to come from the proxy and the page limiter (`RATE_LIMIT_PAGE_CONCURRENT`, 16) becomes ONE counter for the whole site: do not rely on it until this row is right |
| Smoke test on both hosts | `uv run python deploy/smoke.py https://api.solindex.net` and `uv run python deploy/smoke.py https://solindex.net` | both pass: the static handler answers on the site host and on the API host, the app routes on both |
| Concurrency ceiling | `docker compose exec app printenv UVICORN_LIMIT_CONCURRENCY` (not `env`: it prints secrets) | `384` (or the value set in `.env`); Caddy sets no cap of its own (no `max_conns_per_host` in the `Caddyfile`) |
| Edge limits | "Edge limits" below | `limits.sh status` shows the table, counters at 0 |

The Caddyfile was exercised locally against a stub upstream (admin `404` in four spellings,
`414` at 5000 characters and `200` at 4000, `431`, `413`, forged `X-Forwarded-For` replaced,
no `Server`/`Via`, no `content-encoding` on the app's answers; the web interface files from a
mounted build, a slow-read attack on them); real addresses, certificates and the peer address
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

### 7a. Loading data by hand on the VPS (RAD export, Curiosity thumbnails)

Both jobs change the database, so take a fresh backup first (`update.sh` makes one; otherwise
run `backup.sh` and check the `.sha256`). Neither needs a code deploy or a migration, but the
running image must already contain the command (`rad`, `thumbnails` in `python -m app.cli`).

**Windows: use the Windows OpenSSH client.** The key lives in the Windows ssh-agent, so call
`C:\Windows\System32\OpenSSH\ssh.exe` and `scp.exe`. The `ssh` of Git for Windows does not see
the agent and fails with `Permission denied (publickey)`. The host key must be accepted once by
hand (compare the fingerprint with the provider's console). Do not put the server address into
files; pass it on the command line.

**RAD export.** On the development machine: `uv run python -m app.cli rad export` writes
`data/export/rad-<date>.jsonl.gz`. Then:

```
scp data\export\rad-<date>.jsonl.gz deploy@<host>:/srv/mars/     # not into backups/ (rotation)
ssh deploy@<host> sha256sum /srv/mars/rad-<date>.jsonl.gz         # compare with Get-FileHash locally
ssh deploy@<host>
/srv/mars/repo/deploy/vps/rad-import.sh /srv/mars/rad-<date>.jsonl.gz
rm /srv/mars/rad-<date>.jsonl.gz
```

`rad-import.sh [--no-backup] [--force] PATH` checks that the file exists and is world-readable
(the container runs as uid 10001: `chmod 644` it otherwise), starts the db (`up -d --wait`),
prints `rad status`, takes a backup (`BACKUP_KIND=pre-import`: its own rotation, the 2 newest
`preimport-<stamp>.dump`, so pre-update rollback points are not evicted; `--no-backup` skips it),
runs `rad import` in a one-off container with the file mounted read-only (the root file system
of the `app` container is read-only), then prints `rad status` again. Repeating it is safe: an
identical file changes nothing. Exit codes: 0 done (also `--help`); 1 the app refused the file
because it has far fewer sols than the database (output says "use --force"; add `--force` only
after checking it is the right file); 2 bad arguments, invalid file, unreadable file or the lock
is taken; 4 a step failed before the app could judge the file (db did not start, backup, docker
or compose; nothing was imported). Check `GET /api/v2/rovers/curiosity/radiation`
(`source_max_sol`, `items`). Done by hand on 2026-11-24: 5351 files, 4625 sols, the server was
empty, no `--force`.

**Weather export (REMS, MEDA).** The same way, with `weather export` on the development machine
and `deploy/vps/weather-import.sh /srv/mars/weather-<date>.jsonl.gz` on the server (same options
and exit codes; it prints `weather status` before and after).

**Monthly RAD update: `mars-rad-sync.timer`.** Enable it only after the RAD import. As a guard,
`rad-sync.sh` reads `rad status` first and, when the database holds no sols, sends an alert and
exits 2 without downloading (a first full load of ~230-340 GB is deliberate only with
`rad-sync.sh --initial`, which is not what the timer does). It runs `rad-sync.sh` on the
1st of each month at 06:00 UTC (+ up to 1 h random delay, after the 05:00 reboot;
`Persistent=true`: a run missed while the server was off starts after boot): `rad sync` in a
one-off container, files are streamed through the parser and not stored on disk. The import
carries the md5 of every file and the `ETag`/`Last-Modified` of the listings, so a run only
loads new or changed sols. With nothing new: 2 conditional requests (`CHECKSUMS.TXT`,
`TIMING_ERRORS.TXT`, normally `304`, about 0 bytes; without validators about 1.2 MB for the two
listings, still no file). A new PDS release (every 4-5 months): about 130 files, about 9 GB,
about 30 minutes (2 s pause between requests). Exit code 0 is success; 1 (some files stay
unloaded) and 3 (server unavailable) send a Telegram message from the app (its `rad sync: ...`
summary line is the marker; without it the failure was in docker/compose and the script sends
the message); other codes (2 refused or lock taken, 4 status unreadable, 143 stopped by systemd:
timeout, `systemctl stop`, reboot) are sent by the script, which names the container
`mars-rad-sync`, stops it on SIGTERM so no lock stays held, and removes a stale one at the start;
the unit then shows `failed`. Rerun by hand: `sudo systemctl start mars-rad-sync.service`
(or `rad-sync.sh`); a rerun continues where it stopped. Log: `/srv/mars/backups/rad-sync.log`
(trimmed to the last 10000 lines at each start),
state: `docker compose run --rm --no-deps -T app python -m app.cli rad status`.

**Curiosity thumbnails (`photos.img_small`).** The command asks the JPL feed (about 900
requests for sols 0-5100) and runs in about 4-5 minutes per 1700 sols. Run it in chunks so one
SSH session stays short, and take the upper sol from `SELECT max(sol) FROM photos`:

```
cd /srv/mars
docker compose run --rm -T app python -m app.cli thumbnails --rover curiosity --from-sol 0    --to-sol 1700
docker compose run --rm -T app python -m app.cli thumbnails --rover curiosity --from-sol 1701 --to-sol 3400
docker compose run --rm -T app python -m app.cli thumbnails --rover curiosity --from-sol 3401 --to-sol <latest sol>
```

Each run ends with `batches=... matched=... updated=... skipped=... failed_batches=0`; a rerun
of a range is safe. Only Mastcam, MAHLI and MARDI frames have thumbnails (on 2026-11-24:
MAST_LEFT 96.7 %, MAST_RIGHT 98.2 %, MAHLI 99.5 %, MARDI 84.5 %). Check
`GET /api/v2/rovers/curiosity/photos?sol=<sol>&camera=MAST_LEFT` shows `images.small`.

### Accepted risks and TODO (owner's decisions, from the security review)

- Branch `master` is not protected: whoever can push there controls what `update.sh` builds
  and runs as root-equivalent (docker group). TODO: branch protection, signed commits.
- `deploy` has `sudo` without a password and is in the `docker` group (root-equivalent): the
  SSH key of the owner is the single point of trust. Keep it in the ssh-agent, protected.
  Recommendation (not applied, the owner decides): protect the private key with a passphrase,
  or move it to a hardware key (`ssh-keygen -t ed25519-sk`, needs a FIDO2 token and OpenSSH on
  both sides; `bootstrap.sh` would then install that public key). A password for `sudo`
  (replacing `NOPASSWD: ALL`) adds little while the account is in the `docker` group, which is
  root already, so the key is what counts: keep it on as few machines as possible.
- DNS has no wildcard (removed 2026-11-23): only the apex, `api` and `www` resolve. A future
  subdomain needs its own A/AAAA record and a Caddy site block.
- HSTS is one year (`max-age=31536000`, raised on 2026-11-22). TODO: `includeSubDomains`/`preload`
  only when all subdomains are on HTTPS.
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
| Clients get `503 Service Unavailable` as plain text; `docker compose logs app` shows "Exceeded concurrency limit" | `docker compose logs --tail 200 app`; `docker compose exec db psql -U mars -d mars -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY 1"`; `ss -tn state established '( sport = :443 )' \| awk '{print $4}' \| sort \| uniq -c \| sort -rn \| head` | uvicorn's ceiling (`UVICORN_LIMIT_CONCURRENCY`) is reached: slow readers or a flood. Find the source address in the Caddy log; `limits.sh status` shows whether the nftables counters rise. `host-check.sh` alerts (it reads `/health` on 127.0.0.1:8000); the Docker health check stays green on purpose. Raising the ceiling does not help against a flood |
| A visitor reports that the site does not load, others are fine | `sudo bash /srv/mars/repo/deploy/vps/limits.sh status`: a rising `counter` in a rule named `conn ...` or `rate ...` | nftables limits (one address or `/48` over its budget, often a shared NAT). Proof: the same visitor on mobile data works. Relief: raise the number (`CONN_MAX_V4=400 limits.sh apply`) or `limits.sh remove` while investigating |
| App log: `permission denied for ...` | `docker compose logs --tail 100 app` | the restricted role lacks a grant: run `update.sh --skip-pull` (re-applies `db-roles.sql`); if it persists, the new code needs owner rights: a code or `db-roles.sql` change, not the owner password |

### 9. Edge limits

The application limits requests per IP only on `/api/v1`, `/mars-photos/api/v1` and `/api/v2`.
Three further layers cover the rest. None of them stops a volume DDoS (see the end of this
section).

| Layer | Covers | Where |
|---|---|---|
| Caddy | slow or oversized requests: header 10 s, body 30 s, URL 4096 characters, headers 16 KB, body 64 KB; the web interface files (`/`, `/assets/*`, `favicon.svg`, every page path) are served by Caddy itself and never use an app connection | `Caddyfile` |
| nftables (`limits.sh`) | per client: open connections and new connections per second on TCP 80/443, for every path including the web interface, `/assets`, `/docs`, `/health` | the kernel, before Caddy |
| uvicorn ceiling | safety net for callers that bypass Caddy | `UVICORN_LIMIT_CONCURRENCY` |

**Concurrency: no cap in Caddy, the web interface never reaches the app.** Each request in
flight is one connection from Caddy to the app. A cap on those connections
(`max_conns_per_host`) was tried and removed: a stand test (2026-11-26, Caddy 2.10 with this
Caddyfile and a stub upstream) showed (while HTTP/2 was still on) that ONE HTTP/2 connection with 200 streams, a receive
window of 0 and response bodies above 32 KB held all 96 upstream slots until Caddy's write
timeout (2 minutes), and every other visitor timed out for those 2 minutes. Without the cap the
same client occupied 200 of the 384 uvicorn slots and two such connections reached the ceiling
(everybody gets 503, but only until they let go: nothing is queued). The cause is a path with no
per-client limit: the application limits requests per IP and in flight only on its API prefixes,
and the web interface (pages, `/assets/*`) had none. So the interface is no longer served by the
app: Caddy serves those files itself ("Update and rollback", the `@static` block of the
Caddyfile), where a slow reader costs a socket and a buffer in Caddy, not an app slot. The same
attack on the 1 MB model-viewer bundle (200 streams, window 0, 15 s) left the app untouched
(`in_flight` 0 in the stub's counter) and `/api/small`, `/` and an asset answered in about
35 ms during it.

**Keep-alive to the app is off** (`keepalive off` in the `reverse_proxy` transport). A second
stand test (2026-11-27: 3 connections x 200 streams, window 0, 195 KB answers on a non-API
path, repeated every 4 s) showed that with `keepalive 4s` the app finishes each response at once
(it fits into the kernel buffers), but Caddy, which does not read the body, keeps the upstream
connection; uvicorn counts it against `--limit-concurrency` until its own 5 s keep-alive ends.
383 connections were held and every other visitor got 503, wave after wave; the application's
per-request limiter cannot see this. With `keepalive off` Caddy sends `Connection: close`,
uvicorn closes the connection right after writing, all 1800 attack requests were served and
visitors got 200 in every wave. Cost: one loopback TCP connect per request. Nothing else in
the transport relies on keep-alive (`dial_timeout` and `response_header_timeout` are per request).

`UVICORN_LIMIT_CONCURRENCY` (default 384) is the ceiling for everything that does reach the
app, and the safety net for callers that bypass Caddy (127.0.0.1:8000, the SSH tunnel). It
counts open connections as well as requests. Above it uvicorn answers every request, `/health` included, with `503 Service
Unavailable`, plain text, `Connection: close`, before the application runs (no `Retry-After`,
no CORS headers, not counted by the rate limiter); the log line is `Exceeded concurrency
limit`. The Docker `HEALTHCHECK` is kept tolerant of exactly that answer: it is harmless and
protects against a pointless "unhealthy" if the ceiling is ever reached by a local flood;
`host-check.sh` and the external monitor still see the 503 and alert. Change it with
`UVICORN_LIMIT_CONCURRENCY=<n>` in `.env`, `docker compose up -d app`.

**What is closed, what remains.** Closed: the web interface files are served by Caddy and never
reach the app; every other non-API path (`/docs`, `/redoc`, `/openapi.json`, `/developers`,
`/llms.txt`, `/health*`, unknown paths) is limited by the application to
`RATE_LIMIT_PAGE_CONCURRENT` (default 16) concurrent requests per address (IPv6 sites get the
usual factor). That limit holds a slot while a response is still being written: the application
sends large bodies in pieces of 64 KB, so a client that does not read stalls the writer through
flow control and keeps its slot until it reads or gives up. THIS DEPENDS ON THAT CHANGE IN THE
APPLICATION (a body sent as one piece frees the slot at once, while uvicorn's connection stays
open until Caddy has read it): check that the deployed release has it before relying on the
limit. Responses that fit into the socket buffers finish at once
in the app and `keepalive off` makes the connection end with them. The API paths keep the
application's per-IP concurrency (4) and the heavy-search gate. Remains: a distributed attack
(many addresses, each under its limits) is not stopped here, see "Volume DDoS"; half-read
responses of up to a few hundred KB hold memory in Caddy and the kernel until Caddy's `write`
timeout (2 minutes), bounded by the per-address connection limits of nftables and not by the
number of streams: there are none, HTTP/2 is off (see below). Not verified on a real server: the
stand used a stub upstream, not the application with `RATE_LIMIT_PAGE_CONCURRENT`.

**HTTP/2 is off for clients** (owner's decision of 2026-11-27, after the security review). The
reviewer killed a 512 MB Caddy from one address with 10 HTTP/2 connections x 250 zero-window
streams; Caddy 2.10 has no `max_concurrent_streams`. Do NOT use `protocols h1` for this: stand
tests of Caddy 2.10.2 showed that it leaves requests without TLS state and client address
(`{remote_host}` empty, so `X-Forwarded-For` would be empty and the app's per-address limits
would count the proxy; `strict_sni_host` answers every request with 421). The Caddyfile keeps
`protocols h1 h2` and the TLS handshake offers only `http/1.1` (`tls { alpn http/1.1 }`, snippet
`h1_only`, imported by every site including the `:443` catch-all): a client offering `h2` and
`http/1.1` gets `http/1.1`, one offering only `h2` is refused in the handshake
(`no application protocol`). Cost: browsers open up to 6 connections per host, more TLS
handshakes, slower first load. Plain HTTP on port 80 only redirects and is HTTP/1.1 only
(`servers :80 { protocols h1 }`; safe there because the TLS-state problem concerns the TLS
listener): h2c with prior knowledge gets no `SETTINGS` answer any more, the `308` redirect and
the abort of an unknown Host were re-checked.

**Memory held by clients that stop reading** (stand, Caddy 2.10.2 with this Caddyfile, one
source address, 500 connections, hold 25 s, `memory.current` of the container, base 17-25 MB).
The client sends a request, reads the stated number of bytes quickly (so that the TCP buffers
autotune up), then stops reading; "rcvbuf 4K" means the client also shrinks its receive buffer,
so that nothing is absorbed on its side (the worst case for Caddy):

| Load | Client | Peak | Per connection |
|---|---|---|---|
| static 1 MB bundle | default rcvbuf, read 256 KB | 49 MB | 0.06 MB |
| static 1 MB bundle | default rcvbuf, read 64 KB | 226 MB | 0.42 MB |
| static 1 MB bundle | rcvbuf 4K, read 256 KB | 132 MB | 0.22 MB |
| proxied 190 KB | default rcvbuf, read 64 KB | 91 MB | 0.06 MB |
| proxied 190 KB | rcvbuf 4K, read 64 KB | 237 MB | 0.44 MB |
| proxied 190 KB | rcvbuf 4K, never reads (1000 / 2000 connections, 1 GB limit at 2000) | 447 / 853 MB | 0.43 MB |
| proxied 8 MB | default rcvbuf, read 256 KB (200 connections) | 285 MB | 1.3 MB |
| proxied 8 MB | rcvbuf 4K, read 512 KB | 698 MB | 1.36 MB |
| proxied 8 MB, host `tcp_rmem` max 256 KB (container sysctl on the stand) | rcvbuf 4K, read 512 KB | 272 MB | 0.50 MB |

A visitor got `200` in 10 ms (static: up to 70 ms) during every run, and Caddy was not killed in
any of them at 1 GB (a 512 MB Caddy was killed at about 1275 connections of the 190 KB kind).
Where the memory is: Go heap and TLS state about 0.2 MB per connection, the rest is kernel socket
memory (`memory.stat` `sock`). For the 8 MB answer the extra comes from the RECEIVE buffer of
Caddy's connection to the app, which autotunes up to `net.ipv4.tcp_rmem` max (6 MB by default),
not from the send buffer: capping `tcp_wmem` max at 256 KB changed nothing (703 MB), capping
`tcp_rmem` max did (272 MB). Lowering Caddy's write timeout would only shorten the hold, not the
peak. Remedies, and what was applied:

- **Applied:** `mem_limit` of Caddy 2g (budget: db 4g + app 3g + caddy 2g = 9g of 12 GB);
  the overall ceiling of the next paragraph; `CONN_MAX_V4`/`V6_64` stay at 500 (below).
- **Not applied, available:** `net.ipv4.tcp_rmem` max 256 KB host-wide in `/etc/sysctl.d/`
  (Caddy runs on the host network, so a container `sysctls:` entry is not allowed). It cuts the
  worst case to 0.5 MB per connection, but affects every TCP receive on the server: the sync
  downloads from NASA hosts get at most 256 KB of window per connection (about 14 Mbit/s at
  150 ms round trip). Worth it only if the 8 MB case turns out to be reachable in practice.

**Why 8 MB per connection is not the planning number.** A response that large exists only on
the API paths (big v1 lists, full-size images). The application allows 4 concurrent requests
per address there (page paths: `RATE_LIMIT_PAGE_CONCURRENT`, 16) and answers the rest with a
small `429` at once; with the change that sends bodies in 64 KB pieces a half-read response
keeps its slot. So one address can hold at most about 20 such connections (about 28 MB) and the
rest of its 500 connections are small answers or static files: **the bound per address is
about 500 x 0.44 MB = 220 MB (`/64`), about 440 MB per `/48`, plus 20 x 1.4 MB.** That relies on
the application limits being in force; the stand upstream had none (its 384 ceiling was raised
for these runs to measure Caddy alone), so the 8 MB rows are the bound if they were not.
Lowering `CONN_MAX_V4` was rejected: a browser uses up to 12 connections for the two hosts, 20
browsers active together behind one NAT already use 240, and the saving is small.

**Overall ceiling (`CONN_MAX_ALL`, default 1500, `limits.sh`).** Five addresses at the
per-address limit would have been 5 x 500 x 0.44 = 1.1 GB, enough to kill a 1 GB Caddy, and
nothing limited the total. An unkeyed rule now counts open connections on 80/443 of all clients
together; above N new SYNs are dropped for everybody (existing connections are not cut), so
under a distributed overload the site stops accepting new visitors instead of Caddy being
killed. N = 1500: 1500 x 0.44 MB = 660 MB, 33% of the 2g limit (the 8 MB case, 1500 x 1.4 MB =
2.1 GB, is the one that could not be fitted and needs the application limits above). Normal
load is far below it: a visitor uses up to 12 connections, so 1500 connections are over 100
simultaneous browsers, much more than this site has. The price is that a determined attacker
with three addresses at their limit makes new visitors wait (their SYN is retried and succeeds
when connections close); with `mem_limit` 1g Caddy would instead be killed and everybody
refused, so this is the milder failure. Raise N only together with `mem_limit` (N x 0.44 MB
under about 70% of it). The nft rule (`ct count over N` on a set keyed by the protocol) could not
be loaded here: `limits.sh check` on the server is its first syntax test, and the table
counter `conn all` in `limits.sh status` shows whether it ever fired.

**Per-client limits in nftables (`deploy/vps/limits.sh`).** Options compared:

- (a) nftables, chosen: no new software, works for every path, IPv4 per address and IPv6 per
  `/64` and `/48`, loaded from a file by a boot unit, removed with one command. Cost: the
  rules are kernel configuration outside Docker; the syntax is checked by `nft -c` before
  anything is loaded.
- (b) Caddy with a rate-limit module (`caddy-ratelimit`): a custom image built with `xcaddy`.
  It would need its own build pipeline, a third-party module to track for security updates
  and a rebuild for every Caddy release; per-request limits would also not stop a TLS
  handshake flood. Not worth it at this size.
- (c) Stock Caddy: only timeouts and size limits (already set); it has no per-client limit.

Numbers (environment variables of the script, `apply` again after a change): open connections
500 per IPv4 address, 500 per IPv6 `/64`, 1000 per IPv6 `/48`, 1500 for all clients together (`CONN_MAX_ALL`, see "Overall ceiling"); new connections 100 per second
per IPv4 address and per `/64` (burst 300), 400 per second per `/48` (burst 1000). A browser
holds up to 6 connections per host on HTTP/1.1 (the only protocol offered), 12 for the two
hosts, so an ordinary visitor uses under 20; carrier-grade NAT and universities put hundreds or thousands of visitors behind one IPv4
address, which is why the numbers are loose: the aim is to stop one source opening thousands of
connections, not to give each person a quota (the application limits requests per IP on the API
separately). The rate rules come first, so a SYN dropped for its rate is not counted as a
connection. Only the first packet of a new connection is judged: a client over the limit has
the extra connection delayed (its SYN is dropped and retried), established connections and
downloads in progress are never cut, nothing is banned. Loopback is exempt, so `host-check.sh`,
the Docker health check and the SSH tunnel are not limited; SSH (22) is not matched at all. The
`ct count` sets have no timeout (the kernel refuses that combination) and use `add`; the rate
sets use `timeout` and `update`. Check conntrack headroom before applying:
`sysctl net.netfilter.nf_conntrack_max net.netfilter.nf_conntrack_count`.

**Boot safety.** `mars-limits.service` is a one-shot unit ordered only `After=network.target`,
with no `Before=`/`network-pre` ordering and nothing depending on it. If loading the rules
fails at boot the unit shows `failed` and the server simply runs without these limits (the
network, ufw, Docker and SSH do not depend on it), which is NOT harmless: the per-address memory
bound and the overall ceiling of "Edge limits" are gone. `host-check.sh` (every 5 minutes)
therefore alerts in Telegram when `limits.sh apply` has been run (`/etc/nftables.d/mars-limits.nft`
exists) and `mars-limits.service` is not active, or, if `deploy` may use `sudo` without a
password, when the table `inet mars_limits` is missing although the unit is active. Test: `sudo
systemctl stop mars-limits.service`, wait for the next run (or `host-check.sh` by hand): the
message names the unit; `sudo systemctl start mars-limits.service` clears it. Only the short
window during boot, before the unit has run, is without limits.

Apply (as `deploy`, over SSH; there is no lock-out risk: only 80/443 are matched):

```
sudo apt-get install -y nftables            # normally present already
sudo bash /srv/mars/repo/deploy/vps/limits.sh check     # validates with `nft -c`, loads nothing
sudo bash /srv/mars/repo/deploy/vps/limits.sh apply     # loads, enables mars-limits.service
sudo bash /srv/mars/repo/deploy/vps/limits.sh status    # table and counters
```

Check that the rules work, from a machine other than the server (not through the SSH tunnel;
your own address may be delayed for a few seconds, that is the point):

```
# 1. Open connections: expect about 500 ok and the rest refused (timeout after 3 s).
python3 - <<'EOF'
import socket
held, ok, failed = [], 0, 0
for _ in range(560):
    try:
        held.append(socket.create_connection(("api.solindex.net", 443), timeout=3))
        ok += 1
    except OSError:
        failed += 1
print("ok", ok, "failed", failed)
EOF
# 2. New connections per second: some of 800 parallel requests fail (000) once the burst is used.
seq 800 | xargs -P 400 -I{} curl -s -o /dev/null -m 5 -w '%{http_code}\n' https://api.solindex.net/health | sort | uniq -c
```

Then `limits.sh status` on the server: the counters of `conn v4` (or `conn v6 /64`) and `rate
v4` are above 0. The sets show the tracked clients:
`sudo nft list set inet mars_limits conn_v4`. After a reboot (`sudo reboot`, or just
`systemctl restart mars-limits.service`) `status` must still show the table.

Roll back: `sudo bash /srv/mars/repo/deploy/vps/limits.sh remove` (deletes the table, disables
the unit, removes the file; takes effect at once, ufw and Docker are untouched). If `check` or
`apply` fails, nothing was changed: `nft` names the line; the script keeps the rendered file
for inspection and `limits.sh show` prints it. The rules were never run on a real kernel
before the first rollout: the first `check` on the server is the real syntax test, and the
two load tests above the real functional test.

**Volume DDoS** (traffic that fills the link, or very many sources) is outside the reach of
anything on this server; the decision is the owner's. Options and what changes:

- A CDN or proxy in front (for example Cloudflare, free plan): DNS of the domain moves to it,
  the origin address is hidden, volume is absorbed there. TLS ends at the CDN (it sees all
  traffic, the origin certificate must be valid for "full strict" mode).
- Then the client address arrives in a header (`CF-Connecting-IP` / `X-Forwarded-For`):
  Caddy must trust it only from the CDN's published ranges (`trusted_proxies`, and
  `header_up X-Forwarded-For {client_ip}` instead of `{remote_host}`), otherwise the rate
  limiter counts the CDN's addresses and limits everyone together, or a forged header picks
  its own key. This is the same trust decision as `FORWARDED_ALLOW_IPS`, one hop earlier.
- The origin must accept 80/443 only from the CDN ranges (ufw), otherwise the CDN is bypassed;
  the nftables limits then see CDN addresses only and must be replaced by CDN rules.
- Alternative without a CDN: ask the hosting provider about its network-level DDoS
  protection and keep the layers above; accept that a large attack takes the site down
  until it ends.

### 10. Content-Security-Policy

Set in the `Caddyfile` (block `solindex.net, api.solindex.net`), by kind of response; the
application sets none. The `header` rules are site-wide, so they apply equally to the web
interface files that Caddy serves itself (`index.html`, assets, `favicon.svg`; checked on the
stand: an asset and a page both carry the enforced `frame-ancestors` and the Report-Only
policy):

| Responses | Policy | Mode |
|---|---|---|
| `/api/*`, `/mars-photos/*`, `/health*`, `/openapi.json`, `/llms.txt` | `default-src 'none'; frame-ancestors 'none'` | enforced |
| `/docs` (API host) | scripts and styles from `cdn.jsdelivr.net`, inline script and style (FastAPI's page starts itself inline), favicon from `fastapi.tiangolo.com`, `connect-src 'self'` | enforced |
| `/redoc` (API host) | the same plus Google Fonts, `worker-src blob:` | enforced |
| everything else (web interface, `/developers`, on both hosts) | enforced `frame-ancestors 'none'`; the full policy as `Content-Security-Policy-Report-Only`: own scripts plus the theme snippet of `index.html` by hash, `'wasm-unsafe-eval'` for the 3D viewer, images from `'self'`, `api.solindex.net` (full-size MER images are our `/image` links on that host) and `*.nasa.gov`, `connect-src` own origin plus `assets.science.nasa.gov` (glTF models) and `www.gstatic.com` (Draco/KTX2 decoders, a guess: remove it if the 3D viewer reports nothing) | report-only |

Report-Only blocks nothing; violations appear in the browser console as `[Report Only] Refused
to ...` and nothing collects them (no report endpoint). To promote the policy: open every kind
of page (home, a sol with photos, a photo card with the 3D model opened, the route map, the
weather and radiation charts, `/developers`, the keys page) on `https://solindex.net` with the
console open and fix what is reported in the `Caddyfile`. Then, in the `@csp_web` lines, give the
enforced `Content-Security-Policy` the Report-Only value plus `; frame-ancestors 'none'` and delete
the Report-Only line. Every frontend release that adds an external host (an image CDN, a font,
an analytics script) must update this list first.

The inline theme snippet in `frontend/index.html` is allowed by its SHA-256. If its text changes
(even whitespace), recompute the hash and update the value in the `Caddyfile` (the build keeps the
script byte for byte: `frontend/index.html` and `frontend/dist/index.html` give the same hash):

```
python3 - <<'EOF'
import base64, hashlib, re
html = open("/srv/mars/repo/frontend/index.html", encoding="utf-8").read()
body = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
print("'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'")
EOF
```

A lasting fix belongs to the frontend (moving the snippet to a file served from `public/`, loaded
with a plain `<script src>` in the head, removes the hash and the `'unsafe-inline'` question for
scripts). With HTTP/1.1 only, browsers do not reuse one connection for both hosts, so the `421`
of `strict_sni_host` is not expected for them; a `421` in the Caddy log for ordinary browsers
means something is wrong (see the `Caddyfile` comment at `protocols`).

### 11. Rolling out the edge hardening (Caddyfile, nftables, ceiling)

Prepared files, changed together; apply in this order and stop at the first failure. Each step
has its own rollback.

**Check before applying** (read-only, as `deploy` over SSH; also make sure you can reach the
provider's VNC/console: it is the way back if a firewall or boot change goes wrong):

```
uname -r; nft --version                       # kernel 6.x and nft 1.0.x are expected
sudo nft list tables                          # the existing tables (ufw, docker, ...)
systemctl is-enabled nftables.service         # must NOT be enabled (it flushes the ruleset)
systemd-analyze verify /srv/mars/repo/deploy/vps/systemd/mars-limits.service
sudo bash /srv/mars/repo/deploy/vps/limits.sh show     # read the rules
sudo bash /srv/mars/repo/deploy/vps/limits.sh check    # nft -c: syntax only, loads nothing
sysctl net.netfilter.nf_conntrack_max net.netfilter.nf_conntrack_count   # headroom
cd /srv/mars && docker compose config -q                # compose still valid
IMG=$(grep -E '^CADDY_IMAGE=' /srv/mars/.env | cut -d= -f2-); IMG=${IMG:-caddy:2.10-alpine}   # the image already on the server
docker run --rm -v /srv/mars/repo/deploy/vps/Caddyfile:/etc/caddy/Caddyfile:ro "$IMG" caddy validate --config /etc/caddy/Caddyfile
docker run --rm -v /srv/mars/repo/deploy/vps/Caddyfile:/etc/caddy/Caddyfile:ro "$IMG" caddy adapt --pretty --config /etc/caddy/Caddyfile | less   # read what Caddy understood
# Expected warnings of validate: "Unnecessary header_up X-Forwarded-For/-Proto" (Caddy sets and
# REPLACES these itself while `trusted_proxies` is unset; the lines are kept as a guard).
grep -nE '^[[:space:]]*max_conns_per_host' /srv/mars/repo/deploy/vps/Caddyfile   # must print nothing
grep -nE '^[[:space:]]*protocols' /srv/mars/repo/deploy/vps/Caddyfile       # must be `protocols h1 h2` (NOT `h1` alone: see "Edge limits"); HTTP/2 is switched off by `import h1_only`
grep -c '^[[:space:]]import h1_only' /srv/mars/repo/deploy/vps/Caddyfile                  # 3: the two named sites and the :443 catch-all
ls -la /srv/mars/www 2>&1 | head -5            # absent before the first update.sh of this version: normal
docker image ls mars-rover-api                 # the images of the releases to publish and to roll back to
```

**Apply**

1. **nftables first**: the memory bound of Caddy and the overall ceiling rest on it, it changes
   nothing for visitors, and the load tests only need the site to answer. `limits.sh check`
   (the first real syntax test of the rules, the `conn all` rule included), then `apply`, then
   `status` (the counter of `conn all` is 0), then the two load tests of section 9. Test the
   reboot path only when the VNC console is at hand: `systemctl restart mars-limits.service`
   first (no reboot), then `sudo reboot` and `limits.sh status`. Then stop the unit once
   (`sudo systemctl stop mars-limits.service`) and check that `host-check.sh` reports it, and
   start it again. Rollback: `limits.sh remove` (immediate).
2. `git pull --ff-only` in `/srv/mars/repo`, then `update.sh --skip-pull` (image, web interface
   files copied to `/srv/mars/www/<commit>`, backup, migrations, `www/current` linked, app
   recreated with `UVICORN_LIMIT_CONCURRENCY=384`, Caddy recreated because `compose.yml` now
   mounts `./www` into it and its memory limit is 2g (a restart of a couple of seconds; certificates live in the
   `mars_caddy_data` volume), Caddyfile validated and reloaded). The update ends with
   "Web interface: Caddy and the app serve the same index.html". Check: `docker compose ps`
   healthy, `docker compose exec app printenv UVICORN_LIMIT_CONCURRENCY` prints `384`,
   `readlink /srv/mars/www/current` is the new commit, `docker compose exec caddy ls /srv/www/current`
   shows `index.html`, `assets`, `favicon.svg`. Then the rows "HTTP/1.1 only", "Client address and TLS state intact", "Web interface from Caddy",
   "Web interface: app routes untouched" and "Active release" of "Checks after a rollout", and
   open `https://solindex.net/` in a browser (a sol with photos, the 3D model, the route map)
   with the console open: no failed requests, no `[Report Only]` lines you did not expect.
   Rollback of the Caddy side (fastest): `git revert` the commit and `update.sh --skip-pull`, or
   delete `/srv/mars/www/current` (`rm /srv/mars/www/current`, no reload needed): Caddy then
   proxies every request to the app, which serves the interface itself, as before.
   Rollback of HTTP/1.1-only: delete the `import h1_only` lines (never switch to `protocols h1`),
   `update.sh --skip-pull`; the memory bound per address then no longer holds (see "Edge limits").
   Rollback of the release: "Update and rollback" (previous `APP_TAG` **and**
   `publish-frontend.sh <previous-tag>`).
3. **Immediately after the Caddy reload**, from the server (these bypass the firewall and DNS):
   `curl -sI --resolve api.solindex.net:443:127.0.0.1 https://api.solindex.net/health` -> `200`;
   `curl -sI -H 'Host: api.solindex.net' http://127.0.0.1/health` -> `308` to `https://`;
   `curl -sI -H 'Host: solindex.net' http://127.0.0.1/` -> `308`;
   `curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example' http://127.0.0.1/` ->
   `000`/empty reply. If the two `308` checks fail: remove the `:80 { abort }` block from the
   `Caddyfile` in the repository (or `git revert` the commit) and `update.sh --skip-pull`.
   Then the remaining rows of "Checks after a rollout" from outside (Unknown Host, docs, headers,
   smoke), and open `/docs` and `/redoc` on `api.solindex.net` in a browser.
## Laptop + Tailscale Funnel

**Fallback, switched off.** This was the production setup until the move to the VPS (section
above); it stays documented and untouched in `deploy/laptop/` as the way back. The scheduled task
`MarsServerBackup` on the laptop is disabled: after the move the laptop data is stale, so
its backups are of no use (a fallback restores a fresh VPS dump there, see "Migration from the
laptop", step 6).

**Keep it switched off, and why.** Tailscale Funnel forwards every path from the whole
internet to port 8000 and cannot filter paths; none of the VPS protections exist here (no Caddy:
no URL/header/body limits, no unknown-Host handling, no Content-Security-Policy or HSTS headers,
no nftables limits; the application's own limiter covers only the API paths). The state to
keep:

- Funnel is OFF: `tailscale funnel status` shows nothing (`tailscale funnel reset` if it does).
- `ADMIN_TOKEN` in the laptop `.env` is EMPTY: without it the admin routes do not exist (`404`),
  which is then the only thing between the internet and the admin API.
- The `app` container is stopped (`docker compose stop app`), so two schedulers do not hit NASA
  and the old data does not get served by mistake.

To use the laptop as the fallback: restore a fresh VPS dump (step 6 of "Migration from the
laptop", "Restore" below), check that `ADMIN_TOKEN` is empty, start the stack, `tailscale funnel
--bg 8000`, then `uv run python deploy/smoke.py https://<funnel-host>` (an https address is
checked as public: the admin path must answer `404` and `/openapi.json` must not mention
`admin`) and the client-IP checks of "Tailscale Funnel". Turn Funnel off again afterwards.

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
`/docs` the Swagger UI (on the VPS only on the API host: the site host redirects `/docs`,
`/redoc` and `/openapi.json` there); `/api/*`, `/mars-photos/*`, `/health` are unchanged.

Check after a rollout: `uv run python deploy/smoke.py <url>` (covers `/` and `/developers`), then
open `/` in a browser and load a sol. If `/` answers 404 or the old developers page, the image
has no `dist/`: `docker compose exec app ls /app/frontend/dist` must show `index.html` and
`assets/`.

## Smoke test

From any machine with the repository and uv (outside the laptop is best: it also checks Funnel):

```
uv run python deploy/smoke.py https://mars-api.sungrazing-menkent.ts.net
```

22 requests (17 count against the per-IP limit of 60/min; one more when `/openapi.json`
redirects), redirects are not followed except that one. Checks:
`/health` 200; `/` 200 HTML containing `id="app"` (the Sol Index interface, not the old
developers page) and one `/assets/*.js` from it with `Cache-Control: immutable`; `/developers`
200 HTML without `id="app"`; `/llms.txt` 200 containing the base URL (`PUBLIC_BASE_URL` is set); `/health/sync` 200 or 503 (body printed, 503 is a warning, not a
failure); v1 photos Curiosity sol 1000 FHAZ = 2 photos dated 2015-05-30; v1 rovers = 4; v1
Perseverance manifest; v2 rovers = 4; v2 photo `per_page=1` and its `/image` redirect `302`;
`/api/v1/rovers/curiosity/` -> `307` with an `https://` `Location` (proves `X-Forwarded-Proto`
is trusted); `If-None-Match` -> `304`; unknown rover -> `400 {"errors": "Invalid Rover Name"}`;
an Opportunity sol 1 photo whose `images.full` points to our `/api/v2/photos/{id}/image?size=full`;
`/openapi.json` (one redirect hop to the API host is followed) does not contain `admin` or
`x-admin-token`, case-insensitively; the admin API answers `404` on an https (public)
address for `GET /api/v2/admin/sync/runs`, `//api/v2/Admin/sync/runs`,
`/api/v2/%61dmin/sync/runs` and `POST /api/v2/admin/sync`. For a local server (`http://127.0.0.1:8000`, or `--local` with https) the
admin path may also answer `401` or `403` (admin enabled, token required); `200` or a server
error fails in every mode.
The flag `--with-mer-image` adds a `GET` of that image (`200 image/jpeg`): it makes the server
download and convert a PDS file, so use it after deploying the MER stage, not routinely.
Exit code 0 when all pass, 1 otherwise.
