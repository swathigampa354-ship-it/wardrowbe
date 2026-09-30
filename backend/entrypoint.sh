#!/bin/sh
# One container, two processes: the Next.js UI (public port) and FastAPI
# (loopback only, reached through the UI's /api/v1 proxy).
#
# There is no supervisor here on purpose. If either process exits, this script
# exits too, so the platform restarts the container instead of serving a half
# dead app - the failure mode the queue-based original hid behind three
# long-lived workers.
set -e

: "${FRONTEND_PORT:=10000}"
: "${BACKEND_PORT:=8000}"
: "${STORAGE_DIR:=/data/wardrobe}"
: "${API_WORKERS:=1}"

# Render gives the free tier no writable $HOME and no persistent volume, so the
# SQLite file and the photos live on the (ephemeral) container disk by default.
mkdir -p "$STORAGE_DIR" 2>/dev/null || {
  echo "[entrypoint] STORAGE_DIR=$STORAGE_DIR is not writable; falling back to /tmp/wardrowbe"
  export STORAGE_DIR=/tmp/wardrowbe
  mkdir -p "$STORAGE_DIR"
}

# The UI proxies /api/v1 to BACKEND_URL; keep the two in step automatically.
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:$BACKEND_PORT}"

UI_DIR="${FRONTEND_DIR:-/app/frontend}"
if [ -f "$UI_DIR/server.js" ]; then
  echo "[entrypoint] UI :$FRONTEND_PORT  ->  API :$BACKEND_PORT ($BACKEND_URL)"
  HOSTNAME=0.0.0.0 PORT="$FRONTEND_PORT" node "$UI_DIR/server.js" &
  UI_PID=$!
else
  echo "[entrypoint] no built UI at $UI_DIR/server.js - API only on :$BACKEND_PORT"
  UI_PID=""
fi

python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --workers "$API_WORKERS" &
API_PID=$!

shutdown() {
  [ -n "$UI_PID" ] && kill "$UI_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap shutdown TERM INT

while :; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "[entrypoint] API exited; stopping"
    shutdown; exit 1
  fi
  if [ -n "$UI_PID" ] && ! kill -0 "$UI_PID" 2>/dev/null; then
    echo "[entrypoint] UI exited; stopping"
    shutdown; exit 1
  fi
  sleep 2
done
