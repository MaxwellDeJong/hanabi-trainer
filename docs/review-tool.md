# Review tool: design v0 (draft)

*Status: draft for discussion · 2026-09-23*

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
| Actual move | Hidden by default. A **hint toggle** (Space) shows it when the labeller is stuck, and the label records that the hint was used |
| Knowledge display | **Clue information only** (our `know`). The group plays with the site's pip-updating setting off and instead hovers a card to highlight the clue log entries that concern it (§4.2). No deduction from visible cards |
| Player names | **Anonymised** in Label mode (§4.3) |
| Advancing | **Your move advances the game at once**, as in a live game (no confirmation). → for each other player's turn. Going back is always possible |
| Extras | None: no skip, no throw-away mark, no notes, no on-screen control hints (labellers know the controls). Decided 2026-09-24 |
| Which games | **Random sample** of (game, seat) pairs for now. Prioritising particular games may come later |
| License | Borrowing from hanab.live (GPL-3.0) is fine. The borrowed code stays in its own directory (§7) so the licence doesn't extend to the engine or dataset code |
| Game logic | The browser **does not compute game state**. It displays what the Python engine produced (§6), so inspecting the tool inspects the real training records |

---

## 2. What we take from hanab.live

Source: `Hanabi-Live/hanabi-live`, commit `c1d970b` (2026-09-18), the same commit `representation.md` §4 cites.

| Feature | Where in the source | Use |
|---|---|---|
| Speedrun click handling | `packages/client/src/game/ui/HanabiCardClickSpeedrun.ts` | Copy its behaviour (§4.2). Discarding at 8 clues is refused and the clue counter flashes. Clues need ≥1 clue token |
| Default "row" layout | `client/src/game/ui/drawHands.ts:110–172` (the non-Keldon, "Board Game Arena" layout) | Hands are stacked in rows. **Our own hand is the top row**, and the others follow in turn order below it (`j = (i − ourPlayerIndex) mod n`). Screenshots 1–3 use this layout |
| Card faces and pips | `client/src/game/ui/drawCards.ts`, `drawPip.ts`, `drawPipFunctions.ts` (plain canvas-2D drawing, ~4k lines) | Port these to draw card images once, then use the images in a DOM layout |
| Clue arrows | `client/src/game/ui/arrows.ts` | Style for the hint overlay and the clue highlights (screenshot 2) |
| Clue log hover | `client/src/game/ui/ClueEntry.ts:128`, `HanabiCardMouse.ts:44` | Hovering a card highlights clue log entries that **touched** it (white) and entries that **missed** it (red `#ff7777`). Clicking an entry jumps to that turn |
| Log wording | `packages/game/src/rules/text.ts` (`getClueText`, `getPlayText`, `getDiscardText`, …) | Log lines word-for-word as on the site ("… tells X about two 1s", "… plays Yellow 1 from slot #5") |
| Replay keys | `docs/features.md` "Keyboard Shortcuts" | ← → one turn, `[` `]` one full round, Home/End |
| Replay URL | `client/src/game/ui/gameCommands.ts:271` | `https://new.playhanabi.com/replay/<id>#<turn>` for "open on site" (still to confirm on that server) |
| Official rules engine | `@hanabi-live/game` on npm (v0.0.8, GPL-3.0): `gameReducer` | **An independent reference to check against** (§6.2) |

---

## 3. Modes

| | **Label** | **Inspect** |
|---|---|---|
| Who | Labellers | Us, while building the engine |
| View | One seat's view only; that seat's own cards face-down | All hands face-up (like spectating), with a switch to any seat's view |
| Navigation | Forward only as far as the current turn. Going back to earlier turns works like the in-game replay | Any turn |
| Extras | Hint toggle, label input | Checks panel, DecisionRecord JSON, differences from the official engine, "open on site" link |
| Data sent to the browser | Only what that seat can know (§6.1) | Everything |

---

## 4. Label mode

### 4.1 Session flow

A session is `(game, seat, labeller)`. It starts at turn 1 and runs to the end of the game.

```
other player's turn ──(→)──► the real move is applied and logged ─┐
        ▲                                                         │
        │                                                         ▼
        └── the real move is applied ◄── labeller clicks a move ◄── labeller's turn
            at once (shown in the log;                              (Space shows the actual move)
            "you: X" if different)
```

- **The real game always continues.** The labeller's choice is recorded, but the next state comes from the
  export. There are no hypotheticals. When the choice differs from the real move, the log shows the real
  move with a small "you: …" marker (✓ when they match), so it's clear what happened.
- **A click is the move.** As in a live game, clicking a move records it and the game goes on to the next
  turn straight away; cards animate as on the site (drawn from the deck, flying to the stacks or the
  discard pile, hands shifting). → applies the next real move on other players' turns; on your own turn
  it does nothing until you've chosen a move. ← goes back one turn at any time.
- **Undo** (Backspace) takes back your latest move and returns to that turn to choose again. Labels are
  append-only events, so undo adds a `retract` event and the new choice a new `label` event. A label made
  after the game has gone past its turn records `after_reveal` (hindsight): with moves applied at once,
  that is every label after an undo.
- **Whose turn it is** is shown by the site's dark box behind the active hand plus a ▶ marker, and a
  pulsing yellow outline and "Your turn" when it's the labeller's.
- **Time to decide** is recorded for each label. It measures labelling cost and gives a weak confidence
  signal.

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
| Space | Hint toggle: show the move that was actually made (arrows, as on the site; P/D for a play or discard) |
| Backspace | Undo your latest move and go back to that turn |
| → | Next turn (reveals the next real move) |
| ← `[` `]` Home End | Look back at earlier turns (never past the current turn) |

With 6 plain suits, **every legal move is exactly one click on some card**: a legal clue touches at least
one card, and clicking any card it touches produces exactly that clue. We don't need clue buttons. A click can
never produce an illegal clue, and it is checked against `obs.legal` anyway.

Later (not v0): card notes (Ctrl + right-click, as on the site), and "empathy" (seeing what teammates
know about their own cards; needs a key other than Space, which is the hint).

### 4.3 Keeping information hidden

- **The browser only receives what the seat can know** (§6.1). Hiding cards on screen isn't enough once
  strangers label through devtools.
- **One seat per labeller per game.** Labelling a second seat of the same game would show cards that were
  hidden in the first session.
- **Anonymised.** Players are shown as Alice, Bob, Cathy, Donald and Emily in seat order (Alice moves
  first). The server replaces the names before sending anything. Label mode also hides everything that
  would identify the game: the game ID (the site shows it on the deck), `seed`, date and final score.
  With the game ID, a labeller could open the real replay and see their own hand.
- **No worry about memory.** A labeller may have played the game or the deal before, but these players
  have played too many games to remember hands, so we don't track it.
- **The hint** shows the actual move. Every label records `hint_used`.

---

## 5. Inspect mode

- All hands face-up, free navigation, and a switch to any seat's view: that seat's view is exactly
  `obs`, so it tests that the actor's cards are hidden correctly.
- **Checks panel:** the `representation.md` §9 checks, invariants (total card count preserved; each turn's history
  extends the previous one; label ∈ `legal`; `unseen` agrees with the visible cards), and differences
  from the official engine (§6.2). Each failure links to its turn and is marked on the timeline.
- **Changes since the previous turn:** new cards, touched cards, and changes to stacks and counters.
  Off-by-one errors in slots are easiest to see here.
- **Details:** the DecisionRecord JSON, the raw export action, and `meta`.
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
| `seat_views[s][t]` | `obs` for seat `s` at every position, including turns where `s` doesn't act. Relative seats, as in the DecisionRecord |
| `decisions[t]` | Full DecisionRecords for the acting player (Inspect only) |
| `checks[]` | `{turn, check, ok, kind, detail}`: invariants and oracle comparisons. `kind` is `error` or `wording` |

In Label mode the server sends only `seat_views[s]` (without `history` and `unseen`, which the screen doesn't
use), and `log_anon` and `clues` cut off at the frontier (the furthest turn reached). The deck is sent only
as the identities the seat has seen by the frontier. The `private` field, the other seats' views, `decisions`,
`checks`, the game ID, `seed`, the real names and the actual moves for turns not reached yet are never sent.
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
server's version are covered by the `listing.score` check (`representation.md` §9).

---

## 7. Structure

```
review/
  run.sh        one command: set up if needed, rebuild bundles and web app, serve on :8765
  setup.sh      sparse checkout of hanab.live at c1d970b into vendor/, npm install, oracle bundle
  server/       Python: bundle.py (bundles + checks), labels.py (sessions + label events), serve.py (API + web app)
  labels/       Label data (local): sessions/<id>.json and one <game_id>.jsonl of events per game
  web/          TypeScript + Vite, static files. GPL-3.0. Imports hanab.live's card drawing and images
                unmodified from vendor/ (aliases in web/vite.config.ts)
  oracle/       Node: hanab.live's own reducer, built from vendor/
```

| API | Purpose |
|---|---|
| `GET /api/games` | Game list with check status (Inspect lobby) |
| `GET /api/games/<id>/inspect` | Full bundle (local/admin only) |
| `GET /api/sessions?labeller=<name>` | That labeller's sessions, without game IDs (Label lobby) |
| `POST /api/sessions` `{labeller}` | Start a session: a random (game, seat) from a game this labeller has no session in and with no check errors |
| `GET /api/sessions/<sid>` | The label view: the redacted bundle up to the frontier, labels, notes (resume) |
| `POST /api/sessions/<sid>/advance` | Reveal the next move. Refused on the labeller's own turn until it has a label |
| `POST /api/sessions/<sid>/labels` `{turn, choice, also_ok, ms_to_choice, advance}` | Append a label event, checked against that turn's `legal`. With `advance`, a label at the frontier also reveals the next move |
| `POST /api/sessions/<sid>/retract` | Undo the latest label still in force |
| `POST /api/sessions/<sid>/hint` `{turn}` | Reveal the actual move at one of the seat's turns (logged) |

Label mode's API is keyed by **session, not game**, so the browser never learns the game ID (§4.3). Every
session call returns the whole label view. Locally, the server is a small Python process and labels go to
one JSON-lines file per game. Hosting later means adding sign-in and a database behind the same API.

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

The label in force at a turn is the latest `label` event for it that hasn't been retracted. Events written
before 2026-09-24 have `changed_after_reveal` instead of `after_reveal` (always false in that data).

Session record (`review/labels/sessions/<id>.json`, rewritten on change): `game_id`, `seat`, `labeller`,
`chosen_by` (`"random"`), `started`, `ended`, `frontier` (furthest turn revealed), `hints`.

---

## 9. Resolved questions (2026-09-23)

1. **Pips on your own cards:** clue information only. The group doesn't use the site's pip-updating
   setting; they hover to highlight the clue log (§4.2).
2. **Player names:** anonymised (§4.3).
3. **Labellers who played the game or deal:** not a concern, not tracked.
4. **Other players' turns:** one keypress per turn; going back is always possible (§4.1).
5. **Which games:** random sample of (game, seat) pairs for now.

---

## 10. Build order

| Phase | Deliverable | Done when |
|---|---|---|
| 1 ✅ | Oracle script, and a bundle generator based on the prototype | Games 78921 and 78822 match the official reducer on every turn (done 2026-09-23, `docs/progress.md`) |
| 2 ✅ | Inspect mode, read-only: row layout, card art, action log, clue log with hover highlight, stacks, discard pile, counters, replay keys, site link | It reproduces screenshots 1–3 (done 2026-09-23, `docs/progress.md`) |
| 3 ✅ | Label mode: random seat sessions, anonymisation, speedrun controls, hover preview, hint toggle, undo, saving to local files | A full seat of 78921 can be labelled and resumed (done 2026-09-23, `docs/progress.md`) |
| 4 | Checks panel, game browser | |
| 5 | Later: card notes, empathy, prioritised sampling, hosting (sign-in, database) | |

Screenshot 3 is game **78822** (3 players, 9 actions), saved as `prototype/examples/export_78822.json`. The
prototype's replay at UI turn 6 matches everything in screenshot 3: counters, pace, stacks, discard pile,
all three hands and the clued cards.
