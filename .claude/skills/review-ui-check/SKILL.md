---
name: review-ui-check
description: Launch the review/labelling tool (review/) on a throwaway server and validate a change in the real UI by driving headless Chrome — Label mode clicks and keys, screenshots, DOM and console checks. Use this whenever a change under review/web or review/server should be seen working in the browser, when asked to run, screenshot, smoke-test or "check it in the app", or before reporting a UI change to the review tool as done, even if the user doesn't say "browser".
---

# Checking review-tool changes in the browser

The review tool is a Python server (`review/server/serve.py`) serving a built TypeScript app
(`review/web/dist`). To see a change working: build, start a **throwaway** server, drive headless
Chrome over the DevTools protocol with `scripts/cdp.py`, and **look at the screenshots**.

There is no Playwright or `chromium-cli` here. `google-chrome` and Python's `websocket-client` are
available, and `scripts/cdp.py` is a small driver built on them.

## Steps

1. **Typecheck:** `npm --prefix review run typecheck`.
2. **Start a scratch server.** It builds the web app and uses a fresh labels directory:
   ```bash
   bash .claude/skills/review-ui-check/scripts/serve_scratch.sh <scratchpad>/uicheck 8799
   # -> base=http://127.0.0.1:8799 labels=... log=...
   ```
   Use your session scratchpad, not `/tmp` or the repo. Run it again after every edit under
   `review/web/src`, since the server only serves what was last built.
3. **Write a check script** in the scratchpad. Start from `scripts/example_mismatch.py`, which covers
   session setup, choosing a move from the recorded actions, clicks, keys, animation timing and
   assertions. Write it with the Write tool, then run it:
   `python3 <scratchpad>/uicheck/check.py http://127.0.0.1:8799 <scratchpad>/uicheck`.
4. **Read the screenshots** in `<scratch>/shots/` with the Read tool. A blank or "Loading…" frame
   means the check failed, even if the script printed "ok". Also check `b.errors`, which collects
   uncaught exceptions and `console.error` calls.
5. **Clean up:** `lsof -ti:8799 -sTCP:LISTEN | xargs -r kill`. Then `git status` should show only
   the files you meant to change. Nothing new should appear under `review/labels/`.

When reporting back, say what you drove, what the screenshots showed and whether there were console
errors. For an animation, say that a still screenshot only shows one moment of it.

## Pitfalls (all hit in practice)

**Data safety**
- **Never run the server on `review/labels/`.** It holds real, non-regenerable label data, and it's
  `serve.py`'s default, so a bare `serve.py` or `run.sh` would write test sessions into it. Always
  pass `--labels <scratch dir>`, which `serve_scratch.sh` does for you.
- **Don't use port 8765 or kill whatever is on it.** That's the user's own server (`run.sh`). If your
  port is taken, look at what holds it (`lsof -i:<port> -sTCP:LISTEN`) before killing anything.

**Processes**
- **Stop processes by port, not by `pkill`.** `pkill -f <pattern>` matches full command lines, which
  can include your own shell (whose command contains the pattern), killing your session. `pkill chrome`
  or `pkill -f chrome` also kills the **user's desktop Chrome**. Use
  `lsof -ti:<port> -sTCP:LISTEN | xargs -r kill`, or specific PIDs from `pgrep -a chrome` after
  reading the list.
- **Stale headless Chrome.** A killed or crashed run can leave Chrome holding its debugging port. A
  new run on the same port then talks to the old browser (still with its old flags, which caused a
  confusing 403 that the new flags seemed not to fix). `cdp.Browser` avoids this: a free port per
  run, its own process group killed on exit, and a throwaway profile. If you drive Chrome by hand,
  do the same.
- **Always pass `--user-data-dir`.** Without it, `google-chrome` can hand the URL to the user's running
  browser and exit at once.
- **CDP WebSocket 403 Forbidden.** Chrome checks the Origin header. Launch with
  `--remote-allow-origins=*` and connect with `suppress_origin=True` (both done in `cdp.py`).

**Server and API**
- **A seat can be claimed only once** ("someone else has already taken this seat", 409), even by you
  in an earlier run. Use a fresh labels directory (`serve_scratch.sh` makes one each time) or a
  different game or seat.
- **The session id is `view["session"]["session_id"]`**, not `view["id"]`. `GET /api/sessions?labeller=<name>`
  lists sessions with `session_id`.
- **The store writes lazily**: there's no session file until the first label. An empty labels
  directory right after creating a session is expected.
- `run.sh` also rebuilds every game bundle, which is slow and not needed for web-only changes.
  `serve_scratch.sh` only builds the web app.

**Shell**
- **A heredoc chained after `&&` doesn't run if an earlier command fails.** The file just never gets
  written, and the next error ("can't open file") hides the real cause. Write scripts with the
  Write tool, then run them.
- Node 20 has no global `WebSocket`; use the Python driver.

**Driving the UI**
- Cards react to **mousedown**, with the button meaning something: on your own hand, left = play and
  right = discard; on a teammate's hand, left = colour clue and right = rank clue; Shift = "also OK".
  `el.click()` from JS does nothing useful. Use `b.click_card(holder, index, button=...)`, which
  sends real mouse events.
- Game keys go through `document` keydown: Space/→ reveal the next move, ← back, Tab hint,
  Backspace undo, `[` `]` one round, Home/End. Use `b.key(" ")`.
- **Time screenshots around animations.** Card moves take 400 ms (wait ≥ 0.5 s before judging layout).
  The mismatch pulse lasts 1.6 s and peaks at about 0.3 s. The app redraws the whole stage often, so
  query the DOM rather than holding element handles.
- **Hash-only navigation doesn't reload the app.** `b.nav()` reloads for you, which clears the app's
  in-memory state (the undo/replay position) but not `localStorage`.
- `localStorage` keys: `review.labeller` (lobby name), `review.advance` (Manual/Auto turn advance).
  A fresh profile starts with Manual advance and no labeller name.

## Reference

**Routes:** `#/` lobby · `#/label/<sid>/<turn>` Label mode (turn 1-based) · `#/admin` ·
`#/game/<id>/<turn>?pov=<seat>&own=hidden` Inspect mode.

**API** (`serve.py` docstring has the full list): `GET /api/games` · `GET /api/games/<id>/inspect`
(full bundle, including `game.export.actions`) · `POST /api/sessions {labeller, game_id?, seat?}` ·
`GET /api/sessions/<sid>` · `POST /api/sessions/<sid>/{advance,labels,retract,hint,submit}`.

**Recorded actions** (`cdp.recorded_actions`): action *i* is UI turn *i + 1*, made by seat
`(i + startingPlayer) % n`. `type` 0 play and 1 discard have `target` = card id; 2 colour clue has
`target` = seat and `value` = suit index; 3 rank clue has `target` = seat and `value` = rank; 4 game over.

**DOM hooks:** hand cards are `img[data-holder=<seat>][data-card=<id>]`, left to right = slot 1…n.
Label-mode tags are `.mark.chosen`, `.mark.alt` and `.mark.ghost`. Also useful: `.mismatch-pulse`,
`.turn-box.yours`, `.action-log`, `.controls .status`, and `.message-line` (flash messages).

**Games to use:** `GET /api/games` lists the bundles. Game 78921 (2 players, 6 suits) is short and
opens with a clue then a play, so it's handy for Label-mode checks.

**`cdp.Browser` methods:** `nav(url)`, `click_card(holder, index, button, shift)`,
`click_selector(sel, index)`, `key(k)`, `js(expr)`, `wait_for(expr)`, `text(sel)`, `shot(name)`, `errors`.
