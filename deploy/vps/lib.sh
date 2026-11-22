#!/usr/bin/env bash
# Shared helpers of the VPS scripts (sourced, not executed). Layout on the server:
#   /srv/mars/compose.yml, Caddyfile, db-roles.sql   live copies (update.sh refreshes them)
#   /srv/mars/.env, secrets/pg_owner_password        secrets (chmod 600 / 700)
#   /srv/mars/backups/                         pg_dump archives, backup.log
#   /srv/mars/repo/                            git clone of the project (scripts live here)
# MARS_DIR overrides the base directory (for a rehearsal copy).

MARS_DIR="${MARS_DIR:-/srv/mars}"
MARS_PROJECT="${MARS_PROJECT:-mars}"
BACKUP_DIR="${BACKUP_DIR:-$MARS_DIR/backups}"
ENV_FILE="$MARS_DIR/.env"
COMPOSE_FILE="$MARS_DIR/compose.yml"

# docker compose bound to the live directory (reads .env there for interpolation).
mars_compose() {
  docker compose -p "$MARS_PROJECT" -f "$COMPOSE_FILE" --project-directory "$MARS_DIR" "$@"
}

# Value of KEY from .env without sourcing the file (no code execution, no quotes handling
# beyond stripping one pair of surrounding quotes). Prints nothing when unset or commented out.
env_get() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  if [[ "$line" =~ ^\"(.*)\"$ || "$line" =~ ^\'(.*)\'$ ]]; then
    line="${BASH_REMATCH[1]}"
  fi
  printf '%s' "$line"
}

# compose.yml interpolates APP_TAG in every command, even for db-only ones. The tag comes from
# the environment (update.sh), else from .env; before the first update.sh a placeholder keeps
# db-only commands (backup, restore) working.
APP_TAG="${APP_TAG:-$(env_get APP_TAG)}"
export APP_TAG="${APP_TAG:-none}"

log() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S')" "$*"
}

# Telegram message through the same bot as the application alerts (TELEGRAM_BOT_TOKEN and
# TELEGRAM_CHAT_ID in .env). Silent no-op without them. The token goes to curl through
# stdin (--config -), so it is not visible in the process list.
notify() {
  local text="$1" token chat base
  token="$(env_get TELEGRAM_BOT_TOKEN)"
  chat="$(env_get TELEGRAM_CHAT_ID)"
  base="$(env_get TELEGRAM_API_BASE)"
  base="${base:-https://api.telegram.org}"
  [[ -n "$token" && -n "$chat" ]] || return 0
  curl -fsS -m 20 -o /dev/null --config - \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=[mars-vps] ${text}" \
    <<<"url = \"${base}/bot${token}/sendMessage\"" \
    || log "WARNING: could not send the Telegram message"
}

# Newest dump in BACKUP_DIR (full path) or nothing.
latest_dump() {
  local f
  f="$(ls -1 "$BACKUP_DIR"/mars-*.dump 2>/dev/null | sort | tail -n 1 || true)"
  printf '%s' "$f"
}

# Alembic as the schema owner `mars` (one-off container `migrate`, owner password from the
# secret file; the running app uses the restricted role mars_app).
mars_migrate() {
  mars_compose run --rm --no-deps migrate
}

# Create/refresh the mars_app role and its grants (idempotent). Run after every migration and
# after a restore; needs the db container running. APP_DB_PASSWORD is in the db container's
# own environment, so no secret is passed on a command line.
mars_apply_roles() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  mars_compose exec -T db psql -U mars -d mars -q -v ON_ERROR_STOP=1 -f - <"$dir/db-roles.sql"
}
