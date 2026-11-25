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

# Import of a dataset export file (rad-import.sh, weather-import.sh): status, backup, import,
# status. KIND is the app.cli command (rad or weather); the rest are the script's arguments:
#   [--no-backup] [--force] PATH
# The import is idempotent (an identical file changes nothing), so a repeat is safe. The file
# is mounted read-only into a one-off container: the root file system of `app` is read-only.
# The backup before it is BACKUP_KIND=pre-import (its own rotation, 2 kept).
# Exit code: 0 done (or --help), 1 the app refused the file (far fewer sols than the database:
# use --force), 2 bad arguments, an invalid file or the lock is taken, 4 a step failed before
# the app could judge (db did not start, backup, docker or the container itself).
mars_import_dataset() {
  local kind="$1" backup=1 force=() path="" arg rc out usage mode
  shift
  usage="usage: ${0##*/} [--no-backup] [--force] PATH"
  for arg in "$@"; do
    case "$arg" in
      --no-backup) backup=0 ;;
      --force) force=(--force) ;;
      -h | --help) echo "$usage"; return 0 ;;
      -*) echo "unknown option: $arg ($usage)" >&2; return 2 ;;
      *) [[ -z "$path" ]] || { echo "only one PATH is accepted" >&2; return 2; }; path="$arg" ;;
    esac
  done
  [[ -n "$path" ]] || { echo "$usage" >&2; return 2; }
  [[ -f "$path" && -r "$path" ]] || { echo "file not found or not readable: $path" >&2; return 2; }
  path="$(realpath -e -- "$path")" || { echo "cannot resolve the path: $path" >&2; return 2; }
  case "$path" in
    *:* | *,*) echo "the path must not contain ':' or ',' (docker volume syntax): $path" >&2; return 2 ;;
  esac
  # The container runs as uid 10001: the file must be readable by others.
  mode="$(stat -c %a -- "$path")"
  if (( ((8#$mode) & 4) == 0 )); then
    echo "the file is not world-readable (the container runs as uid 10001): chmod 644 $path" >&2
    return 2
  fi
  log "Import of $path into the $kind data (project $MARS_PROJECT)"
  mars_compose up -d --wait db >/dev/null || { log "FAILED: the db container did not become healthy"; return 4; }
  log "--- $kind status before"
  mars_compose run --rm --no-deps -T app python -m app.cli "$kind" status \
    || { log "FAILED: $kind status (docker, compose or the database)"; return 4; }
  if [[ "$backup" -eq 1 ]]; then
    log "--- backup before the import (pre-import kind, 2 kept; --no-backup skips it)"
    BACKUP_KIND=pre-import "$(dirname "${BASH_SOURCE[0]}")/backup.sh" \
      || { log "FAILED: the backup (nothing was imported)"; return 4; }
  else
    log "--- backup skipped (--no-backup)"
  fi
  log "--- $kind import"
  out="$(mktemp)"
  set +e
  mars_compose run --rm -T -v "$path:/in/x.jsonl.gz:ro" app \
    python -m app.cli "$kind" import --input /in/x.jsonl.gz "${force[@]}" 2>&1 | tee "$out"
  rc="${PIPESTATUS[0]}"
  set -e
  case "$rc" in
    0) ;;
    1)
      if grep -q 'use --force' "$out"; then
        rm -f "$out"
        log "IMPORT REFUSED: the file has far fewer sols than the database. Check that it is the right file; if so, repeat with --force"
        return 1
      fi
      rm -f "$out"
      log "IMPORT FAILED with exit code 1 before the app judged the file (docker, compose or a crash): see the output above"
      return 4
      ;;
    2) rm -f "$out"; log "IMPORT FAILED: invalid file or another import/sync holds the lock (exit code 2)"; return 2 ;;
    *) rm -f "$out"; log "IMPORT FAILED with exit code $rc (docker or the container)"; return 4 ;;
  esac
  rm -f "$out"
  log "--- $kind status after"
  mars_compose run --rm --no-deps -T app python -m app.cli "$kind" status \
    || { log "WARNING: the import is done but $kind status failed"; return 4; }
  log "Import done. The file can be deleted: rm $path"
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
