#!/usr/bin/env bash
# Monthly update of the MSL RAD radiation data (mars-rad-sync.timer): `rad sync` in a one-off
# container of the app image. It streams the files of the PDS volume through the parser and
# writes nothing to disk. Only sols whose files are new or changed are loaded: with nothing new
# a run is 2 conditional requests (304); a new release is about 130 files, 9 GB, 30 minutes.
#
# Usage: rad-sync.sh [--initial]
#   --initial   allow the run on a database WITHOUT RAD history: that downloads the whole
#               volume (about 230-340 GB, 12-45 hours). Without it such a run is refused (exit 2)
#               because it means the export was not imported yet (README, section 7a).
#
# Exit code 0 = success; anything else fails the unit. `rad sync` sends a Telegram message
# itself for exit codes 1 and 3 and prints its summary line ("rad sync: ..."); when that line
# (or the crash log) is missing, the failure happened before the app (docker, compose, python
# start) and this script sends the message. It also sends one for every other code, for the
# refused run and for a stop by systemd (timeout, `systemctl stop`, reboot: exit 143).
#
# Runs as the deploy user (systemd timer mars-rad-sync.timer, or by hand).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

CONTAINER="mars-rad-sync"
LOG_FILE="$BACKUP_DIR/rad-sync.log"
LOG_KEEP_LINES=10000
initial=0
case "${1:-}" in
  "") ;;
  --initial) initial=1 ;;
  -h | --help) echo "usage: ${0##*/} [--initial]"; exit 0 ;;
  *) echo "usage: ${0##*/} [--initial]" >&2; exit 2 ;;
esac

mkdir -p "$BACKUP_DIR"
# Keep the log bounded: the last LOG_KEEP_LINES lines survive each start.
if [[ -f "$LOG_FILE" ]] && [[ "$(wc -l <"$LOG_FILE")" -gt "$LOG_KEEP_LINES" ]]; then
  tail -n "$LOG_KEEP_LINES" "$LOG_FILE" >"$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
say() { log "$*" | tee -a "$LOG_FILE"; }

out="$(mktemp)"
pid=""
cleanup() { rm -f "$out"; }

# systemd stops the unit (TimeoutStartSec, `systemctl stop`, reboot) with SIGTERM: stop the
# one-off container so it does not go on holding the advisory lock, then tell the owner.
# Bash runs a trap only between commands, hence the `wait` on a background job below.
on_stop() {
  trap '' TERM INT
  say "rad sync STOPPED by a signal: stopping the container $CONTAINER"
  docker stop -t 30 "$CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  [[ -n "$pid" ]] && wait "$pid" 2>/dev/null
  cat "$out" >>"$LOG_FILE" 2>/dev/null
  notify "rad sync STOPPED (timeout, systemctl stop or reboot); the next run continues where it stopped. See $LOG_FILE"
  cleanup
  exit 143
}
trap on_stop TERM INT
trap cleanup EXIT

say "rad sync started: project=$MARS_PROJECT"
# A leftover container from an earlier killed run would keep the name (and maybe the lock).
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  say "removing a stale container $CONTAINER"
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
fi

# Guard: refuse to download the whole history by mistake (the timer enabled before the import).
if [[ "$initial" -eq 0 ]]; then
  if ! mars_compose run --rm --no-deps -T app python -m app.cli rad status >"$out" 2>&1; then
    cat "$out" >>"$LOG_FILE"
    say "rad sync FAILED: cannot read rad status (docker, compose or the database)"
    notify "rad sync FAILED: cannot read rad status (docker, compose or the database); see $LOG_FILE"
    exit 4
  fi
  sols="$(sed -n 's/^sols: \([0-9][0-9]*\) .*/\1/p' "$out" | head -n 1)"
  if [[ -z "$sols" || "$sols" -eq 0 ]]; then
    cat "$out" >>"$LOG_FILE"
    say "rad sync REFUSED: no RAD history in the database (import it first, README 7a; or --initial to download everything)"
    notify "rad sync REFUSED: no RAD history in the database; import the export first (README 7a). Nothing was downloaded."
    exit 2
  fi
fi

# Background job + wait, so the trap above can run while the container works.
: >"$out"
mars_compose run --rm --no-deps -T --name "$CONTAINER" app python -m app.cli rad sync >"$out" 2>&1 &
pid=$!
wait "$pid"
rc=$?
pid=""
cat "$out" | tee -a "$LOG_FILE"

# Did the app itself report this failure? Its summary line starts with "rad sync: " (printed
# after every finished run) and a crash logs "RAD sync crashed".
app_reported=0
grep -qE '^rad sync: |RAD sync crashed' "$out" && app_reported=1

case "$rc" in
  0) say "rad sync OK" ;;
  1 | 3)
    say "rad sync FAILED with exit code $rc"
    if [[ "$app_reported" -eq 1 ]]; then
      : # the app sent the Telegram message
    else
      notify "rad sync FAILED with exit code $rc before the app reported (docker, compose or python); see $LOG_FILE"
    fi
    ;;
  *)
    say "rad sync FAILED with exit code $rc"
    notify "rad sync FAILED with exit code $rc (2: lock taken or bad input, 130: interrupted); see $LOG_FILE"
    ;;
esac
exit "$rc"
