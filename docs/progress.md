# Progress log

Newest entries first. Design lives in `representation.md`, `review-tool.md` and `label-filtering.md`
(which also keeps its own log); this file records what was done, what was found, and what's next.

Older entries are kept as written. Where the code has since changed, the entry has a *Since changed* note, and
**Current state** below has the up-to-date picture.

---

## Current state (checked against the code 2026-09-25)

`python3 -m pytest`: **96 passed**. 12 games on hand (3 in `examples/`, 10 in `data/exports/`, 78921 in
both): 443 moves. `check` passes on all 12, and all 12 bundles match hanab.live's reducer with 0 errors.

| Area | Where | State |
|---|---|---|
| **Data model** | `hanabi_data/` (`record.py`, `convert_export.py`, `convert_live.py`, `decision.py`) | GameRecord (event log) from an export or a live websocket capture; DecisionRecord `hanabi-decision/v0` per turn from any seat's view, with `meta` (result, `label_effect`, misplay run fields, `seed`, `filters`). CLI: `convert-export`, `convert-live`, `decision`, `decisions`, `check`, `filters`. `listing` is always `null` (no `/history` parser), so `meta.datetime` is `null` |
| **Game engine** | `hanabi_data/engine.py`, `rules.py` | Replays and validates GameRecords, full information or one player's view; every rule-based ending incl. All or Nothing, strikeout, surrender. Frozen against the retired prototype's output by `tests/data/golden_decisions.jsonl` (`meta` not compared); checked against hanab.live's reducer by `review/oracle/` |
| **Downloader** | `hanabi_data/download.py` | Polite: one request at a time, 5 s + jitter, cached, refuses more than 20 missing exports, stops at the first problem. Used once, for the 10 games in `target_games.txt`. **Bulk download waits for the server owner's permission** |
| **Move filtering** | `hanabi_data/filters.py`, `docs/label-filtering.md` | Two `candidate` filters (`play_clued_5_no_4`, `discard_clued_5_live`) and the `filters` report. None `agreed` yet, so `meta.filters` is always `[]`. On the 12 games: one catch (78922 turn 10, looks deliberate) |
| **Review tool** | `review/` | **Inspect** (full bundle, any seat's view, checks, JSON; reached from the admin view) and **Label** (one seat per session, anonymised players, speedrun controls, moves apply at once, Tab hint, Backspace undo with replay, manual or auto-advance, the site's sounds, mismatch cue, Submit with fireworks). Several labellers: claims, board by game ID, 24-hour expiry, admin view. gzip for sharing through a tunnel. Label data in `review/labels/`: 5 submitted and 2 active sessions (test labellers), 76 labels |

**Known gaps**
- No sign-in: the labeller name is typed, and `#/admin` and Inspect are open to anyone who can reach the server.
- Labels in `review/labels/` are not committed and not in `.gitignore`: undecided.
- No live capture yet of an `init` message, a successful `play`, 3–5 players or All or Nothing.
- Surrender: no "… terminated the game!" log line; the admin view's Inspect list shows the board score,
  not the recorded one (0 for any ending other than Normal).
- Failed play of an unclued card: " (blind)" at `c1d970b`, nothing in ours; the site's wording unconfirmed.
- Inspect: efficiency not computed; hidden-card pips approximated; the site's replay URL untested.
- `status.trash` misses dead cards (`representation.md` Q13).
- `review-tool.md` phase 5: card notes, empathy, prioritised sampling, hosting.

**Next**
- Bulk download once permitted, then run `check` and the `filters` report on it.
- Decide whether to agree the two candidate filters, and the next widening (`label-filtering.md` §7).
- Share the review tool with a few labellers through a tunnel; decide on sign-in before hosting.
- Capture another live game (All or Nothing, 3+ players, with an `init` message).

---

## 2026-09-25 · Label mode: replay after undo, pause, mismatch cue; UI check skill ✅

Fixes and additions after labelling the new games:

| Change | Now |
|---|---|
| Replay after undo | After an undo and the new move, the game replays the moves already revealed as live (sounds, auto-advance; your own turns that have a move pass too) until it's back at the newest position. Before, the replay stopped auto-advancing after the undo |
| Pause | Looking back (←, Home, the rewind buttons, the log) pauses the game: silent, no auto-advance. With auto-advance on, the setting is greyed and hatched with "Paused: you went back". Space / →, the replay bar's forward button or choosing a move resumes from there, with sounds and auto-advance. Before, stepping forward after looking back stayed silent and manual all the way to the newest position |
| Mismatch cue | When the recorded move differs from yours, the position after it shows a dashed amber "You: …" tag on the cards your move was about, and, when the game steps onto it, the hand your move concerned (yours, or the clued player's) pulses red once (1.6 s) |
| `serve.py --mute` | Serves the page as `<html data-mute>`, which silences all sounds, for automated checks |
| UI check skill | `.claude/skills/review-ui-check/`: builds, starts a throwaway muted server with fresh labels (`serve_scratch.sh`), and drives headless Chrome over the DevTools protocol (`cdp.py`) for clicks, keys, screenshots, DOM and console checks |

`review-tool.md` §4.1 describes the replay and pause.

---

## 2026-09-25 · Review server: gzip, for sharing through a tunnel ✅

Getting ready to share the tool with a few friends through a Cloudflare quick tunnel. Every click in Label
mode resends the whole label view: every per-turn observation up to the frontier (`seat_views` is ~90%
of it). At the end of game 78663 (75 turns) that is 259 KB per click.

`serve.py` now gzips JSON responses and the web app's text files (html/js/css/json/svg) of 1 KB or more when
`Accept-Encoding` allows it (`Vary: Accept-Encoding`). Images and mp3s go through the stock handler as before.
Files under `/assets/` (content-hashed by Vite) are sent `immutable`, and `index.html` is sent `no-cache`.

| Measured (curl) | Plain | gzip |
|---|---|---|
| Label view, 78663 at the end | 258,740 B | 7,633 B |
| JS bundle | 619,933 B | 108,265 B |

Considered and left for later: **sending only what changed** (`?have=N`). The per-turn arrays are append-only
(the frontier never moves back), so the design is simple. Gzipped it would be 1.6 KB per click, but the gain
over gzip (~6 KB) is lost in the tunnel's round-trip. hanab.live itself sends one `gameActionList`, then
single `gameAction` messages scrubbed per player (`actions_scrub.go`), and the client runs the reducer. We
don't copy that: knowledge would be computed in the browser, separately from the engine the oracle checks.

`tests/test_review_serve.py` (3 tests, real handler on a free port): gzip only when accepted (including
`gzip;q=0`), small bodies plain, static caching headers. `python3 -m pytest`: 96 passed.

---

## 2026-09-25 · Label filters for pretraining: first two candidates ✅

Two candidate filters (`play_clued_5_no_4`, `discard_clued_5_live`), the `filters` report command and
`meta.filters`. Label filtering now has its own document, **`label-filtering.md`**: approach, decisions,
proposals, open questions and its own progress log.

---

## 2026-09-25 · Label mode: auto-advance, sounds, fireworks; expiry and an open board ✅

Built with the several-labellers work below, and changes some of it:

| Change | Now |
|---|---|
| Auto-advance | Lobby setting "Turn advance": Manual (Space / →) or Auto, 0.5–60 s per turn (default 2). In Label mode an "Auto-advance" chip switches it and − / + change the seconds. Waits on your own turn, while looking back, while a panel is open and once the game is over. Kept in `localStorage` |
| Keys | Hint moved to **Tab**; Space and → both reveal the next move |
| Sounds | Each newly revealed move plays the site's mp3 (from `vendor/`, unmodified). `bundle.py` picks it by hanab.live's `getSoundType.ts` rules into a new bundle field `sounds`: blind plays (1–6 in a row), misplays (1–2 in a row), "sad" when the max score drops, and the three endings; otherwise turn-us / turn-other. H-group and variant sounds left out, as with the site's defaults. Looking back is silent. `tests/test_review_sounds.py` (4 tests) |
| Fireworks | A successful Submit shows fireworks in the game's suit colours and a thank-you for ~4.5 s (click or key skips; reduced motion: just the line), then returns to the lobby |
| Expiry | A session not submitted within **24 hours** of its start expires: its events are deleted from `<game_id>.jsonl`, the session file keeps `expired`, reopening it answers 410, and the seat is open again. Checked whenever sessions are read. Submitted sessions never expire. Replaces the admin view's "stale after 3 days" flag |
| Board by game ID | The board lists games by their real ID, newest first, with a search box, instead of an HMAC code (`secret.key` is gone). Labellers are trusted not to look games up. Turn counts and scores are still left out. Label mode shows it on the deck, as the site does |
| Several seats per game | A labeller may take several seats of one game; a random start picks any unclaimed seat |
| Inspect moved | The lobby no longer lists games for Inspect; the admin view does, and Inspect's Lobby button returns there |
| Tooltips | The lobby, Label mode and admin view explain themselves in hover tooltips (`tips.ts`) and "?" icons |

`tests/test_review_labels.py` is now 9 tests (adds expiry, submitted sessions never expiring, old sessions
still loading). Design: `review-tool.md` §1, §4.1, §4.4.

---

## 2026-09-25 · Review tool: several labellers (submit, claims, board, admin view) ✅

Getting ready for more than one labeller (`review-tool.md` §4.1, §4.4):

| Change | Now |
|---|---|
| Active vs submitted | A session is **active** until the labeller presses **Submit** at the end of the game. Submitting is refused while any own turn has no move (the tool jumps to it). Afterwards the session is read-only: moves, undo, hints and advancing return 409. New `submit` event; session field `submitted` |
| Claims | A (game, seat) with any session is claimed. Random starts pick only unclaimed seats, in games the labeller has no seat in |
| Board | The lobby lists every game open for labelling by an 8-character **code** (HMAC of the game ID with `review/labels/secret.key`, gitignored) and each seat as ○ take / ✎ in progress / ✓ submitted, with who's on it. "take" starts a session on that seat (`chosen_by: "picked"`). No game IDs, turn counts or scores |
| Lobby | Your sessions split into active ("resume →", or "submit →" once the game is over) and submitted ("view") |
| Admin view | `#/admin` (`GET /api/admin`): tiles and a status bar, one row per labeller (submitted/active, moves, hint use, after-undo labels, median time, last activity), every session (stale after 3 days idle) and coverage by game with IDs linking to Inspect |

`tests/test_review_labels.py` (6 tests; bundles built without the oracle) covers claims, picking, the
one-seat-per-game rule, submitting (including after an undo) and read-only afterwards, the board hiding
game IDs, and the admin totals. `python3 -m pytest`: 77 passed. Driven in headless Chrome on a copy of
`review/labels/`: resume → Submit → read-only; lobby split; a second labeller took a seat from the board;
admin page counts match.

*Since changed (same day, entry above):* the board lists games by ID with a search box (no HMAC codes or
`secret.key`), a labeller may take several seats of one game, unsubmitted sessions expire after 24 hours
(replacing the "stale after 3 days" flag), and Inspect is reached from the admin view.

**Existing data:** the sessions in `review/labels/` have no `submitted` field, so they count as active.
7c8e94786969 (tester, 78822) is played to the end and shows as "submit →". *Since changed:* both of
tester's sessions (7c8e94786969, 77db208ecc8f) have expired; the data now holds tester1's 5 submitted
seats (78663, 78916, 78852, 78822, 78738) and 2 active sessions.

**Not done:**
- No sign-in: the labeller name is typed, and `#/admin` and Inspect are open to anyone who can reach the
  server. Fine locally; hosting needs sign-in and an admin role.
- A stale active session keeps its seat claimed; there's no release/reassign yet (the admin view only
  flags it). *Since changed:* it expires after 24 hours and the seat opens again.
- No way to hand one seat to two labellers on purpose (e.g. to measure agreement).

---

## 2026-09-25 · Prototype retired: golden decision records ✅

`prototype/` (`replay.py` and its copies of the example games) is gone. The example games moved to
`examples/` (with a README), and every reference was updated.

The parity test (engine vs prototype at every position × seat) is replaced by
`tests/test_golden_decisions.py`: `tests/data/golden_decisions.jsonl` is the prototype's output for every
position and seat of the three example games, and the engine must match it (`schema`, `key`, `obs`,
`label`, `private`; `meta` isn't compared). Regenerate it only for an intended change to the decision
record, and review the diff.

---

## 2026-09-25 · Polite downloader and a 10-game sample ✅

Bulk downloading waits for the server owner's permission. To keep going meanwhile, `target_games.txt`
lists 10 hand-picked games: 2–4 players, 6 and 5 suits, wins, 3 bombs, discard endings and a surrender.
`hanabi_data/download.py` fetched them after review (78921 was copied from `examples/`, so 9 requests; no errors).

```bash
python3 -m hanabi_data.download --dry-run $(awk -F', ' 'NR>1{print $2}' target_games.txt)
python3 -m hanabi_data.download $(awk -F', ' 'NR>1{print $2}' target_games.txt)
```

- One request at a time, 5 s apart plus up to 2.5 s of jitter (`--delay`, minimum 1 s). Gzip accepted.
  The User-Agent names the tool and a contact (`--contact`, default `harikari.live`).
- Exports go to `data/exports/export_<id>.json` unchanged, and a cached ID is never fetched again.
- Refuses to start if more than 20 exports are missing (`--max-requests`).
- Stops at the first problem, with no retries: HTTP error (e.g. 429), timeout, or a reply that isn't the
  requested game's export (not JSON, wrong `id`, no `deck`/`actions`).
- `tests/test_download.py` tests all of this with fake network calls.

**`check` passes on all 10.** What the sample shows, and the answers given (`representation.md` §2, §3.6):
1. **All or Nothing and speedrun don't matter in practice.** A game is won only at the max score, whatever the
   options. The engine still reads both, since they decide when the game ends; they're also kept for accounting.
   The two 5-suit games are not All or Nothing (Q5 answered: include 5-suit games).
2. **A double discard is a loss:** once every copy of a needed card is gone, the max score can't be reached.
   78919 (both T3s) and 78852 (R5) end with All or Nothing fail (8).
3. **78876 is a strikeout, not a double discard** (misplays on turns 1, 10 and 13; `target_games.txt` lists it as
   a double discard).
4. **The surrender (78916)** ends with the end action `{"type": 4, "target": 0, "value": 4}`, the first seen in a real
   export. The engine accepts it.
5. **Exports aren't committed:** `data/` is in `.gitignore`.

### Review bundles

`review/run.sh` now builds bundles for `data/exports/` as well as `examples/`, so the lobby lists
all 12 games and Label sessions draw from them. **All 10 new games match hanab.live's reducer at every
position (0 errors).** Two fixes to the oracle (`review/oracle/oracle.mjs`) were needed:
1. **`speedrun` was refused** as an unknown option. The reducer never reads it (only the server's end check
   does), so it's now passed through.
2. **The surrender's end action (78916) got its own snapshot,** so the oracle had one more position than the
   engine (10 vs 9). The engine doesn't count an end action as a turn, so the oracle no longer takes a
   snapshot for it.

Checked in headless Chrome: 12437 (the first 4-player, 5-suit game in the viewer) lays out 4 rows of 4 cards,
5 stacks and "/ 25"; 78916's last position shows the final state with its 2 strikes.

**Wording differences** are the known ones ("(clued)" on failed plays, which the site prints), plus one new
one: for a failed play of an unclued card (78876 turn 1) `c1d970b` prints " (blind)" and we print nothing.
The site's wording for that case is still unconfirmed.

**Not done:**
- The site ends the log with "… terminated the game!" for a surrender. Our log and the viewer don't show
  the end action at all.
- The lobby's "final score" column shows the board score (e.g. 4 for 78921). The server records 0 for every
  ending other than Normal, and any game below the max counts as a loss. *Since changed:* the games list
  moved to the admin view's Inspect section ("Score"), still with the board score.

Next: label a few of the new games (long wins, a 4-player game) to check how Label mode feels on full-length
games. *Done:* 5 seats submitted (78663, 78916, 78852, 78822, 78738); the fixes it led to are in the entries above.

---

## 2026-09-23 · Engine and GameRecord layer (`hanabi_data/`) ✅

**Done when:**
- the §8 example comes out byte for byte
- the example games still pass the oracle
- the live record of table 43267 and the export record of game 78922 give identical `obs` for seat 0

All three hold, and `python3 -m pytest` passes 63 tests.

```bash
python3 -m hanabi_data decision examples/export_78921.json 4 --pretty
python3 -m hanabi_data check examples/export_*.json
python3 -m pytest
```

### What was built

| Piece | What it does |
|---|---|
| `hanabi_data/` | Rules, engine, export and live converters, decision records, checks, CLI. Python 3.9, standard library only. Layout in `representation.md` §5.1 |
| Engine | Replays GameRecord events and rejects illegal ones: turn order, card in hand, clue tokens, discarding at 8, clue touches, draw order, copy counts. Detects every rule-based ending in `game.go` `CheckEnd` order. Works on a player's view (own cards unknown) as well as on full information |
| Live converter | `gameActionList`/`gameAction`/`init` → GameRecord. Drops the seed. Works out the view from which draws are hidden. `check_stream` compares the replay with every `status` and `turn` message |
| Decision records | One replay per game instead of one per turn. Full `meta` (§7.3): result, `turns_to_end`, `label_effect`, `misplay_knowable`, `misplay_run_to_end`, `end_misplay_run`, `seed` |
| `tests/` | Prototype parity (every position × seat; *since replaced* by golden decision records, see 2026-09-25), worked example, live captures, endings, rejected input. `tests/data/synthetic_*.json`: five long games (softlock, play past the deck under All or Nothing, final rounds) found by a random full-information search |
| `review/server/bundle.py` | Now uses the engine instead of the prototype. Bundle fields are unchanged; Label mode reads them (checked with the review-tool session) |

### Findings

1. **Any ending other than Normal records a score of 0,** with or without All or Nothing
   (`game_end.go:17`). So a strikeout in a standard game also scores 0. `representation.md` §4 said this
   only for All or Nothing; fixed.
2. **Without All or Nothing the game can end before the final round is over.** After a player's last
   turn, their hand counts as unplayable, and the game ends as soon as no remaining card can be played.
   The synthetic game `final_round` ends one turn early for this reason.
3. **The prototype hardcoded `all_or_nothing: true`,** which was wrong for 78922. That was its only
   difference from the engine.
4. **In a player's view, the engine sometimes can't tell whether the "nothing playable" ending applies,**
   because it depends on the player's own unknown cards. It then accepts the stream's `gameOver`. This
   only matters without All or Nothing.
5. **hanab.live's reducer agrees on all five synthetic games** (0 errors on 8,410 checks), including
   hands shrinking to nothing after the deck runs out.
6. **`status.trash` misses dead cards** (above a rank with every copy discarded). The engine keeps the
   prototype's behaviour for now; `representation.md` Q13.

### Not done / known gaps

- `listing` is always `null`: the `/history` page parser doesn't exist yet, so `meta.datetime` is `null`
  and the score check against the listing is skipped.
- The `init` message is parsed from the server source's shape; no real capture of one yet.
- Live streams with a successful `play`, 3–5 players or All or Nothing are still uncaptured (§3.4).

### Next

Capture another live game (All or Nothing, 3+ players, with an `init` message). Then, once approved, a
pilot download of ~50–100 exports to run `check` on (`representation.md` Q5, Q10, Q12).

---

## 2026-09-24 · Review tool: Label mode revised after first try ✅

Changes asked for after trying phase 3, all done and tested in headless Chrome (a full seat of 78921
labelled with one click per own turn, resumed partway through; undo, hint and animations checked):

| Change | Now |
|---|---|
| No control hints | The hover preview ("Left: … · Right: …" and the L/R tags) is gone; hover only highlights the clue log, as on the site |
| Fewer options | Skip, throw-away mark and notes removed, from the UI and the API. Buttons left: Hint, Undo |
| Hint key | Space (was H). *Since changed:* Tab; Space and → reveal the next move |
| Moves apply at once | Clicking a move records it and the game goes on to the next turn, in one request (`labels` with `advance`). No → needed after your own move |
| Undo | Backspace takes back your latest move and returns to that turn to choose again |
| Card animations | After a one-turn step (either direction) every card that changed place slides there, as on the site: new cards from the deck, plays to the stack (the card underneath stays visible), discards and misplays to the discard pile, hands shifting over. 400 ms. Longer jumps don't animate. Also in Inspect mode |
| Whose turn | The active hand's dark box gets an outline and a ▶ marker; on your own turn the outline is yellow and pulses and the status line says "Your turn" |

`changed_after_reveal` is now **`after_reveal`**: the label was made after the game had gone past its
turn. With moves applied at once, that's every label made after an undo. It used to be set only when a
label replaced another, so a new label after an undo wasn't flagged. The two sessions already in
`review/labels/` (labeller "tester") have the old field name; the server reads them as `after_reveal:
false`. Their data wasn't rewritten.

Bugs found while testing: `classList.add` with a space in the token threw and stopped the hands from
drawing; the label store now recreates its folders if they disappear.

---

## 2026-09-23 · Review tool, phase 3: Label mode ✅

**Done when** a full seat of 78921 can be labelled and resumed. It can: driven in headless Chrome, a
labeller was given seat 1 of 78921 at random, labelled turns 2–6, went back to the lobby, resumed at turn 6
and finished the game. The events file has one label per turn with the right `choice`.

```bash
bash review/run.sh     # then open http://127.0.0.1:8765/, enter a name under "Label", "Start a new session"
```

### What was built

| Piece | What it does |
|---|---|
| `review/server/labels.py` | Sessions and append-only label events (`review-tool.md` §8). Random (game, seat) from games the labeller has no session in and with no check errors. The **label view** is the bundle cut off at the frontier and redacted: anonymised names (`log_anon`), only this seat's views, only card identities the seat has seen, no game ID/seed/checks/decisions |
| Rules enforced by the server | → is refused on your own turn until you label or skip; every label is checked against that turn's `legal`; hints only for your own turns already reached; `changed_after_reveal` when a label is replaced after the turn was passed or the hint shown |
| `review/server/serve.py` | Session API (`review-tool.md` §7), keyed by session so the game ID never reaches the browser |
| `review/web/src/label.ts` | Label mode on the same table as Inspect: speedrun clicks (left = play / colour clue, right = discard / rank clue), Shift + click = also OK, hover preview (what each click would do; L/R tags on the cards a clue would touch), H hint (site-style arrow: P/D for play/discard, clue arrows for clues), S skip, Backspace undo, T throw-away, N note, `?` help. The log marks your turns once passed: ✓ (same as the real move), ≈✓ (real move was in "also OK") or "you: …" |
| Lobby | "Label" section: labeller name (remembered in the browser), start a session, resume unfinished ones. Sessions are listed without game IDs |
| `bundle.py` | Adds `log_anon` (log lines with Alice, Bob, … by seat) |

### Decisions made while building

1. **Clicking a card doesn't advance.** It records the choice (a blue tag on the card); → reveals the real
   move. That's the flow in §4.1 and keeps changing your mind and undo before the reveal free of
   hindsight. Making the click advance straight away (more like the live game) is a small change in
   `label.ts` if labelling feels slow.
2. **Notes and the throw-away mark are their own events**, not fields on the label (they can be set on any
   turn). §8 updated.
3. **The browser gets the label view on every call** (no incremental updates). It leaves out `history`
   and `unseen`, so it stays small.

### Not done / known gaps

- Undoing a label after the turn was passed leaves that turn unlabelled; the session can still be finished.
  No count of unlabelled turns is shown.
- The site flashes the clue counter when you try to discard at 8 clues; we show a message instead.
- No card notes or empathy (phase 5), no game browser (phase 4).
- Labels live in `review/labels/` (not under `build/`, since they're not regenerable). Not in `.gitignore`;
  decide whether labels are committed once the repo is set up.

### Next: phase 4

Checks panel, game browser.

---

## 2026-09-23 · Review tool, phase 2: Inspect viewer ✅

**Done when** it reproduces screenshots 1–3. It does: headless-Chrome screenshots of 78921 turn 4,
78921 turn 10 and 78822 turn 6 (seen from harikari.live) match the site's screen element by element (log
lines, hands, clue borders, clue arrows, stacks, discard pile, deck count, Turn/Score/Clues, strikes,
pace).

```bash
bash review/run.sh     # then open http://127.0.0.1:8765/
```

### What was built

| Piece | What it does |
|---|---|
| `review/web/` | The viewer. The site's default row layout, positioned with the fractions from hanab.live's `drawUI.ts`/`drawHands.ts`/`drawReplayArea.ts`. Card faces, stack bases, deck back and pips come from hanab.live's own `drawCards.ts` (imported unmodified); background, trash can, strike X and replay-button images from its `public/img` |
| Site features copied | Action log (last 8 lines; click for the full log), clue log with **hover highlight** (white = touched, red = missed) and click-to-jump, clue arrows for the previous clue, orange border on clued cards, active player's box and bold name, replay bar and buttons, ← → `[` `]` Home End, future strikes shown faded (as in the site's replays), "Score x / max" with the reachable max |
| Inspect additions | "View from" any seat (that seat's hand on top, as on the site); **own cards hidden** (`O`), which shows exactly the seat's `obs`: clue-only knowledge as white rank cards / suit cards / gray cards with remaining ranks and suit pips; card tooltip (ID, identity, slot, drawn/touched turns, knowledge, status); checks panel; JSON panel (DecisionRecord, seat view, raw action); "Open on site ↗" |
| `review/server/serve.py` | Local server: `/api/games`, `/api/games/<id>/inspect`, the built app |
| `review/setup.sh`, `review/run.sh` | Shared setup for the oracle and the web app (one `node_modules`, one vendor checkout); one-command start |
| Bundle | Positions now carry `max_score` (reachable max), checked against the oracle (`oracle_max_score`) |

Tested by driving headless Chrome over the DevTools protocol: hover highlights (P2 → red "1" entry,
the 1s → white, R3 drawn later → none), keys, clicking a clue entry (goes to the position just after the
clue), JSON and checks panels.

### Findings

1. **The screenshots use plain rank numbers** (the site's "stylized numbers" setting off) and a serif
   font: the site draws with "Verdana" and no fallback, so without Verdana installed the browser default
   is used. The viewer does the same, to look identical on the same machine.
2. **The site shows the reachable max score** ("Score 3 / 26" after both B2s are gone), same source as
   pace.

### Not done / known gaps

- **Efficiency** is shown as "–" (not computed).
- Hidden-card overlay is an approximation of the site's pips (suit pips + remaining ranks), not a port.
- No card notes, no "?" help icon, no timers. Name frames are CSS, not the site's exact bracket shape.
- Replay link `new.playhanabi.com/replay/<id>#<turn>` still untested on the site.

### Next: phase 3 (Label mode)

Random seat sessions, anonymisation, speedrun controls, hover preview, hint toggle, undo, saving labels to
local files.

---

## 2026-09-23 · Review tool, phase 1: oracle and bundle generator ✅

**Done when** games 78921 and 78822 match the official reducer on every turn. They do: 0 errors in
227 and 218 checks.

```bash
bash review/oracle/setup.sh                                          # once
python3 review/server/bundle.py examples/export_*.json     # writes review/build/bundles/<id>.json
```

### What was built

| Piece | What it does |
|---|---|
| `review/oracle/` | Replays an export through hanab.live's own `gameReducer` (Node) and prints the state before every turn. `setup.sh` builds the game package from the repo at `c1d970b` |
| `review/server/bundle.py` | Builds the game bundle (`review-tool.md` §6.1): site-worded log, clue log with touched/missed cards, every position, every seat's view at every position, DecisionRecords, and checks |
| Checks | Invariants (cards conserved, own cards hidden, `unseen` totals, history only grows, label ∈ `legal`) and the oracle comparison (score, clues, strikes, deck, pace, stacks, discard piles, hands in slot order, clues, log lines). Tested by corrupting the input: both corrupted cases are caught |
| `prototype/replay.py` (*since removed*, 2026-09-25) | `decision_record(..., viewer=)` for any seat's view; the final position (no label); pace as hanab.live computes it; clue events carry `missed` internally (removed from DecisionRecord output, so schema v0 is unchanged). The §8 worked example is byte-for-byte identical |
| `examples/export_78822.json` | Game 78822 (3 players, screenshot 3). Fetched with a single request |

### Findings

1. **npm `@hanabi-live/game` 0.0.8 is stale.** It differs from the repo at `c1d970b` by 8.6k diff lines,
   including logic and log wording. The oracle is built from source at the pinned commit.
2. **Pace uses the reachable max score.** hanab.live computes `score + deck + players − max`, where
   `max` drops when a suit can no longer be finished, and shows nothing once the deck is empty. Game
   78822 turn 10: +19, not +15. The prototype used 30. Fixed; noted under `representation.md` Q4.
3. **new.playhanabi.com runs a different hanab.live version.** The site prints "fails to play Yellow 1
   from slot #4 (clued)" (screenshot 2); `c1d970b` prints no suffix. Game state still matches
   everywhere. Noted under `representation.md` Q10.
4. **The official reducer doesn't decide when a game ends.** hanab.live's server does, so the oracle
   can't check end conditions. Game 78822's last move leaves Blue unfinishable (both B2s gone) and the
   game ends there, which fits the All or Nothing "fail" rule (`representation.md` §4).
5. **The row layout puts your own hand on top** (`drawHands.ts:163`), and hovering a card highlights
   the clue log entries that touched it (white) or missed it (red).

### Unverified assumptions (check against the site)

- **Wording of failed plays and blind plays.** We print " (critical)" > " (clued)" > nothing for failed
  plays (the discard rules), and " (blind)" for an unclued successful play. Only "(clued)" on a failed
  play has been seen on the site. **To check:** the last log line of game 78822 on the site. It's a
  misplay of the last B2, which was clued, and we print "(critical)".
- **Replay URL** `new.playhanabi.com/replay/<id>#<turn>`: taken from the source, not tried on the site.

### Next: phase 2 (Inspect mode, read-only)

Row layout, card art ported from `drawCards.ts`/`drawPipFunctions.ts`, action log, clue log with hover
highlight, stacks, discard pile, counters, replay keys, site link. **Done when** it reproduces
screenshots 1–3.

---

## 2026-09-23 · Design

- `representation.md` v0 draft: GameRecord → engine → DecisionRecord; prototype and worked example
  (game 78921, turn 4).
- `review-tool.md` v0 draft: Label and Inspect modes, speedrun controls, one seat per session,
  anonymised names, hint toggle, clue-only knowledge display, random (game, seat) sampling.
