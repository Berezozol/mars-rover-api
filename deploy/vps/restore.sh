#!/usr/bin/env bash
# Restores the database from a dump made by backup.sh. THIS REPLACES THE WHOLE `mars`
# DATABASE. Run it only after an explicit decision (README, "Restore").
#
# Usage: restore.sh [--yes] [--no-safety-dump] [--jobs N] <dump file inside /srv/mars/backups>
#   The dump must be in BACKUP_DIR (the db container sees that directory read-only as
#   /backups). Copy a dump from another machine there first.
#   --yes             do not ask for the confirmation word
#   --no-safety-dump  skip the pre-restore dump of the current state (use when the current
#                     database is unreadable; the script tries a dump by default)
#   --jobs N          parallel pg_restore jobs (default 4)
#
# Steps: stop app -> (safety dump) -> drop + create database -> pg_restore -> alembic upgrade
# head (the dump may be from an older schema) -> start app -> check /health.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

assume_yes=0
safety=1
jobs=4
dump=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) assume_yes=1 ;;
    --no-safety-dump) safety=0 ;;
    --jobs) shift; jobs="${1:?--jobs needs a number}" ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) dump="$1" ;;
  esac
  shift
done
[[ -n "$dump" ]] || { echo "usage: restore.sh [--yes] [--no-safety-dump] [--jobs N] <dump>" >&2; exit 2; }
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "--jobs must be a positive number" >&2; exit 2; }

# Accept a bare name or a path inside BACKUP_DIR.
name="$(basename "$dump")"
host_path="$BACKUP_DIR/$name"
[[ -s "$host_path" ]] || { echo "not found or empty: $host_path (the dump must be in $BACKUP_DIR)" >&2; exit 1; }
[[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "unsafe file name: $name" >&2; exit 1; }
container_path="/backups/$name"

echo "Dump:     $host_path ($(du -m "$host_path" | cut -f1) MB)"
echo "Database: mars in project $MARS_PROJECT -- it will be DROPPED and recreated."
if [[ "$assume_yes" -ne 1 ]]; then
  read -r -p "Type 'restore' to continue: " word
  [[ "$word" == "restore" ]] || { echo "cancelled"; exit 1; }
fi

# The dump must be readable before anything is destroyed.
mars_compose up -d --wait db
mars_compose exec -T db pg_restore --list "$container_path" >/dev/null \
  || { echo "pg_restore --list cannot read $container_path; nothing was changed" >&2; exit 1; }

log "Stopping app"
mars_compose stop app

if [[ "$safety" -eq 1 ]]; then
  safety_file="$BACKUP_DIR/pre-restore-$(date -u +%Y%m%d-%H%M%S).dump"
  log "Safety dump of the current database -> $safety_file"
  if mars_compose exec -T db pg_dump -U mars -d mars -Fc --no-acl >"$safety_file" && [[ -s "$safety_file" ]]; then
    log "Safety dump OK (delete it by hand when no longer needed; rotation ignores it)"
  else
    rm -f "$safety_file"
    log "WARNING: safety dump failed; continuing (the database is replaced anyway)"
  fi
fi

# From here on the old database is gone: say where it can be found if anything fails.
safety_note="${safety_file:+ The previous state is in $safety_file.}"
trap 'rc=$?; log "RESTORE FAILED after the database was dropped (exit $rc).${safety_note}"; notify "RESTORE FAILED after dropping the database.${safety_note}"; exit "$rc"' ERR

log "Dropping and creating the database"
mars_compose exec -T db dropdb -U mars --if-exists --force mars
mars_compose exec -T db createdb -U mars -O mars mars

log "pg_restore (jobs=$jobs)"
mars_compose exec -T db pg_restore -U mars -d mars --no-owner --no-acl --role=mars --exit-on-error \
  --jobs "$jobs" "$container_path"

log "alembic upgrade head (as the owner role)"
mars_migrate

log "Grants of the application role mars_app"
mars_apply_roles

log "Starting app"
mars_compose up -d app

for _ in $(seq 1 40); do
  if curl -fsS -m 5 -o /dev/null http://127.0.0.1:8000/health; then
    trap - ERR
    log "RESTORE OK: /health answers 200"
    notify "database restored from $name"
    exit 0
  fi
  sleep 3
done
log "RESTORE FINISHED BUT /health does not answer: docker compose -p $MARS_PROJECT logs --tail 200 app"
exit 1
