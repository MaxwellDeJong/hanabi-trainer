#!/usr/bin/env bash
# Start the review tool: set up (first time only), rebuild bundles and the web app, then serve.
#   bash review/run.sh [--port 8765]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f review/vendor/hanabi-game.mjs ] && [ -d review/node_modules ] || bash review/setup.sh
shopt -s nullglob  # data/exports/ is not committed and may be empty
python3 review/server/bundle.py prototype/examples/export_*.json data/exports/export_*.json \
  || echo "!! some checks failed (see the Checks button)"
npm --prefix review run -s build >/dev/null
exec python3 review/server/serve.py "$@"
