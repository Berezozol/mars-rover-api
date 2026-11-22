#!/usr/bin/env bash
# Updates the Mars Rover API on the VPS: pull, build, refresh compose/Caddyfile, backup,
# migrate, restart, health check. Each step must succeed before the next one; nothing is rolled
# back and the database is never restored automatically (README, "Restore").
#
# Usage (as deploy, on the server):  /srv/mars/repo/deploy/vps/update.sh [--skip-pull]
#   --skip-pull   build what is checked out now, without git pull
# Environment: BRANCH (default master), KEEP_IMAGES (default 3).
#
# Steps:
#   1. git pull --ff-only in /srv/mars/repo
#   2. docker build -t mars-rover-api:<commit>
#   3. copy compose.yml, Caddyfile and db-roles.sql to /srv/mars, validate the Compose file
#   4. start db, fresh backup (backup.sh)
#   5. migrations as the owner role in a one-off container with the NEW image (the old app
#      keeps serving), then db-roles.sql (grants of the restricted role mars_app)
#   6. APP_TAG=<commit> in .env, recreate app, start/reload caddy
#   7. wait for /health, remove old images
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

REPO_DIR="${REPO_DIR:-$MARS_DIR/repo}"
BRANCH="${BRANCH:-master}"
KEEP_IMAGES="${KEEP_IMAGES:-3}"
HEALTH_URL="http://127.0.0.1:8000/health"
HEALTH_TIMEOUT=120

skip_pull=0
[[ "${1:-}" == "--skip-pull" ]] && skip_pull=1

step_name="start"
on_error() {
  echo
  echo "UPDATE FAILED at step: $step_name" >&2
  echo "Nothing was rolled back. Logs: docker compose -p $MARS_PROJECT logs --tail 200 app" >&2
  echo "Previous image tag (rollback): ${prev_tag:-unknown} -- README, \"Update and rollback\"" >&2
  notify "update FAILED at step: $step_name"
}
trap on_error ERR

step() { step_name="$1"; echo; log "== $1"; }

[[ -f "$ENV_FILE" ]] || { echo "missing $ENV_FILE (copy deploy/vps/.env.example)" >&2; exit 1; }
[[ -s "$MARS_DIR/secrets/pg_owner_password" ]] || { echo "missing $MARS_DIR/secrets/pg_owner_password (README, install step 2)" >&2; exit 1; }
# Secrets stay private to the deploy user (the secret file itself is 0644 inside a 0700
# directory: the containers read it through the bind mount).
chmod 600 "$ENV_FILE"
chmod 700 "$MARS_DIR/secrets"
chmod 644 "$MARS_DIR/secrets/pg_owner_password"
[[ -d "$REPO_DIR/.git" ]] || { echo "missing git clone $REPO_DIR" >&2; exit 1; }
prev_tag="$(env_get APP_TAG)"

step "1/7 git pull --ff-only ($REPO_DIR, $BRANCH)"
if [[ "$skip_pull" -eq 1 ]]; then
  echo "skipped (--skip-pull)"
else
  git -C "$REPO_DIR" fetch --quiet origin "$BRANCH"
  git -C "$REPO_DIR" checkout --quiet "$BRANCH"
  git -C "$REPO_DIR" merge --ff-only "origin/$BRANCH"
fi
commit="$(git -C "$REPO_DIR" rev-parse --short=12 HEAD)"
if [[ -n "$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)" ]]; then
  echo "WARNING: the clone has local changes; the image is built from the working tree"
fi
echo "Commit: $commit (previous image tag: ${prev_tag:-none})"

step "2/7 docker build -t mars-rover-api:$commit"
docker build -t "mars-rover-api:$commit" "$REPO_DIR"
# From here on every compose call (and backup.sh) interpolates the new tag; .env gets it in step 6.
export APP_TAG="$commit"

step "3/7 compose.yml and Caddyfile -> $MARS_DIR"
# cp over the existing file keeps its inode: the Caddyfile is bind-mounted as a single file.
caddy_changed=0
cmp -s "$SCRIPT_DIR/Caddyfile" "$MARS_DIR/Caddyfile" 2>/dev/null || caddy_changed=1
cp "$SCRIPT_DIR/compose.yml" "$MARS_DIR/compose.yml"
cp "$SCRIPT_DIR/db-roles.sql" "$MARS_DIR/db-roles.sql"
cp "$SCRIPT_DIR/Caddyfile" "$MARS_DIR/Caddyfile"
chmod 644 "$MARS_DIR/Caddyfile"
# Interpolation needs APP_TAG: validate with the new tag, without touching .env yet.
mars_compose config -q
echo "compose.yml valid; Caddyfile changed: $caddy_changed"

step "4/7 database up and backup"
mars_compose up -d --wait db
BACKUP_KIND=pre-update "$SCRIPT_DIR/backup.sh"

step "5/7 migrations: alembic upgrade head (one-off container, new image)"
mars_migrate
echo "Grants of the application role mars_app"
mars_apply_roles

step "6/7 app with the new image, caddy"
if grep -q '^APP_TAG=' "$ENV_FILE"; then
  sed -i "s/^APP_TAG=.*/APP_TAG=$commit/" "$ENV_FILE"
else
  printf '\nAPP_TAG=%s\n' "$commit" >>"$ENV_FILE"
fi
mars_compose up -d app
mars_compose up -d caddy
if [[ "$caddy_changed" -eq 1 ]]; then
  mars_compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile
  mars_compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
fi

step "7/7 waiting for $HEALTH_URL (up to $HEALTH_TIMEOUT s)"
deadline=$((SECONDS + HEALTH_TIMEOUT))
until body="$(curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null)"; do
  if (( SECONDS >= deadline )); then
    false  # triggers the ERR trap with this step name
  fi
  sleep 3
done
echo "Health: $body"

# Keep the newest KEEP_IMAGES tags of the app image (the running one and rollback targets).
mapfile -t stale < <(docker image ls mars-rover-api --format '{{.Tag}} {{.CreatedAt}}' \
  | grep -v '^<none>' | sort -k2 -r | awk '{print $1}' | tail -n +"$((KEEP_IMAGES + 1))")
for tag in "${stale[@]}"; do
  [[ "$tag" == "$commit" || "$tag" == "$prev_tag" ]] && continue
  docker image rm "mars-rover-api:$tag" >/dev/null 2>&1 || true
done
docker image prune -f >/dev/null

trap - ERR
echo
log "UPDATE OK: mars-rover-api:$commit is serving (previous: ${prev_tag:-none})"
