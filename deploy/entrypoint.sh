#!/usr/bin/env bash
# Start the policy service and the viewer in one container.
#
# The API binds loopback only: nothing outside the container can reach it
# directly, so every request arrives through the Next.js proxy on the single
# public port. If either process dies the container exits, because a viewer
# without a policy service would render an empty document rather than fail
# visibly, and that is a worse failure than a restart.
set -euo pipefail

PORT="${PORT:-24680}"
API_PORT="${API_PORT:-24681}"
STATE_DIR="$(dirname "${REDACTLY_DATABASE_PATH:-/home/node/state/redactly.sqlite3}")"
mkdir -p "$STATE_DIR"

terminate() {
  trap - TERM INT
  kill 0 2>/dev/null || true
}
trap terminate TERM INT

echo "starting policy service on 127.0.0.1:${API_PORT}"
(
  cd /app/services/api
  exec uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" --log-level info
) &

# The ONNX session load and the fixture scan both happen during startup, so the
# viewer must not accept traffic until the API answers.
for attempt in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    echo "policy service ready after ${attempt}s"
    break
  fi
  if [ "$attempt" -eq 120 ]; then
    echo "policy service did not become ready in 120s" >&2
    exit 1
  fi
  sleep 1
done

echo "starting viewer on 0.0.0.0:${PORT}"
(
  cd /app/web
  exec node server.js
) &

# Exit as soon as either process does, rather than lingering half-alive.
wait -n
echo "a process exited; shutting down" >&2
exit 1
