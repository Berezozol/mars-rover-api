#!/usr/bin/env bash
# Host and stack check for Telegram alerts (systemd timer mars-hostcheck.timer, every 5 min).
# Checks: disk, memory, the three containers (running and healthy), /health of the app,
# HTTPS through Caddy with real certificate validation, age of the newest backup.
# Alert rules: a message when the set of problems changes, a reminder every 6 hours while it
# lasts, and "OK again" when it clears. The exit code is always 0 unless the script itself
# breaks (a failing check is the message, not a failed unit).
#
# Test an alert: HOSTCHECK_TEST=1 host-check.sh   (reports a fake problem, then clears it)
#   or lower a threshold for one run: DISK_WARN_PCT=1 host-check.sh
# Thresholds (environment): DISK_WARN_PCT (85), MEM_AVAIL_MIN_PCT (10),
#   BACKUP_MAX_AGE_HOURS (36), BACKUP_PULL_MAX_AGE_HOURS (72), PUBLIC_HOST (api.solindex.net).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

DISK_WARN_PCT="${DISK_WARN_PCT:-85}"
MEM_AVAIL_MIN_PCT="${MEM_AVAIL_MIN_PCT:-10}"
BACKUP_MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-36}"
BACKUP_PULL_MAX_AGE_HOURS="${BACKUP_PULL_MAX_AGE_HOURS:-72}"
PUBLIC_HOST="${PUBLIC_HOST:-api.solindex.net}"
REPEAT_SECONDS=$((6 * 3600))
STATE_DIR="${STATE_DIRECTORY:-$HOME/.local/state/mars-hostcheck}"
STATE_FILE="$STATE_DIR/state"
mkdir -p "$STATE_DIR"

problems=()
add() { problems+=("$1"); }

# Disk: the root filesystem holds the Docker volumes and the backups.
used_pct="$(df --output=pcent / | tail -n 1 | tr -dc '0-9')"
if [[ -n "$used_pct" ]] && (( used_pct >= DISK_WARN_PCT )); then
  add "disk / is ${used_pct}% full (limit ${DISK_WARN_PCT}%)"
fi

# Memory: MemAvailable counts reclaimable page cache.
mem_total="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
mem_avail="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
if [[ -n "$mem_total" && -n "$mem_avail" ]] && (( mem_avail * 100 < mem_total * MEM_AVAIL_MIN_PCT )); then
  add "memory: only $((mem_avail / 1024)) MB available of $((mem_total / 1024)) MB"
fi

# Containers: each service must run, and be healthy where it has a healthcheck.
for svc in db app caddy; do
  cid="$(mars_compose ps -q "$svc" 2>/dev/null | head -n 1)"
  if [[ -z "$cid" ]]; then
    add "container $svc does not exist"
    continue
  fi
  state="$(docker inspect -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null)"
  status="${state%%|*}"
  health="${state#*|}"
  if [[ "$status" != "running" ]]; then
    add "container $svc is $status"
  elif [[ -n "$health" && "$health" != "healthy" ]]; then
    add "container $svc is $health"
  fi
done

# The application directly (bypasses Caddy) ...
if ! curl -fsS -m 10 -o /dev/null http://127.0.0.1:8000/health; then
  add "app /health on 127.0.0.1:8000 does not answer 200"
fi
# ... and through Caddy over HTTPS from this host (resolves the name to localhost, validates
# the certificate: an expired or missing certificate fails here). HEAD only.
if ! curl -fsS -m 15 -I -o /dev/null --resolve "${PUBLIC_HOST}:443:127.0.0.1" "https://${PUBLIC_HOST}/health"; then
  add "https://${PUBLIC_HOST}/health through Caddy fails (certificate, Caddy or app)"
fi

# Backup age: the newest mars-*.dump.
newest="$(latest_dump)"
if [[ -z "$newest" ]]; then
  add "no backups in $BACKUP_DIR"
else
  age_h=$(( ($(date +%s) - $(stat -c %Y "$newest")) / 3600 ))
  if (( age_h > BACKUP_MAX_AGE_HOURS )); then
    add "newest backup is ${age_h} h old ($(basename "$newest"))"
  fi
fi

# Off-server copy: pull-backup.ps1 on the owner's computer touches .last-pulled after every
# successful download. Checked only once such a file exists (i.e. the pull has been set up).
pulled="$BACKUP_DIR/.last-pulled"
if [[ -f "$pulled" ]]; then
  pulled_h=$(( ($(date +%s) - $(stat -c %Y "$pulled")) / 3600 ))
  if (( pulled_h > BACKUP_PULL_MAX_AGE_HOURS )); then
    add "no off-server backup download for ${pulled_h} h (the owner's computer has not pulled; limit ${BACKUP_PULL_MAX_AGE_HOURS} h)"
  fi
fi

if [[ "${HOSTCHECK_TEST:-0}" == "1" ]]; then
  add "TEST alert (HOSTCHECK_TEST=1), not a real problem"
fi

# --- Alert logic ---------------------------------------------------------------------------
now="$(date +%s)"
current="$(printf '%s\n' "${problems[@]:-}" | sort | sha256sum | cut -d' ' -f1)"
[[ ${#problems[@]} -eq 0 ]] && current="ok"
prev_hash=""; prev_sent=0
if [[ -f "$STATE_FILE" ]]; then
  read -r prev_hash prev_sent <"$STATE_FILE" || true
fi

if [[ "$current" == "ok" ]]; then
  if [[ -n "$prev_hash" && "$prev_hash" != "ok" ]]; then
    notify "OK again: all checks pass on $(hostname)"
  fi
  echo "ok $now" >"$STATE_FILE"
  log "all checks pass"
  exit 0
fi

summary="PROBLEM on $(hostname):"$'\n'"$(printf -- '- %s\n' "${problems[@]}")"
log "$summary"
if [[ "$current" != "$prev_hash" ]] || (( now - prev_sent >= REPEAT_SECONDS )); then
  notify "$summary"
  echo "$current $now" >"$STATE_FILE"
fi
exit 0
