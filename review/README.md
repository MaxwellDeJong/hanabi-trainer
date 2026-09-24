# Review tool

Design: `docs/review-tool.md`. Progress: `docs/progress.md`.

```bash
bash review/run.sh          # first run also fetches hanab.live's source and installs npm packages
# then open http://127.0.0.1:8765/
```

`run.sh` rebuilds the bundles for `prototype/examples/export_*.json`, builds the web app and starts the
server. Hover a card to highlight the clue log (white = touched, red = missed).

**Label mode** (lobby → "Label": enter your name, start or resume a session). You get one seat of a random
game, players anonymised. Left click: play your card / colour clue on a teammate's card. Right click:
discard / rank clue. Your move applies at once and the game goes on. Shift + click before the move: also
OK. → reveals other players' moves. Space: hint (the move actually made). Backspace: undo your latest move.
Labels are written to `review/labels/` (`serve.py --labels DIR` to change).

**Inspect mode** keys: ← → one turn, `[` `]` one round, Home/End, `O` show/hide the top row's cards, `J`
JSON, Esc closes overlays.

| Path | What it is |
|---|---|
| `setup.sh` | One-time: sparse checkout of hanab.live at `c1d970b` into `vendor/`, `npm install`, bundle the game package for the oracle |
| `server/bundle.py` | Builds a game bundle with the engine (`hanabi_data/`) and checks it against the oracle. Exit status is non-zero on any check error |
| `server/serve.py` | Local server: `/api/games`, `/api/games/<id>/inspect`, the Label session API and the built web app |
| `server/labels.py` | Label mode: sessions, label events, and the redacted view the browser gets |
| `labels/` | Label data: `sessions/<id>.json` and `<game_id>.jsonl` events. Not generated: keep it |
| `oracle/` | hanab.live's own reducer, run from Node |
| `web/` | The viewer (TypeScript + Vite). Uses hanab.live's card-drawing code and images unmodified from `vendor/` |
| `build/`, `vendor/`, `node_modules/`, `web/dist/` | Generated, not committed |

Development: `npm --prefix review run dev` (Vite on :5173, proxying `/api` to `serve.py` on :8765) and
`npm --prefix review run typecheck`.

Everything under `review/` that uses hanab.live code (`oracle/`, `web/`) is GPL-3.0, like hanab.live.
