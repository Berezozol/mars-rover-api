#!/usr/bin/env bash
# Forced command of the deploy key (authorized_keys: command="...deploy-entry.sh",restrict).
# GitHub Actions connects with that key and sends exactly one command:  deploy <40-hex SHA>
# Anything else is refused. Spec: docs/proposals/ci-cd-auto-deploy.md.
#
# Steps:
#   1. validate SSH_ORIGINAL_COMMAND, fetch, check the SHA is in origin/master
#   2. remember the current APP_TAG, fast-forward the clone to exactly that SHA
#   3. update.sh --skip-pull (build, backup, migrations, restart, /health)
#   4. smoke.py against http://127.0.0.1:8000 (inside the new image: it has httpx)
#   5. on any failure after step 2: put the previous APP_TAG back, recreate app, notify, exit 1
# The database is never touched by the rollback: migrations must be backward compatible.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

REPO_DIR="${REPO_DIR:-$MARS_DIR/repo}"
BRANCH="master"
LOCAL_URL="http://127.0.0.1:8000"
HEALTH_TIMEOUT=120

fail() { echo "deploy-entry: $*" >&2; exit 2; }

# --- 1. the command ---------------------------------------------------------------------
cmd="${SSH_ORIGINAL_COMMAND:-}"
[[ "$cmd" != *$'\n'* && "$cmd" != *$'\r'* ]] || fail "refused: multi-line command"
[[ "$cmd" =~ ^deploy\ ([0-9a-f]{40})$ ]] || fail "refused: expected exactly 'deploy <40-hex sha>'"
sha="${BASH_REMATCH[1]}"

[[ -d "$REPO_DIR/.git" ]] || fail "missing git clone $REPO_DIR"
git -C "$REPO_DIR" fetch --quiet origin "$BRANCH"
git -C "$REPO_DIR" cat-file -e "$sha^{commit}" 2>/dev/null || fail "refused: unknown commit $sha"
git -C "$REPO_DIR" merge-base --is-ancestor "$sha" "origin/$BRANCH" \
  || fail "refused: $sha is not in origin/$BRANCH"

short="${sha:0:12}"
prev_tag="$(env_get APP_TAG)"
log "Deploy $short (previous image tag: ${prev_tag:-none})"
notify "deploy started: $short"

rollback() {
  local reason="$1"
  trap - ERR
  set +e
  if [[ -n "$prev_tag" ]] && docker image inspect "mars-rover-api:$prev_tag" >/dev/null 2>&1; then
    log "ROLLBACK to $prev_tag: $reason"
    sed -i "s/^APP_TAG=.*/APP_TAG=$prev_tag/" "$ENV_FILE"
    export APP_TAG="$prev_tag"
    mars_compose up -d app
    notify "rollback: $short -> $prev_tag, reason: $reason"
  else
    log "CANNOT ROLL BACK ($reason): previous image '${prev_tag:-none}' is not available"
    notify "deploy FAILED: $short, reason: $reason; no previous image to roll back to, check the server"
  fi
  exit 1
}

# --- 2. the exact commit ----------------------------------------------------------------
git -C "$REPO_DIR" checkout --quiet "$BRANCH"
git -C "$REPO_DIR" merge --ff-only "$sha" || fail "cannot fast-forward the clone to $sha"
[[ "$(git -C "$REPO_DIR" rev-parse HEAD)" == "$sha" ]] \
  || fail "the clone is ahead of $sha; refusing to deploy an older commit"

# --- 3. update.sh -----------------------------------------------------------------------
# update.sh sends its own "update FAILED" message; the rollback message follows it.
if ! "$SCRIPT_DIR/update.sh" --skip-pull; then
  rollback "update.sh failed (see the log of the job)"
fi

# --- 4. checks --------------------------------------------------------------------------
deadline=$((SECONDS + HEALTH_TIMEOUT))
until curl -fsS -m 5 -o /dev/null "$LOCAL_URL/health" 2>/dev/null; do
  (( SECONDS < deadline )) || rollback "/health did not answer within ${HEALTH_TIMEOUT}s"
  sleep 3
done

# smoke.py needs httpx, which the application image already has. Host network: the app is
# published on 127.0.0.1:8000 only.
if ! docker run --rm --network host --read-only \
  -v "$REPO_DIR/deploy/smoke.py:/smoke.py:ro" \
  "mars-rover-api:$short" python /smoke.py "$LOCAL_URL"; then
  rollback "smoke.py failed against $LOCAL_URL"
fi

log "DEPLOY OK: $short"
notify "deployed: $short"
