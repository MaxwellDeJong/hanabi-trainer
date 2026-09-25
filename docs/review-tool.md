# Review tool: design v0 (draft)

*Status: phases 1–3 and 4a built (§10); last updated 2026-09-25. Progress: `progress.md`*

A browser tool for reviewing recorded games. It has two jobs:

1. **Inspect**: check that we parse and replay the JSON correctly, turn by turn.
2. **Label**: let a reviewer sit in one player's seat and choose the best move at each of that player's
   turns, using the same screen and controls as a live game on new.playhanabi.com.

It runs locally first, but is built so that it can later become a self-hosted site for crowdsourced labelling.
How labels are stored is deliberately left open (§8).

---

## 1. Decisions so far

| Topic | Decision |
|---|---|
| Deployment | Local tool first. Keep an HTTP API between the browser and the server from day one, so it can later be hosted for crowdsourcing |
| Look and feel | Copy a **live game on the site** (layout, card art, log wording, keys). Labellers know it well, which keeps labelling time low |
| Controls | **Speedrun controls**: left-click plays your own card or gives a colour clue to a teammate's card; right-click discards your own card or gives a rank clue to a teammate's card |
| Unit of labelling | **One seat of one game per session.** The labeller sees only that seat's view for the whole game, which preserves hidden information |
| Actual move | Hidden by default. A **hint toggle** (Tab) shows it when the labeller is stuck, and the label records that the hint was used |
| Knowledge display | **Clue information only** (our `know`). The group plays with the site's pip-updating setting off and instead hovers a card to highlight the clue log entries that concern it (§4.2). No deduction from visible cards |
| Player names | **Anonymised** in Label mode (§4.3) |
| Advancing | **Your move advances the game at once**, as in a live game (no confirmation). Space (or →) for each other player's turn, or **auto-advance** every so many seconds (a per-labeller setting, §4.1). Going back is always possible |
| Extras | None: no skip, no throw-away mark, no notes, no on-screen control hints (labellers know the controls). Decided 2026-09-24 |
| Which games | **Random sample** of (game, seat) pairs that nobody has claimed yet, or a seat picked from the lobby's board (searchable by game ID). Prioritising particular games may come later |
| Several labellers | **One labeller per seat.** A seat with a session (in progress or submitted) is claimed and isn't handed out again. A labeller may take several seats of the same game (decided 2026-09-25). The lobby's board shows every game (by ID) and seat; an admin view shows coverage and contributions (§4.4). Decided 2026-09-25 |
| Finishing | A session is **active** until the labeller **submits** it at the end of the game, with a move at every one of their turns. A submitted session is read-only (§4.1). A session not submitted within **24 hours** of its start expires: its moves are dropped and the seat is open again (§4.4) |
| License | Borrowing from hanab.live (GPL-3.0) is fine. The borrowed code stays in its own directory (§7) so the licence doesn't extend to the engine or dataset code |
| Game logic | The browser **does not compute game state**. It displays what the Python engine produced (§6), so inspecting the tool inspects the real training records |

---

## 2. What we take from hanab.live

Source: `Hanabi-Live/hanabi-live`, commit `c1d970b` (2026-09-18), the same commit `representation.md` §4 cites.

| Feature | Where in the source | Use |
|---|---|---|
| Speedrun click handling | `packages/client/src/game/ui/HanabiCardClickSpeedrun.ts` | Copy its behaviour (§4.2). Discarding at 8 clues is refused and the clue counter flashes. Clues need ≥1 clue token |
| Default "row" layout | `client/src/game/ui/drawHands.ts:110–172` (the non-Keldon, "Board Game Arena" layout) | Hands are stacked in rows. **Our own hand is the top row**, and the others follow in turn order below it (`j = (i − ourPlayerIndex) mod n`). Screenshots 1–3 use this layout |
| Card faces and pips | `client/src/game/ui/drawCards.ts`, `drawPip.ts`, `drawPipFunctions.ts` (plain canvas-2D drawing, ~4k lines) | Imported unmodified from `vendor/` to draw card images once; the images are then used in a DOM layout |
| Clue arrows | `client/src/game/ui/arrows.ts` | Style for the hint overlay and the clue highlights (screenshot 2) |
| Clue log hover | `client/src/game/ui/ClueEntry.ts:128`, `HanabiCardMouse.ts:44` | Hovering a card highlights clue log entries that **touched** it (white) and entries that **missed** it (red `#ff7777`). Clicking an entry jumps to that turn |
| Log wording | `packages/game/src/rules/text.ts` (`getClueText`, `getPlayText`, `getDiscardText`, …) | Log lines word-for-word as on the site ("… tells X about two 1s", "… plays Yellow 1 from slot #5") |
| Replay keys | `docs/features.md` "Keyboard Shortcuts" | ← → one turn, `[` `]` one full round, Home/End |
| Replay URL | `client/src/game/ui/gameCommands.ts:271` | `https://new.playhanabi.com/replay/<id>#<turn>` for "open on site" (still to confirm on that server) |
| Official rules engine | `packages/game` (GPL-3.0): `gameReducer`, built from the repo at `c1d970b` (npm v0.0.8 is stale) | **An independent reference to check against** (§6.2) |

---

## 3. Modes

| | **Label** | **Inspect** |
|---|---|---|
| Who | Labellers | Us, while building the engine. Reached from the admin view (§4.4) |
| View | One seat's view only; that seat's own cards face-down | All hands face-up (like spectating), with a switch to any seat's view |
| Navigation | Forward only as far as the current turn. Going back to earlier turns works like the in-game replay | Any turn |
| Extras | Hint toggle, label input, auto-advance, sounds | Checks panel (incl. differences from the official engine), DecisionRecord / seat view / raw action JSON, "open on site" link |
| Data sent to the browser | Only what that seat can know (§6.1) | Everything |

---

## 4. Label mode

### 4.1 Session flow

A session is `(game, seat, labeller)`. It starts at turn 1 and runs to the end of the game.

```
other player's turn ─(Space)─► the real move is applied and logged ─┐
        ▲                                                         │
        │                                                         ▼
        └── the real move is applied ◄── labeller clicks a move ◄── labeller's turn
            at once (shown in the log;                              (Tab shows the actual move)
            "you: X" if different)
```

- **The real game always continues.** The labeller's choice is recorded, but the next state comes from the
  export. There are no hypotheticals. When the choice differs from the real move, the log shows the real
  move with a small "you: …" marker (✓ when they match, ≈✓ when the real move was one of the "equally
  good" ones), so it's clear what happened. The position after it also shows a dashed amber "You: …" tag
  on the cards your move was about, and when the game steps onto it the hand concerned (yours, or the
  clued player's) pulses red once (1.6 s). Decided 2026-09-25
- **A click is the move.** As in a live game, clicking a move records it and the game goes on to the next
  turn straight away; cards animate as on the site (drawn from the deck, flying to the stacks or the
  discard pile, hands shifting). Space (or →) applies the next real move on other players' turns; on your
  own turn it does nothing until you've chosen a move. ← goes back one turn at any time.
- **Auto-advance** (setting, decided 2026-09-25). In the lobby, "Turn advance" is Manual (Space / →) or
  Auto, with the seconds per turn (0.5–60, default 2). With Auto, each other player's move is revealed
  by itself after that many seconds. It waits while you look back at earlier turns (a replay after an undo is not looking back), on your own turn,
  while a panel is open and once the game is over. In Label mode an "Auto-advance" chip switches it on and
  off mid-game, and − / + beside it change the seconds (0.5 s steps up to 3 s, 1 s up to 10 s, then 5 s). Kept in the browser (`localStorage`), like the labeller's name; nothing is recorded.
- **Sounds as on the site.** Each newly revealed move plays the site's sound: hanab.live's own mp3 files,
  chosen by its rules (`getSoundType.ts`): turn-us when it's now your turn, otherwise turn-other, and the
  special sounds for blind plays (1–6 in a row), misplays (1 or 2 in a row), a lower max score ("sad") and
  the end of the game (perfect, success or fail). As with the site's default settings, the H-group sounds
  (discarding a clued card, double discards, order chop moves) are left out. Looking back at earlier turns
  is silent, as in a replay. Decided 2026-09-25
- **Undo** (Backspace) takes back your latest move and returns to that turn to choose again. Labels are
  append-only events, so undo adds a `retract` event and the new choice a new `label` event. A label made
  after the game has gone past its turn records `after_reveal` (hindsight): with moves applied at once,
  that is every label after an undo. After the new move the game goes on from there as it did before:
  the moves already revealed are replayed as live (auto-advance, sounds; your own turns that still have
  a move pass too) until it's back at the newest position, where it carries on as usual. The real move
  at the undone turn isn't shown there (except with the hint).
- **Looking back pauses the game.** ←, `[`, Home, the replay bar's rewind buttons or a click in the log
  show an earlier position silently, with auto-advance paused; with Auto on, the setting is greyed and
  hatched with "Paused: you went back". Space, →, the forward button or choosing a move resumes from the
  position shown, as live (sounds, auto-advance), replaying the moves already revealed until it's back at
  the newest position. Resuming from the lobby opens at the newest position. Decided 2026-09-25
- **Whose turn it is** is shown by the site's dark box behind the active hand plus a ▶ marker, and a
  pulsing yellow outline and "Your turn" when it's the labeller's.
- **Time to decide** is recorded for each label. It measures labelling cost and gives a weak confidence
  signal.
- **Submitting.** When the game is over, a Submit button hands the session in. It's refused while any
  of the labeller's turns has no move (after an undo, say); the tool jumps to the first such turn. A
  submitted session can still be viewed (hint included, since every real move is public by then), but
  moves, undo and hints are refused. Until then the session is **active**, and "ready to submit" once
  the game is over. A successful submit is rewarded with fireworks (花火) in the game's suit colours
  and a thank-you line for about 4.5 s (a click or key skips it; with reduced motion, just the line),
  then the tool returns to the lobby for the next seat.

### 4.2 Input

| Input | On your own card | On a teammate's card |
|---|---|---|
| Left click | Play | Colour clue for that card's suit, to that player |
| Right click | Discard (refused at 8 clues) | Rank clue for that card's rank, to that player |
| Shift + click (before the move) | Add as "equally good"; the plain click that follows makes the move | same |
| Hover | Clue log entries that touched the card turn white and entries that missed it turn red, as on the site | same |

Your own cards show only what clues say about them, as on the site with the pip-updating setting off:
positive clues on the card face and negative information when hovering. This is exactly `know`.

| Key | Action |
|---|---|
| Tab | Hint toggle: show the move that was actually made (arrows, as on the site; P/D for a play or discard) |
| Backspace | Undo your latest move and go back to that turn |
| Space or → | Next turn (reveals the next real move) |
| ← `[` `]` Home End | Look back at earlier turns (never past the current turn); going back pauses the game |

With 6 plain suits, **every legal move is exactly one click on some card**: a legal clue touches at least
one card, and clicking any card it touches produces exactly that clue. We don't need clue buttons. A click can
never produce an illegal clue, and it is checked against `obs.legal` anyway.

Later (not v0): card notes (Ctrl + right-click, as on the site), and "empathy" (seeing what teammates
know about their own cards; needs a key other than Tab, which is the hint).

### 4.3 Keeping information hidden

- **The browser only receives what the seat can know** (§6.1). Hiding cards on screen isn't enough once
  strangers label through devtools.
- **Several seats of one game are fine.** A second seat of the same game shows cards that were hidden in
  the first session, but once there are many games nobody remembers the cards of a particular one
  (decided 2026-09-25; before, a labeller got at most one seat per game).
- **Anonymised.** Players are shown as Alice, Bob, Cathy, Donald and Emily in seat order (Alice moves
  first). The server replaces the names before sending anything, and leaves out `seed`, date and final score.
- **The game ID is shown** (on the deck, as on the site, and in the lobby). With it a labeller could open
  the real replay and see their own hand, but our labellers are trusted volunteers, so we rely on them not
  to (decided 2026-09-25; before, labellers saw a keyed hash of the ID).
- **No worry about memory.** A labeller may have played the game or the deal before, but these players
  have played too many games to remember hands, so we don't track it.
- **The hint** shows the actual move. Every label records `hint_used`.

### 4.4 Several labellers: claims, the board and the admin view

- **Claims.** A (game, seat) with an active or submitted session is claimed and can't be started again,
  whether picked from the board or at random. "Start a random open seat" picks only unclaimed seats.
- **Expiry.** A session not submitted within 24 hours of its start expires, so abandoned sessions don't
  hold seats. Its events are deleted from `<game_id>.jsonl`, the session file is kept with `expired` set
  (so reopening it says it expired: 410), and it no longer counts anywhere: the seat is open again, and
  the lobby and admin view leave it out. Expiry is checked whenever sessions are read, with no background
  job. Submitted sessions never expire.
- **The board** (lobby, per labeller): every game open for labelling by game ID, newest first, with its
  players and variant, and each seat as ○ open (a "take" button), ✎ in progress or ✓ submitted, with
  who's on it. A search box narrows it to a game ID, to pick a specific game. Your own seats link to your
  session. Turn counts and scores are left out: they would hint at how the game ends. The lobby also
  splits your sessions into **active** and **submitted**.
- **The admin view** (`#/admin`, local/admin only, like Inspect): tiles and a bar for seats submitted /
  in progress / open and own turns labelled in submitted seats; one row per labeller (sessions
  submitted and active, moves, hint use, labels made after an undo, median time to decide, first and last
  activity); every session, most recent first; and coverage by game with real player names
  and each seat's status, labeller and progress. Games with check errors are listed as excluded.
  At the bottom, the list of games to open in **Inspect mode** (§5). Labellers don't inspect games, so
  the lobby doesn't list them (moved 2026-09-25); Inspect's Lobby button returns to the admin view.

---

## 5. Inspect mode

- All hands face-up, free navigation, and a switch to any seat's view: that seat's view is exactly
  `obs`, so it tests that the actor's cards are hidden correctly.
- **Checks panel:** the `representation.md` §9 checks, invariants (total card count preserved; each turn's history
  extends the previous one; label ∈ `legal`; `unseen` agrees with the visible cards), and differences
  from the official engine (§6.2). Each failure links to its turn. *Not built:* marking failures on the
  timeline.
- **Changes since the previous turn:** new cards, touched cards, and changes to stacks and counters.
  Off-by-one errors in slots are easiest to see here. *Not built as a panel;* after a one-turn step the
  cards slide to their new places, as on the site.
- **Details:** the DecisionRecord JSON (with `meta`), the seat's `obs`, and the raw export action (`J`).
- **Open on site:** `new.playhanabi.com/replay/<id>#<turn>` for side-by-side comparison.

---

## 6. Data flow

```
GameRecord ──► Python engine ──► game bundle ──► server ──► browser (label / inspect)
     │                               ▲             │
     └──► oracle (Node, hanab.live's reducer) ──► checks in the bundle
                                                   └──► labels (append-only events)
```

### 6.1 Game bundle

Made once per game by `review/server/bundle.py` (schema `hanabi-review-bundle/v0`). A game with `T`
actions has `T + 1` positions: before each action, plus the final position.

| Part | Contents |
|---|---|
| `game` | The GameRecord (export + listing) |
| `turns` | `T` |
| `log[k]` | Log lines in the site's wording. Line 0 is "X goes first"; line `k` is the action at UI turn `k` |
| `log_anon[k]` | The same lines with the Label mode names (Alice, Bob, … by seat) |
| `clues[]` | Every clue: `turn`, `giver`, `target` (absolute seats), `kind`, `value`, `touched` **and `missed`** (the rest of the recipient's hand, for the red clue log highlight) |
| `positions[t]` | Board and every hand's card IDs in slot order, absolute seats (Inspect only) |
| `sounds[t]` | The site's sound for position `t` (the action that led to it): an mp3 name from hanab.live's `public/sounds`, or null for the standard turn-us / turn-other, which depends on the listener's seat |
| `seat_views[s][t]` | `obs` for seat `s` at every position, including turns where `s` doesn't act. Relative seats, as in the DecisionRecord |
| `decisions[t]` | Full DecisionRecords for the acting player (Inspect only) |
| `checks[]` | `{turn, check, ok, kind, detail}`: invariants and oracle comparisons. `kind` is `error` or `wording` |

In Label mode the server sends only `seat_views[s]` (without `history` and `unseen`, which the screen doesn't
use), and `log_anon`, `clues` and `sounds` cut off at the frontier (the furthest turn reached). The deck is sent only
as the identities the seat has seen by the frontier. The `private` field, the other seats' views, `decisions`,
`checks`, `seed`, the real names and the actual moves for turns not reached yet are never sent.
The hint asks the server for one move at a time.

Note for `representation.md`: DecisionRecord clue events list only `touched`. The missed cards can be
reconstructed from `deal`/`drew` events, and their effect is already in `know`. But the labellers look at
them directly, which is an argument for adding `missed` to clue events for the model too.

### 6.2 The oracle

`review/oracle/oracle.mjs` feeds each export through hanab.live's `gameReducer`, **built from the repo at
`c1d970b`** (npm 0.0.8 is older, so we don't use it). `bundle.py` compares every position: score, clues,
strikes, deck count, pace, stacks, discard piles, hands in slot order, every clue's touched and missed
cards, and the log lines. The official reducer is the reference implementation, so this checks our engine
on every game, not just the ones we look at.

Limits: the reducer doesn't decide when a game ends (hanab.live's server does), and new.playhanabi.com runs
an older version than `c1d970b` (its log wording differs, `docs/progress.md`). End conditions and the
server's version are covered by the `listing.score` check (`representation.md` §9). That check is
skipped for now: `listing` is always `null` until the `/history` page is parsed.

---

## 7. Structure

```
review/
  run.sh        one command: set up if needed, rebuild bundles and web app, serve on :8765
  setup.sh      sparse checkout of hanab.live at c1d970b into vendor/, npm install, oracle bundle
  server/       Python: bundle.py (bundles + checks), labels.py (sessions + label events), serve.py (API + web app)
  labels/       Label data (local): sessions/<id>.json and one <game_id>.jsonl of events per game
  screenshots/  The screenshots in review/README.md
  web/          TypeScript + Vite, static files. GPL-3.0. Imports hanab.live's card drawing, images and sounds
                unmodified from vendor/ (aliases in web/vite.config.ts)
  oracle/       Node: hanab.live's own reducer, built from vendor/
```

| API | Purpose |
|---|---|
| `GET /api/games` | Game list with check status (Inspect list, admin view) |
| `GET /api/games/<id>/inspect` | Full bundle (local/admin only) |
| `GET /api/admin` | Coverage, labellers and sessions, with real player names (admin only, §4.4) |
| `GET /api/sessions?labeller=<name>` | That labeller's sessions (active and submitted) (Label lobby) |
| `GET /api/board?labeller=<name>` | Every game open for labelling, with each seat's status (§4.4) |
| `POST /api/sessions` `{labeller, game_id?, seat?}` | Start a session on that (game, seat), refused if it's claimed; or without a game on a random unclaimed seat. Games with check errors are never used. Any session call on an expired session returns 410 |
| `GET /api/sessions/<sid>` | The label view: the redacted bundle up to the frontier, labels, the real moves already public (resume) |
| `POST /api/sessions/<sid>/advance` | Reveal the next move. Refused on the labeller's own turn until it has a label |
| `POST /api/sessions/<sid>/labels` `{turn, choice, also_ok, ms_to_choice, advance}` | Append a label event, checked against that turn's `legal`. With `advance`, a label at the frontier also reveals the next move |
| `POST /api/sessions/<sid>/retract` | Undo the latest label still in force |
| `POST /api/sessions/<sid>/hint` `{turn}` | Reveal the actual move at one of the seat's turns (logged) |
| `POST /api/sessions/<sid>/submit` | Hand in the session: the game is over and every own turn has a move. Afterwards every changing call is refused (409) |

Label mode's API is keyed by **session**, not game. Every session call returns the whole label view. Locally, the server is a small Python process and labels go to
one JSON-lines file per game. Hosting later means adding sign-in and a database behind the same API.

`serve.py` options: `--port` (8765), `--host`, `--labels DIR` (default `review/labels/`) and `--mute` (serves
the page as `<html data-mute>`, which silences its sounds, for automated UI checks). Responses of 1 KB or
more are gzipped when the client accepts it: a label view late in a game is ~250 KB raw, ~8 KB gzipped,
which matters through a tunnel. Vite's content-hashed `/assets/` are cached as immutable, `index.html` is
`no-cache`.

UI changes are checked in headless Chrome with the `review-ui-check` skill
(`.claude/skills/review-ui-check/`): a throwaway muted server with fresh labels, driven over the DevTools
protocol.

---

## 8. Label record

Append-only events in `review/labels/<game_id>.jsonl`. Moves use **absolute** seats and permanent card IDs,
so labels stay valid when the DecisionRecord schema changes (exports never change). `turn` is the UI turn
(1-based), as in the DecisionRecord's `key.turn`.

```jsonc
{
  "kind": "label",
  "event_id": "…", "replaces": null,              // the label event at this turn that this one replaces
  "server": "new.playhanabi.com", "game_id": 78921, "turn": 4, "seat": 1,
  "labeller": "max", "session_id": "…", "at": "2026-09-23T21:00:00.000Z", "tool": "review/0.3",
  "choice": {"type": "clue", "to_seat": 0, "kind": "color", "value": "P"},   // or {"type": "play", "card": 7}
  "also_ok": [],
  "hint_used": false,                              // the hint was shown at this turn before this event
  "after_reveal": false,                           // the game had already gone past this turn (e.g. after an undo)
  "ms_to_choice": 8200                             // since the position was shown
}
```

The other event kinds share the same header fields (`event_id` … `tool`):

| `kind` | Fields | Meaning |
|---|---|---|
| `retract` | `replaces`, `turn` | Undo (Backspace): the label event `replaces` is no longer in force |
| `hint` | `turn`, `before_frontier` | The hint was shown at `turn` (logged once per turn) |
| `submit` | `labels` | The session was submitted with this many labels in force |

The label in force at a turn is the latest `label` event for it that hasn't been retracted. Events written
before 2026-09-24 have `changed_after_reveal` instead of `after_reveal` (always false in that data).

Session record (`review/labels/sessions/<id>.json`, rewritten on change): `game_id`, `seat`, `labeller`,
`chosen_by` (`"random"` or `"picked"` from the board), `started`, `ended` (the game was played to the end),
`submitted` (when it was handed in; `null` while active; missing in sessions from before 2026-09-25, which
are active), `expired` (when it expired, unsubmitted 24 hours after `started`; its events are deleted then),
`frontier` (furthest turn revealed), `hints`.

---

## 9. Resolved questions (2026-09-23)

1. **Pips on your own cards:** clue information only. The group doesn't use the site's pip-updating
   setting; they hover to highlight the clue log (§4.2).
2. **Player names:** anonymised (§4.3).
3. **Labellers who played the game or deal:** not a concern, not tracked.
4. **Other players' turns:** one keypress per turn, or auto-advance (added 2026-09-25); going back is always possible (§4.1).
5. **Which games:** random sample of (game, seat) pairs for now; since 2026-09-25 also a seat picked from the board.

---

## 10. Build order

| Phase | Deliverable | Done when |
|---|---|---|
| 1 ✅ | Oracle script, and a bundle generator based on the prototype (now on the engine, `hanabi_data/`) | Games 78921 and 78822 match the official reducer on every turn (done 2026-09-23, `docs/progress.md`) |
| 2 ✅ | Inspect mode, read-only: row layout, card art, action log, clue log with hover highlight, stacks, discard pile, counters, replay keys, site link | It reproduces screenshots 1–3 (done 2026-09-23, `docs/progress.md`) |
| 3 ✅ | Label mode: random seat sessions, anonymisation, speedrun controls, hint toggle, undo, saving to local files. Revised 2026-09-24 (moves apply at once, card animations; hover preview, skip, throw-away mark and notes removed) and 2026-09-25 (auto-advance, sounds, replay after undo, pause, mismatch cue) | A full seat of 78921 can be labelled and resumed (done 2026-09-23, `docs/progress.md`) |
| 4 | Checks panel, game browser | Mostly there: Inspect's checks panel (phase 2) and the admin view's list of games (4a). Left: failures marked on the timeline, a "changes since the previous turn" panel (§5), filtering or searching the games list |
| 4a ✅ | Several labellers: submit vs active sessions, claims, lobby board, 24-hour expiry, admin view; gzip for sharing through a tunnel | Done 2026-09-25 (`docs/progress.md`) |
| 5 | Later: card notes, empathy, prioritised sampling, hosting (sign-in, database) | |

Screenshot 3 is game **78822** (3 players, 9 actions), saved as `examples/export_78822.json`. The
prototype's replay at UI turn 6 matches everything in screenshot 3: counters, pace, stacks, discard pile,
all three hands and the clued cards.
