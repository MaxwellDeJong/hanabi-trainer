---
name: review-ui-check
description: Launch the review/labeling tool (review/) on a throwaway server and validate a change in the real UI by driving headless Chrome — Label mode clicks and keys, screenshots, DOM and console checks. Use this whenever a change under review/web or review/server should be seen working in the browser, when asked to run, screenshot, smoke-test or "check it in the app", or before reporting a UI change to the review tool as done, even if the user doesn't say "browser".
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
   `review/web/src`, since the server only serves what was last built, and before re-running a check
   that claims seats. Given the same scratch dir and port, it restarts its own earlier server (new
   build, fresh labels). It refuses a port anything else holds. **Check that it printed `base=…`**:
   don't pipe it through `tail -1` or `>/dev/null`, which can hide a refusal and leave you testing
   an old build.
   **The app is muted by default**: the script passes `serve.py --mute`, which marks the page
   `<html data-mute>`, and `web/src/sounds.ts` then plays nothing. Otherwise every revealed move
   would play over the user's speakers. Pass `--sound` first
   (`serve_scratch.sh --sound <scratch> <port>`) only when checking sound logic (see Pitfalls).
3. **Write a check script** in the scratchpad. Start from `scripts/example_mismatch.py`, which covers
   session setup, choosing a move from the recorded actions, clicks, keys, animation timing and
   assertions. Keep its import block: it finds `cdp.py` from the repo when the script isn't in
   `scripts/`. Write it with the Write tool, then run it from the repo root:
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
  returns a plain list of rows with `session_id`, newest first. `new_session` uses the labeler
  `ui-check`.
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
  in-memory state (the undo/replay position and the paused state) but not `localStorage`.
- **Raw `Page.navigate` to a URL that only changes the hash is unreliable.** Switching from one label
  session to another, Chrome sometimes left the hash unchanged, and the reload that followed reopened
  the old session (the symptom: "expected turn 2, at 5"). `b.nav()` now sets `location.hash` from the
  page, waits for it and then reloads. It raises if the page lands on another route. Use `b.nav()`
  rather than calling `Page.navigate` yourself.
- `localStorage` keys: `review.labeller` (lobby name), `review.advance` (Manual/Auto turn advance,
  JSON `{auto, seconds}`). A fresh profile starts with Manual advance and no labeler name. To test
  Auto, set it before the app loads (see below) with `seconds: 1`:
  `localStorage.setItem('review.advance', JSON.stringify({auto: true, seconds: 1}))`.
- **Testing auto-advance timing:** poll the turn (`int(b.js("location.hash").rsplit("/", 1)[1])` in
  Label mode) with a deadline, rather than fixed sleeps, which are flaky. To show that the game
  *stood still*, sleep longer than `seconds` (by about 1 s) and check the turn didn't move.
- **Tooltips** come from a `data-tip` attribute: read it with
  `b.js("document.querySelector(sel).dataset.tip")`, and to screenshot one, `b.hover(sel)`, wait
  about 0.3 s, then `b.shot(...)`. Call `b.unhover()` afterwards so the tooltip doesn't cover later
  screenshots.
- **Look at new text in the screenshot, not just the DOM.** A "⏸" glyph passed the DOM check but
  rendered as a thin sliver in the stage font. Symbols and emoji can render badly there.
- **Running code before the app loads** (spies, stubs): `b.nav()` reloads the page, so patches made
  with `b.js()` beforehand are lost. Register them with the raw protocol call before navigating:
  `b.cdp("Page.addScriptToEvaluateOnNewDocument", source="...")`. They then run before the app on
  every load.

**Sounds**
- A screenshot can't show a sound. To check which moves play one, count calls to `play()`:
  ```python
  b.cdp("Page.addScriptToEvaluateOnNewDocument", source="""
      window.__plays = [];
      const orig = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function () { window.__plays.push(this.src); return orig.call(this); };
  """)
  b.nav(...); b.key(" "); print(b.js("window.__plays"))
  ```
- **A muted server always gives an empty list.** Checking sound logic (changes to `sounds.ts`, the
  sound calls in `label.ts`, or `bundle.py` `sound_effects`) needs `serve_scratch.sh --sound`, which
  plays over the user's speakers, so keep those runs short and only use them when needed. The server
  prints `sound=off` or `sound=on`, and `b.js("document.documentElement.hasAttribute('data-mute')")`
  tells you from the page.
- When not muted, the app plays a sound on load at turn 0 and one for each revealed move. Moving back
  (←, Home, a click on the progress bar) is silent and pauses the game. Space / → after that resumes
  it, so each step forward plays its sound again.

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

**Replay area** (under the stacks): `button:has(> img.button-icon)`, with index 0 = rewind to the start,
1 = one turn back, 2 = one turn forward (in Label mode it acts like →), 3 = to the newest turn. A disabled
button is a real `disabled` button.

**Label-mode play state:** `.paused-text` ("Paused: you went back", with a tooltip) appears next to
the Auto-advance chip while Auto is on and the labeler has gone back. Going back pauses the game;
Space / → resumes it.

**Games to use:** `GET /api/games` lists the bundles. Game 78921 (2 players, 6 suits) is short and
opens with a clue then a play, so it's handy for Label-mode checks. Sitting at `startingPlayer`, your
turns are the odd ones (1, 3, 5…). Sitting at the other seat, they're the even ones. One labeler can
hold both seats, so one check can create a session for each.
Setting up an ongoing session: playing your slot 1 is always legal, so `b.click_card(seat, 0)` at each
of your turns gets you ahead quickly. Whether it matches the recorded move doesn't matter.

**`cdp.Browser` methods:** `nav(url)`, `click_card(holder, index, button, shift)`,
`click_selector(sel, index)`, `hover(sel, index)`, `unhover()`, `key(k)`, `js(expr)`, `wait_for(expr)`,
`text(sel)`, `shot(name)`, `errors`,
and `cdp(method, **params)`: any raw DevTools protocol call (e.g. `Page.addScriptToEvaluateOnNewDocument`),
returning its result.
