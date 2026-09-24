#!/bin/sh
# gbrain container for Märkmik. `serve --http` is the single PGLite owner; a loop of one-shot
# `gbrain sync` runs delegates to it over its IPC socket (`sync --watch` is rejected while serve
# owns the DB). The app is the only process that pulls, commits and pushes; /brain is read-only.
set -eu
: "${GBRAIN_HOME:=/data}" "${BRAIN_DIR:=/brain}" "${GBRAIN_PORT:=8083}" "${GBRAIN_SYNC_INTERVAL:=60}"
export GBRAIN_HOME
# Read-only token for the app. /data here is ./data/gbrain on the host, which the app sees as
# <DATA_DIR>/gbrain/app_token and reads on use (app/gbrain.py). To rotate: stop gbrain,
# `gbrain auth revoke markmik-app`, delete the file, start gbrain.
TOKEN_FILE="$GBRAIN_HOME/app_token"

# As PID 1 the shell gets no default signal handling: stop cleanly while waiting too.
trap 'exit 0' TERM INT
# The app clones the brain once the owner connects the repo (Seaded → Ühendused).
until [ -d "$BRAIN_DIR/.git" ]; do
  echo "gbrain: waiting for the app's clone in $BRAIN_DIR"
  sleep 30 & wait $!
done

# init / config / auth need the DB closed, so they run before serve starts.
if [ ! -f "$GBRAIN_HOME/.gbrain/config.json" ]; then
  gbrain init --pglite --no-embedding --non-interactive --db-only
  gbrain sources set-path default "$BRAIN_DIR"
  # Note files link as bare [[slug|Title]]; they resolve by unique basename only with this on.
  gbrain config set link_resolution.global_basename true
fi
if [ ! -s "$TOKEN_FILE" ]; then
  (umask 077; gbrain auth create markmik-app --scopes read \
    | grep -oE 'gbrain_[A-Za-z0-9_]{20,}' | head -n1 > "$TOKEN_FILE")
fi
gbrain sync --no-pull --no-embed || echo "gbrain: initial sync failed; the loop retries"
# the first (full-import) sync leaves links unextracted
gbrain extract --stale || true

gbrain serve --http --bind 0.0.0.0 --port "$GBRAIN_PORT" &
SERVE=$!
trap 'kill -TERM "$SERVE"; wait "$SERVE"; exit 0' TERM INT
while kill -0 "$SERVE" 2>/dev/null; do
  sleep "$GBRAIN_SYNC_INTERVAL" & wait $!
  # Fails with write_pending while serve is busy; the cursor is durable, so the next tick catches up.
  gbrain sync --no-pull --no-embed >/dev/null 2>&1 || echo "gbrain: sync deferred"
done
