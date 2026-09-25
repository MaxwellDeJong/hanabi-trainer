#!/usr/bin/env bash
# One-time setup for the review tool:
#  1. fetch hanab.live at a pinned commit (sparse: the game package, the card-drawing code, UI images, sounds)
#  2. install npm dependencies
#  3. bundle the game package for the oracle (vendor/hanabi-game.mjs)
# The npm release (@hanabi-live/game 0.0.8) is older than the repo, so we build from source.
set -euo pipefail
cd "$(dirname "$0")"
COMMIT=c1d970bd610395087958fe7d1bb96f52244f7b04   # 2026-09-18, the commit docs/representation.md §4 cites
SRC=vendor/hanabi-live
UI=packages/client/src/game/ui
PATHS=(
  LICENSE
  packages/game
  $UI/drawCards.ts $UI/drawCardsBrowser.ts $UI/drawPip.ts $UI/drawPipFunctions.ts
  $UI/drawStylizedRank.ts $UI/cardRendererBackend.ts $UI/constants.ts
  public/img/background.jpg public/img/trashcan.png public/img/x.png
  public/img/replay-back-full.png public/img/replay-back.png
  public/img/replay-forward.png public/img/replay-forward-full.png
  public/img/replay-back-full-disabled.png public/img/replay-back-disabled.png
  public/img/replay-forward-disabled.png public/img/replay-forward-full-disabled.png
  # The sounds Label mode plays (review/server/bundle.py sound_effects)
  public/sounds/turn-us.mp3 public/sounds/turn-other.mp3 public/sounds/turn-sad.mp3
  public/sounds/turn-fail1.mp3 public/sounds/turn-fail2.mp3
  public/sounds/turn-blind1.mp3 public/sounds/turn-blind2.mp3 public/sounds/turn-blind3.mp3
  public/sounds/turn-blind4.mp3 public/sounds/turn-blind5.mp3 public/sounds/turn-blind6.mp3
  public/sounds/finished-success.mp3 public/sounds/finished-fail.mp3 public/sounds/finished-perfect.mp3
)

if [ ! -d "$SRC/.git" ] || [ "$(git -C "$SRC" rev-parse HEAD)" != "$COMMIT" ] \
   || [ "$(git -C "$SRC" sparse-checkout list | sort)" != "$(printf '%s\n' "${PATHS[@]}" | sort)" ]; then
  rm -rf "$SRC"
  git init -q "$SRC"
  git -C "$SRC" remote add origin https://github.com/Hanabi-Live/hanabi-live.git
  git -C "$SRC" sparse-checkout set --no-cone "${PATHS[@]}"
  git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
  git -C "$SRC" checkout -q FETCH_HEAD
fi

npm install --no-audit --no-fund --silent
npx esbuild "$SRC/packages/game/src/index.ts" --bundle --format=esm --platform=node \
  --outfile=vendor/hanabi-game.mjs --tsconfig-raw={} --log-level=warning
echo "review tool set up with hanabi-live@${COMMIT:0:7}"
