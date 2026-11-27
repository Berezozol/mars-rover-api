#!/usr/bin/env bash
# Makes Caddy serve the web interface of an app image: copies /app/frontend/dist out of
# mars-rover-api:<tag> into /srv/mars/www/<tag> (if not there yet) and points www/current at it.
# update.sh does this by itself; run it by hand after a ROLLBACK of the app (README, "Update and
# rollback"), so that Caddy serves the files of the image that is running.
#
# Usage (as deploy, on the server):  /srv/mars/repo/deploy/vps/publish-frontend.sh <tag>
# Without arguments it shows the staged releases and the active one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

if [[ $# -eq 0 ]]; then
  echo "usage: ${0##*/} <image-tag>   (docker image ls mars-rover-api; APP_TAG in $ENV_FILE is $(env_get APP_TAG))" >&2
  echo "active: $(readlink "$WWW_DIR/current" 2>/dev/null || echo none)" >&2
  ls -1 "$WWW_DIR" 2>/dev/null | grep -v '^current$' >&2 || true
  exit 2
fi
[[ $# -eq 1 ]] && mars_valid_tag "$1" || { echo "usage: ${0##*/} <image-tag>" >&2; exit 2; }
mars_stage_frontend "$1"
mars_activate_frontend "$1"
log "Caddy now serves the web interface of mars-rover-api:$1 ($WWW_DIR/current -> $1)"
