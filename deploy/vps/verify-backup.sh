#!/usr/bin/env bash
# Proves that the newest dump really restores: loads it into a scratch database
# `mars_verify` in the running db container, compares row counts of the key tables with the
# live database, drops the scratch database. The live database and the app are not touched
# (it only costs CPU, I/O and temporary disk space).
# Exit code 0 = restorable, 1 = not (and a Telegram message).
#
# Runs weekly from systemd (mars-verify-backup.timer) or by hand:
#   /srv/mars/repo/deploy/vps/verify-backup.sh [dump-name]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

SCRATCH_DB="mars_verify"
# Tables compared with the live database; the live one may have grown since the dump
# (sync runs), so only "not empty" is required for photos and rovers; more rows in the dump
# than live is reported as a warning.
TABLES=(photos rovers cameras sync_runs)

dump="${1:-$(latest_dump)}"
[[ -n "$dump" ]] || { echo "no dumps in $BACKUP_DIR"; notify "verify-backup: no dump found in $BACKUP_DIR"; exit 1; }
name="$(basename "$dump")"
[[ -s "$BACKUP_DIR/$name" ]] || { echo "not found: $BACKUP_DIR/$name"; exit 1; }

psql_db() { mars_compose exec -T db psql -U mars -d "$1" -v ON_ERROR_STOP=1 -tA -c "$2"; }

cleanup() { mars_compose exec -T db dropdb -U mars --if-exists --force "$SCRATCH_DB" >/dev/null 2>&1 || true; }
fail() { log "VERIFY FAILED ($name): $*"; notify "backup verification FAILED ($name): $*"; cleanup; exit 1; }
trap cleanup EXIT

# Room for the scratch copy: restoring needs roughly the size of the live database.
db_bytes="$(psql_db mars "select pg_database_size('mars')")"
free_bytes="$(df -B1 --output=avail "$MARS_DIR" | tail -n 1 | tr -d ' ')"
if (( free_bytes < db_bytes * 2 )); then
  fail "not enough free disk space (free ${free_bytes} B, database ${db_bytes} B)"
fi

log "Verifying $name in scratch database $SCRATCH_DB"
cleanup
mars_compose exec -T db createdb -U mars -O mars "$SCRATCH_DB" || fail "createdb"
mars_compose exec -T db pg_restore -U mars -d "$SCRATCH_DB" --no-owner --no-acl --role=mars \
  --exit-on-error --jobs 2 "/backups/$name" || fail "pg_restore"

for t in "${TABLES[@]}"; do
  restored="$(psql_db "$SCRATCH_DB" "select count(*) from $t" 2>/dev/null)" || fail "table $t is missing in the restored database"
  live="$(psql_db mars "select count(*) from $t")"
  log "  $t: restored=$restored live=$live"
  if [[ "$t" == "photos" || "$t" == "rovers" ]] && (( restored == 0 )); then
    fail "table $t is empty in the restored database"
  fi
  if (( restored > live )); then
    log "  WARNING: $t has more rows in the dump than in the live database (rows deleted since?)"
  fi
done

log "VERIFY OK: $name restores into a scratch database"
