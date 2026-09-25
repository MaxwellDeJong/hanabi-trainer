#!/usr/bin/env bash
# Bring the review tool online for sharing: build + serve it locally, open a Cloudflare
# quick tunnel, and print the public URL. Ctrl-C stops both.
#
#   bash review/share.sh [--port 8765] [--labels DIR]
#
# The quick tunnel is anonymous and ephemeral (a fresh URL each run). It runs with
# --config /dev/null so it ignores ~/.cloudflared/config.yml: on a host that also runs a
# named tunnel (e.g. a production one), that config's ingress rules would otherwise send
# every request to its own 404 and the tunnel would never reach this server. Nothing here
# touches an already-running named tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=8765
RUN_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --port)   PORT="$2"; RUN_ARGS+=(--port "$2"); shift 2 ;;
    --labels) RUN_ARGS+=(--labels "$2"); shift 2 ;;
    *) echo "unknown option: $1 (use --port and --labels)" >&2; exit 2 ;;
  esac
done

command -v cloudflared >/dev/null || { echo "cloudflared is not installed" >&2; exit 1; }

TUNNEL_LOG="$(mktemp)"
SERVER_PID=""
TUNNEL_PID=""
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "Shutting down the tunnel and server…"
  [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
  rm -f "$TUNNEL_LOG"
}
trap cleanup INT TERM EXIT

# 1. Build (the first run also fetches hanab.live and installs npm packages) and serve, in
#    the background. run.sh execs serve.py, so $SERVER_PID becomes the server after the build.
echo "Building and starting the review server on 127.0.0.1:$PORT …"
bash review/run.sh "${RUN_ARGS[@]}" &
SERVER_PID=$!

# 2. Wait for it to accept connections (the build can take a few seconds).
up=""
for _ in $(seq 1 90); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "The server exited before it came up (see the build output above)." >&2
    exit 1
  fi
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/"; then up=1; break; fi
  sleep 1
done
[ -n "$up" ] || { echo "The server did not come up on port $PORT within 90s." >&2; exit 1; }
echo "Server is up."

# 3. Open the quick tunnel, ignoring any named-tunnel config in ~/.cloudflared.
echo "Opening the Cloudflare quick tunnel …"
cloudflared tunnel --config /dev/null --url "http://127.0.0.1:$PORT" > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

# 4. Wait for the public URL and a registered connection.
URL=""
for _ in $(seq 1 30); do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "cloudflared exited. Its output:" >&2; cat "$TUNNEL_LOG" >&2; exit 1
  fi
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
  if [ -n "$URL" ] && grep -q "Registered tunnel connection" "$TUNNEL_LOG"; then break; fi
  sleep 1
done
[ -n "$URL" ] || { echo "Could not get a tunnel URL. cloudflared output:" >&2; cat "$TUNNEL_LOG" >&2; exit 1; }

# 5. Show it.
echo
echo "==================================================================="
echo "  Review tool is online. Share this URL:"
echo
echo "      $URL"
echo
echo "  Ctrl-C here stops the server and the tunnel."
echo "==================================================================="
echo

# 6. Stay up until Ctrl-C, or until either process exits (then cleanup brings the other down).
wait -n "$SERVER_PID" "$TUNNEL_PID" || true
