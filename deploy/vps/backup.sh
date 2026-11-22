#!/usr/bin/env bash
# Backs up the database: pg_dump (custom format) streamed from the db container to a host
# file, checked with pg_restore --list, rotated (newest KEEP kept). Optional upload to remote
# storage with rclone when BACKUP_REMOTE is set in .env (otherwise that step is skipped).
# Exit code 0 on success, 1 on failure (and a Telegram message when alerts are configured).
#
# Runs as the deploy user (systemd timer mars-backup.timer, or by hand, or from update.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

KEEP="${KEEP:-7}"
LOG_FILE="$BACKUP_DIR/backup.log"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y%m%d-%H%M%S)"
final="$BACKUP_DIR/mars-$stamp.dump"
partial="$final.partial"

say() { log "$*" | tee -a "$LOG_FILE"; }

fail() {
  say "BACKUP FAILED: $*"
  rm -f "$partial"
  notify "backup FAILED: $*"
  exit 1
}

say "Backup started: project=$MARS_PROJECT -> $final"

# Streamed straight to the host: the dump never exists inside the container, and a binary
# redirect is safe in bash (unlike PowerShell 5.1 pipelines).
if ! mars_compose exec -T db pg_dump -U mars -d mars -Fc --no-acl >"$partial"; then
  fail "pg_dump exited with an error"
fi
[[ -s "$partial" ]] || fail "dump is empty"

# A readable table of contents means the archive is complete and restorable by pg_restore.
if ! mars_compose exec -T db pg_restore --list <"$partial" >/dev/null; then
  fail "pg_restore --list cannot read the archive"
fi

mv "$partial" "$final"
# Checksum written only now, after the archive was verified: pull-backup.ps1 on the owner's
# computer downloads only dumps that have one and compares it after the copy.
( cd "$BACKUP_DIR" && sha256sum "$(basename "$final")" >"$(basename "$final").sha256" )
size_mb="$(du -m "$final" | cut -f1)"
say "Backup OK: $final (${size_mb} MB)"

# Rotation: keep the newest KEEP archives.
mapfile -t old < <(ls -1 "$BACKUP_DIR"/mars-*.dump 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))")
for f in "${old[@]}"; do
  rm -f "$f" "$f.sha256"
  say "Rotated out: $(basename "$f")"
done

# Optional off-server copy to a bucket (the main off-server copy is pulled by the owner's
# computer: deploy/vps/pull-backup.ps1). BACKUP_REMOTE is an rclone destination, e.g. "myremote:bucket/mars"
# (configure the remote with `rclone config` as the deploy user first). Not set: skipped.
remote="$(env_get BACKUP_REMOTE)"
if [[ -n "$remote" ]]; then
  if ! command -v rclone >/dev/null 2>&1; then
    fail "BACKUP_REMOTE is set but rclone is not installed"
  fi
  if ! rclone copyto "$final" "${remote%/}/$(basename "$final")" --retries 3 2>>"$LOG_FILE" \
    || ! rclone copyto "$final.sha256" "${remote%/}/$(basename "$final").sha256" --retries 3 2>>"$LOG_FILE"; then
    fail "rclone upload to the remote storage failed (the local backup is fine)"
  fi
  say "Uploaded to remote: ${remote%/}/$(basename "$final")"
  # Remote retention is the storage's business (lifecycle rule) or: rclone delete --min-age 30d
else
  say "Off-server upload skipped (BACKUP_REMOTE is not set)"
fi
