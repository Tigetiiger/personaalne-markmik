#!/bin/sh
# gbrain container: `serve --http` is the single PGLite owner; a loop of
# one-shot `gbrain sync` runs delegates to it over its IPC socket
# (`sync --watch` is rejected while serve owns the DB).
set -eu
: "${GBRAIN_HOME:=/data}" "${BRAIN_DIR:=/brain}" "${GBRAIN_PORT:=8083}" "${GBRAIN_SYNC_INTERVAL:=60}"
export GBRAIN_HOME
TOKEN_FILE="$GBRAIN_HOME/app_token"

# init / config / auth need the DB closed, so they run before serve starts.
if [ ! -f "$GBRAIN_HOME/.gbrain/config.json" ]; then
  gbrain init --pglite --no-embedding --non-interactive --db-only
  gbrain sources set-path default "$BRAIN_DIR"
  # bare [[slug]] wikilinks resolve by unique basename (off by default)
  gbrain config set link_resolution.global_basename true
fi
if [ ! -s "$TOKEN_FILE" ]; then
  (umask 077; gbrain auth create markmik-app --scopes read \
    | grep -oE 'gbrain_[A-Za-z0-9_]{20,}' | head -n1 > "$TOKEN_FILE")
fi
gbrain sync --no-pull --no-embed || echo "gbrain: initial sync failed; loop retries"
# the first (full-import) sync leaves links unextracted
gbrain extract --stale || true

gbrain serve --http --bind 0.0.0.0 --port "$GBRAIN_PORT" &
SERVE=$!
trap 'kill -TERM "$SERVE"; wait "$SERVE"; exit 0' TERM INT
while kill -0 "$SERVE" 2>/dev/null; do
  sleep "$GBRAIN_SYNC_INTERVAL" & wait $!
  # fails with write_pending while serve is busy; the cursor is durable, so
  # the next tick catches up
  gbrain sync --no-pull --no-embed >/dev/null 2>&1 || echo "gbrain: sync deferred"
done
