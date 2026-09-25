#!/usr/bin/env bash
# Build the review web app and start serve.py on a fresh, throwaway labels directory.
#   serve_scratch.sh <scratch-dir> [port]      (default port 8799; 8765 is the user's own server)
# Prints the base URL. Stop it with:  lsof -ti:<port> -sTCP:LISTEN | xargs -r kill
set -euo pipefail
scratch=${1:?usage: serve_scratch.sh <scratch-dir> [port]}
port=${2:-8799}
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)

if lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $port is already in use (a server from an earlier run, or the user's own)." >&2
  echo "Look before killing:  lsof -i:$port -sTCP:LISTEN" >&2
  exit 1
fi

# A new labels directory every run: seats claimed in an earlier run can't be claimed again, and
# review/labels/ holds real label data that must never get test sessions.
labels="$scratch/labels-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$labels"

# serve.py serves review/web/dist, so edits under web/src only show up after a build.
npm --prefix "$repo/review" run -s build >/dev/null

log="$scratch/serve-$port.log"
nohup python3 "$repo/review/server/serve.py" --port "$port" --labels "$labels" >"$log" 2>&1 &
if ! timeout 30 bash -c "until curl -sf http://127.0.0.1:$port/api/games >/dev/null; do sleep 0.5; done"; then
  echo "server did not come up; see $log" >&2
  exit 1
fi
echo "base=http://127.0.0.1:$port labels=$labels log=$log"
