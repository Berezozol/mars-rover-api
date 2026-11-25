#!/usr/bin/env bash
# Loads a RAD export (made on the development machine with `rad export`) into the VPS database:
# prints `rad status`, takes a backup (BACKUP_KIND=pre-import), runs `rad import`, prints `rad status` again.
# Safe to repeat: importing an identical file changes nothing (exit code 0).
#
# Usage (as deploy, on the server):
#   /srv/mars/repo/deploy/vps/rad-import.sh [--no-backup] [--force] PATH
#   --no-backup   skip the backup that normally runs first (README, section 7a, requires one)
#   --force       import even if the file has far fewer sols than the database (see rad import)
# PATH is a .jsonl.gz file, e.g. /srv/mars/rad-<date>.jsonl.gz (not in backups/: rotation).
# The running image must already contain `python -m app.cli rad`. Exit code: 0 done, 1 the
# import was refused (use --force), 2 bad arguments, invalid file or lock taken, 4 a step failed
# before the app could judge (db, backup, docker).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

mars_import_dataset rad "$@"
