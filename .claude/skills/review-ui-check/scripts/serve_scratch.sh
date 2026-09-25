#!/usr/bin/env bash
# Build the review web app and start serve.py on a fresh, throwaway labels directory.
#   serve_scratch.sh [--sound] <scratch-dir> [port]      (default port 8799; 8765 is the user's own server)
# The app is muted (serve.py --mute) unless --sound is given: only for checking sound logic, which
# plays every revealed move over the user's speakers.
# Prints the base URL. Stop it with:  lsof -ti:<port> -sTCP:LISTEN | xargs -r kill
set -euo pipefail
mute=--mute
if [ "${1:-}" = "--sound" ]; then mute=; shift; fi
scratch=${1:?usage: serve_scratch.sh [--sound] <scratch-dir> [port]}
port=${2:-8799}
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)

# A scratch server from an earlier run with this same scratch dir is ours: restart it (new build, new
# labels). Anything else on the port is left alone.
for pid in $(lsof -ti:"$port" -sTCP:LISTEN 2>/dev/null); do
  args=$(ps -o args= -p "$pid" 2>/dev/null || true)
  if [[ "$args" == *review/server/serve.py* && "$args" == *"--labels $scratch/labels-"* ]]; then
    echo "restarting the earlier scratch server on port $port (pid $pid)" >&2
    kill "$pid"
    timeout 10 bash -c "while lsof -ti:$port -sTCP:LISTEN >/dev/null 2>&1; do sleep 0.2; done"
  fi
done
if lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $port is already in use (not by a scratch server for $scratch)." >&2
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
nohup python3 "$repo/review/server/serve.py" --port "$port" --labels "$labels" $mute >"$log" 2>&1 &
if ! timeout 30 bash -c "until curl -sf http://127.0.0.1:$port/api/games >/dev/null; do sleep 0.5; done"; then
  echo "server did not come up; see $log" >&2
  exit 1
fi
echo "base=http://127.0.0.1:$port labels=$labels log=$log sound=$([ -n "$mute" ] && echo off || echo on)"
