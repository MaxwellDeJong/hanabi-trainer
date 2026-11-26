#!/usr/bin/env bash
# Start the review tool: set up (first time only), rebuild bundles and the web app, then serve.
#   bash review/run.sh [--port 8765]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f review/vendor/hanabi-game.mjs ] && [ -d review/node_modules ] \
  && [ -f review/vendor/hanabi-live/public/sounds/turn-us.mp3 ] || bash review/setup.sh
shopt -s nullglob  # data/exports/ is not committed and may be empty
# With review/pool.txt (one game ID per line, '#' comments), bundle only those games: the full download is
# ~14k exports, far too many to bundle (while-waiting.md B3). Without it, bundle every export as before.
if [ -f review/pool.txt ]; then
  games=()
  for id in $(sed 's/#.*//' review/pool.txt); do
    for f in "examples/export_$id.json" "data/exports/export_$id.json"; do
      [ -f "$f" ] && { games+=("$f"); break; }
    done
  done
else
  games=(examples/export_*.json data/exports/export_*.json)
fi
uv run python review/server/bundle.py "${games[@]}" \
  || echo "!! some checks failed (see the Checks button)"
npm --prefix review run -s build >/dev/null
exec uv run python review/server/serve.py "$@"
