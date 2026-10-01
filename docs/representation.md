# Hanabi decision representation: design v0 (draft)

*Status: draft for discussion · 2026-09-23 (updated the same day: live websocket stream, event-log GameRecord;
engine implemented in `hanabi_data/`, see §5.1) · 2026-09-26: model input format decided (§8) · 2026-09-30: trajectory adapter built (§8.8)*

This document defines how a recorded Hanabi game becomes training examples. Each decision is
everything the acting player could know at that moment, plus the move they actually made. The goal is
an input rich enough to choose the best move from it alone (a Markovian state), without hardcoding any
conventions. Models train on whole games seen from one seat (trajectories, §8). The input for one decision
is a prefix of such a trajectory.

---

## 1. Scope

**In scope (this repo)**
- A **game record**: an event log (§6). It is the hand-off format for both recorded games (exports) and
  live games (the websocket stream a seated player receives, §3.4). In a live game the player's own
  cards are unknown, so the record allows unknown identities.
- **Converters** from an export and from a captured live stream into a game record.
- A deterministic **rules engine** that replays a game record turn by turn.
- A **decision record**: one per turn, seen from the acting player's point of view, with the move
  actually made (the label) and metadata for filtering.
- A **trajectory**: the model input. It covers a whole game seen from one seat, with a frame for every
  turn, stored as integer-coded numpy arrays (§8).

All five are implemented in the `hanabi_data/` package (§5.1). Trajectories since 2026-09-30.

**Deferred**
- Models and training (a separate repo). They turn trajectories into tokens or tensors (§8.6).
- Label filtering policy (see Q1).
- The live client: connecting to the websocket, and sending moves (a separate repo, later; see Q11).
- Screenshot parsing: now only a fallback, since the websocket stream gives the game state as JSON.
- Bulk downloading: waits for the server owner's permission. Until then, a hand-picked sample of 10 games
  (`target_games.txt`) was fetched with `hanabi_data/download.py` into `data/exports/` (§3.6).

---

## 2. Decisions so far

| Topic | Decision |
|---|---|
| Data source | JSON exports from **new.playhanabi.com**, not hanab.live (a separate database with its own game IDs) |
| Variants | "6 Suits" (6 suits, max score 30) and possibly "No Variant" (5 suits, max score 25). No variants with special rules |
| Player counts | 2–5. The group has no 6-player games |
| Result | **A game is won only at the variant's max score** (30 with 6 suits, 25 with 5). Anything less is a loss, whatever the options. Discarding every copy of a card that's still needed (both copies of a 2–4, or a 5) makes the max score unreachable, so the game is lost at that point |
| Game options | Most games are **All or Nothing**, and some are **speedrun** games. Neither changes how the group plays or how a result counts, so neither is model input. The engine still reads both, because they change when the game ends and what score the server records (§4). They're also kept for accounting |
| Time | Games are timed (settings in seconds), but time is **not** part of the representation. Timer options are kept in the stored export only |
| Players | Several players' histories, deduplicated by game ID |
| Conventions | One shared set within the group, changing over time. **Not** hardcoded; the model learns them from history |
| Hand-off format | An **event log** GameRecord (§6). Exports and live streams both convert into it. Unknown identities are `null`, and every clue stores the cards it touched |
| Live game state | Read from the game's **websocket stream** as the seated player receives it (§3.4), not from screenshots |
| Formats | Verbose JSON for game records and decision records (§6, §7). The model reads **trajectories** (§8), which are derived from game records |
| Models (2026-09-26) | **Trained from scratch.** This covers sequence models with their own vocabulary (not a pretrained LLM's tokenizer) as well as recurrent and other tensor models. Feeding the text rendering to an off-the-shelf LLM is a side experiment, not the main effort |
| Model input (2026-09-26) | One **trajectory** per (game, viewer seat), with a **frame for every turn**, whoever acts, plus the action taken at each turn (§8). Token and tensor models both read it |
| Numeric storage (2026-09-26) | **numpy**. Trajectories are stored as integer-coded arrays (§8.5). Converting them to torch tensors is left to the training code. numpy is a dependency of `hanabi_data` and may be used freely, tests included |
| JSON → arrays (2026-09-26) | An **adapter** (§8.8) translates the structured JSON records (exported games through their DecisionRecords, and the labeling tool's labels) into the dense arrays, and model outputs back into moves |
| Code | The schema, converters, engine and decision records are one Python package, `hanabi_data/`, in this repo (provisional answer to Q9) |
| Raw data | Exports are cached in `data/exports/export_<id>.json`, unchanged. **Not committed** (`.gitignore`); they can be downloaded again |

---

## 3. Data source

### 3.1 Endpoints (checked 2026-09-23)

| Endpoint on `new.playhanabi.com` | Status | Returns |
|---|---|---|
| `/export/<gameID>` | ✅ works, **finished games only** | `id`, `players`, `deck`, `actions`, `options`, `seed` |
| `/history/<player>` | ✅ works | One HTML page listing **every** game (ID, player count, score, variant, date/time, players (sorted by name, not seat order), "Other Scores" = the number of games played on the same seed, **this one included**: never below 1). Times are end times, in UTC. 11.6 MB / 16,731 rows for pour1out4bga |
| `/game/<tableID>`, `/game/<tableID>/shadow/<seat>` | ✅ (logged in) | The live game client. Its game state arrives over the websocket (§3.4) |
| `/api/v1/history*`, `/api/v1/variants/*` | ❌ 404 | These exist only on hanab.live |

- **Game IDs are unique across the whole server**: one increasing sequence shared by every group of players.
- **A game gets its game ID only when it ends.** The server writes the database row at game end
  (`game_end.go:69`), and `/export` reads only from the database. A running game has a **table ID**, which
  is a different numbering: live table 43267 became game 78922, and `/export/43267` returns an unrelated
  old game.
- **No rate-limit headers and no `robots.txt`.** It's a small nginx server, so collection must be throttled
  (about 1 request per second, one connection) and cached permanently, since finished games never change.
- **harikari.live's history (fetched once, 2026-09-30; saved in `data/history/`):** 25,412 games, IDs 2447 (2021-09-17)
  to 79136 (2026-09-28). Target set (6 Suits or No Variant, 2–5 players): **13,975** exports: 6 Suits 11,405
  (2p 3,652 · 3p 5,794 · 4p 1,760 · 5p 199), No Variant 2,570. The page's time is the end time only; the median gap
  between consecutive games is 30 s, so most deals are abandoned quickly. Parsed by `listing.py` (§5.1) into
  `data/history/listing.jsonl`. All 11 of the 12 games on hand that appear there pass the listing checks
  (78663 isn't in harikari.live's history).
- **pour1out4bga's 6-suit games:** 2p 2,432 · 3p 5,070 · 4p 1,788 · 5p 199. Of these, 9,084 score 0 and
  285 score 30.

### 3.2 Export format

```jsonc
{
  "id": 78921,
  "players": ["pour1out4bga", "harikari.live"],   // seat order; seat 0 moves first unless options.startingPlayer is set
  "deck": [{"suitIndex": 1, "rank": 1}, ...],     // the full shuffled deck; the array index is the card's permanent ID
  "actions": [{"type": 3, "target": 1, "value": 1}, ...],
  "options": {"variant": "6 Suits", "timed": true, "timeBase": 5, "timePerTurn": 1, "allOrNothing": true},
  "seed": "p2v1s4001"
}
```

| `type` | Meaning | `target` | `value` |
|---|---|---|---|
| 0 | play | card ID (deck index) | — |
| 1 | discard | card ID | — |
| 2 | colour clue | seat index of the recipient | suit index |
| 3 | rank clue | seat index of the recipient | rank 1–5 |
| 4 | end game (server-generated: termination, timeout, …) | seat index | end-condition code |
| 5 | end game by vote | — | — |

Types 4 and 5 are defined in the server source (§4). I haven't yet seen them in our exports. **A
strikeout does not add an end action**: game 78922 ended on its 3rd strike, and its export stops at the
misplay. The engine has to detect rule-based endings itself.

Suit indices: `0 R · 1 Y · 2 G · 3 B · 4 P · 5 T`. The first five are the same in 5- and 6-suit games.

`options` lists only settings that differ from the default. Clue actions do **not** record which cards
they touched; the engine works that out by replaying the game.

### 3.3 How the deal and slots work (checked against screenshots 1–2 of game 78921)

- Cards are dealt in deck order: seat 0 gets cards 0–4, seat 1 gets 5–9, and so on (hand sizes in §4).
- A new card goes into **slot 1** (the leftmost), so slot numbers shift right as cards are drawn. In screenshot 1,
  "plays Yellow 1 from slot #5" is card 0, the first card dealt.
- Screenshot 1 (UI "Turn 4") is the position after 3 actions: score 1, 6 clues, 49 cards in the deck, pace +22.
  The replay reproduces all of these.

### 3.4 Live games: the websocket stream (checked on table 43267 = game 78922)

The game client gets its state over the websocket as JSON, not as pixels. Each client receives:
- **`gameActionList`**: every event so far. It's sent on joining, and again on every page reload
  (`command_get_game_info_2.go:65`), so a client that joins late or reconnects still gets the full history.
- **`gameAction`**: one event per update after that (`session_notify.go:227`).
- **`init`**, on joining: `playerNames`, `ourPlayerIndex`, `options`, … and the game's `seed` (see the
  warning below).

| Event | Fields | Notes |
|---|---|---|
| `draw` | `playerIndex`, `order`, `suitIndex`, `rank` | `order` is the deck index, the **same card ID as in the export**. The receiver's own draws arrive as `-1 / -1` |
| `clue` | `giver`, `target`, `clue {type, value}`, `list`, `turn` | `clue.type` 0 = colour, 1 = rank (export action type = 2 + `clue.type`). **`list` = the IDs of the touched cards**, sorted by ID, not by slot |
| `play` | `playerIndex`, `order`, `suitIndex`, `rank` | A successful play. Reveals the card |
| `discard` | `playerIndex`, `order`, `suitIndex`, `rank`, `failed` | Reveals the card. **A misplay is a `strike` event followed by a `discard` with `failed: true`**, not a `play` |
| `strike` | `num`, `turn`, `order` | |
| `status` | `clues`, `score`, `maxScore` | Sent after every move. `maxScore` is the highest score **still reachable**: it dropped from 25 to 24 when P5 was discarded |
| `turn` | `num`, `currentPlayerIndex` | `num` counts from 0, so the UI's "Turn N" is `num + 1` |
| `gameOver` | `endCondition`, `playerIndex` | End-condition codes as in §4 |
| `playerTimes` | | Ignored |

Events carry no slot numbers. Slots follow from the order in which cards were drawn, as for exports.

**Who sees what** (`actions_scrub.go`):

| Receiver | Sees |
|---|---|
| A seated player | Everything except their own unrevealed cards. Their own cards are revealed when they play or discard them |
| A spectator shadowing a seat (`/shadow/<seat>`) | The same as that player |
| Any other spectator | Every drawn card. Undrawn cards stay hidden |
| Anyone, **after the game ends** | Everything: the table becomes a replay, and a reload sends the list unhidden (`command_get_game_info_2.go:52`) |

**The bot must use the seated player's view.** An unshadowed spectator sees the bot's own cards. Using that
would be cheating, and it would also give the model input it never saw in training.

**Warning: `init` contains the seed during a live game.** The deck is a deterministic shuffle of the seed
(`http_export.go`), and the same seeds are played by many groups (§3.5). So for a seed that has been played
before, every hidden card can be looked up in a public export. The live client must drop the seed, and
nothing seed-derived may reach the model.

**Verified end to end.** Test game 43267 (2 players, No Variant, not All or Nothing) was captured from
seat 0 (harikari.live) at four points. After the game it was compared with `/export/78922`:
- All 15 card identities that appear in the stream (cards 0–14) match the export's `deck`.
- Converting the events one for one reproduces the export's 10 `actions` exactly: clue → type 2 + `clue.type`,
  discard → 1, `strike` + failed `discard` → 0.
- Replaying the stream, the engine's list of touched cards matches `list` for every clue to the visible
  hand, and the clue count and score match all 9 `status` events.
- The captures are in `examples/live_43267_player0_*.txt`. The last one was taken after the
  game ended, so it is unhidden.

Not yet seen in a capture: a successful `play`, the `init` message, a 3–5 player game, and an All or
Nothing game.

### 3.5 Seeds repeat, and so do decks

A seed such as `p2v0s713` fixes the deck. The server picks a seed the players at the table haven't played
before, but other players will have played it: game 78922's seed shows "Other Scores: 8". So the dataset
will contain **several games with the same deck**, played by different groups of players. Two
consequences:
- **Split training and test data by seed**, not by game. Otherwise the model can learn to recognise decks
  it has already seen and infer hidden cards from them (Q12). Fixed 2026-10-01: `listing.split_of(seed)`
  (§10).
- The seed must never be model input (§3.4).

### 3.6 The 10-game sample (2026-09-25)

Bulk downloading waits for permission (§1), so these 10 games were picked to cover the common cases.
`hanabi_data/download.py` fetched them politely: one request at a time, ≥5 s apart, cached, stopping at the
first error. All 10 pass `check` and match hanab.live's reducer (§10).

| Game | Players | Suits | Options besides timers | How it ends (engine) |
|---|---|---|---|---|
| 78738, 78742 | 2 | 6 | All or Nothing | Win, 30 |
| 78663 | 3 | 6 | All or Nothing, speedrun | Win, 30 |
| 12437 | 4 | 5 | speedrun | Win, 25 |
| 13398 | 3 | 5 | speedrun | Win, 25 |
| 78921 | 2 | 6 | All or Nothing | Strikeout (2), turn 11 |
| 78876 | 3 | 6 | All or Nothing | Strikeout (2), turn 13: misplays on turns 1, 10 and 13. Listed as a double discard in `target_games.txt`, but no card had all its copies discarded |
| 78919 | 2 | 6 | All or Nothing | All or Nothing fail (8), turn 34: the second T3 discarded |
| 78852 | 3 | 6 | All or Nothing | All or Nothing fail (8), turn 8: R5 discarded |
| 78916 | 2 | 6 | All or Nothing | Terminated by a player (4), after turn 8: the end action `{"type": 4, "target": 0, "value": 4}`, the first seen in a real export (a surrender) |

---

## 4. Rules the engine implements

The reference is the hanab.live source (`Hanabi-Live/hanabi-live`, commit `c1d970b`, 2026-09-18). **We
don't know which version new.playhanabi.com runs**, so replay checks (§10) have to confirm that it behaves
the same way.

| Rule | Value | Source |
|---|---|---|
| Deck | Each suit has 1×3, 2×2, 3×2, 4×2, 5×1 (10 cards). 60 cards with 6 suits, 50 with 5 | variant |
| Hand size | 2p: 5 · 3p: 5 · 4p: 4 · 5p: 4 | `server/src/constants.go:132` |
| Clue tokens | Start with 8, maximum 8. A clue costs 1 | `constants.go:91` |
| Discard | Gains 1 clue. **Not allowed at 8 clues** | `command_action.go:314` |
| Clue | Must touch ≥1 card in the recipient's hand. Needs ≥1 clue token | `command_action.go:405` |
| Play | Success adds to the stack; **playing a 5 gives +1 clue** (if below 8). A misplay goes to the discard pile and adds 1 strike | `game_player.go:170` |
| Strikes | The 3rd strike ends the game (`Strikeout`) | `game.go` `CheckEnd` |
| Starting player, hand size | `startingPlayer` (legacy, before April 2020) changes who moves first, not the deal. `oneExtraCard` / `oneLessCard` change the hand size by 1 | `options.go`, `game.go` `GetHandSize` |
| **All or Nothing: no final round** | Running out of deck does **not** start a final round. Play continues with shrinking hands | `game_player.go:277` |
| **All or Nothing: fail** | The game ends as soon as the max score is no longer reachable, i.e. every copy of some needed card is in the discard pile | `game.go:271` |
| **All or Nothing: softlock** | The game ends if the active player has no cards and no clue tokens | `game.go:279` |
| Win | Score = max score (30 or 25) | `game.go` `CheckEnd` |
| Recorded score | **Any ending other than Normal (1) records a score of 0**: strikeouts, All or Nothing fails, timeouts, terminations. This holds with or without All or Nothing | `game_end.go:17` |
| Order of end checks | After every action and draw, the turn advances, then: strikeout → speedrun fail → All or Nothing fail → All or Nothing softlock (the *new* active player has no cards and no clues) → final round over → score = reachable max score → no remaining card can be played. The first match ends the game | `command_action.go`, `game.go` `CheckEnd` |
| Moves with an empty hand | Only clues are possible (play and discard need a card) | follows from the rules above |
| Without All or Nothing | Drawing the last card sets the end turn: every player, including the one who drew it, gets one more turn. After a player's last turn, the cards in their hand count as unplayable, so the game can end early when every card still needed is in such a hand ("no remaining card can be played"). A Normal ending keeps its score, even below the maximum. Test game 78922 was such a game. The engine follows the game's options, so it supports both | `game_player.go` `DrawCard`, `command_action.go:150` (checked 2026-09-23) |

End conditions (`constants.go:40`): 1 Normal · 2 Strikeout · 3 Timeout · 4 TerminatedByPlayer ·
5 SpeedrunFail · 6 IdleTimeout · 8 AllOrNothingFail · 9 AllOrNothingSoftlock · 10 TerminatedByVote.
3, 4, 6 and 10 come from outside the rules (a timer, a player, a vote) and can happen on any turn; the
engine derives all the others itself.

**Not supported** (the converters raise `Unsupported`, so a bulk run can count and skip these games): any
variant other than "No Variant" and "6 Suits", and the options `cardCycle`, `deckPlays`, `emptyClues` and
`detrimentalCharacters`.

---

## 5. Architecture

```
 export JSON ──► converter ─┐                                                      ┌─►  DecisionRecord × turns     (JSON; review tool, filters, inspection)
                            ├─►  GameRecord  ──►  Engine (deterministic replay)  ──┤
 live stream ──► converter ─┘    event log;       knows the rules; rejects         └─►  Trajectory × viewer seats  ──►  tokens / tensors
 (websocket,                     stored, the      invalid games                         integer-coded numpy (§8);        in the training code
  player's view)                 source of truth                                        derived, versioned, cached
```

- **Store only game records.** Decision records and trajectories are derived from them. Regenerating them
  after a schema change needs no downloading. Trajectories are also cached on disk (§8.5), because
  replaying every game in Python each epoch would be too slow.
- **One engine for both paths.** Training (exports, full information) and live play (the stream, the
  player's view) go through the same GameRecord and the same engine. So the model sees exactly the same
  kind of input in both.
- Every derived record carries `schema` and the engine version.

### 5.1 Code: `hanabi_data/`

| Module | What it does |
|---|---|
| `rules.py` | §4 constants, `Rules` (from the site's options or a GameRecord's), `Unsupported`, `InvalidGame` |
| `engine.py` | `Engine.apply(event)` replays a GameRecord event by event and raises `InvalidGame` on anything the rules don't allow. It works with unknown identities (a player's view) and checks everything that view can see. `positions(record)` stops before every action |
| `convert_export.py` | `from_export(export, listing=, fetched_at=)`. Runs the engine while building the events, so it fills in `touched`, misplays and rule-based endings |
| `convert_live.py` | `parse_capture(text)`, `from_live(messages)`, `check_stream(record)` (§10) |
| `decision.py` | `decisions(record)`, `decision_record(record, turn, viewer=)`, `views(record, viewer)`, `summarize(record)` |
| `check.py` | `check_record(record)`: the §10 checks |
| `filters.py` | Label filters for the pretraining corpus (`label-filtering.md`): `FILTERS` with their status, `fired(engine, action)`, `firings(record, summary)` |
| `record.py` | `load_game(path, listing=)` (a GameRecord or a raw export; `listing` fills in its `/history` row), `player_view(record, seat)` |
| `listing.py` | Saved `/history` pages → listing rows (§6): `parse_history`, `merge` (several players' pages, one row per game), `load_listing`/`write_listing` (`.jsonl`), `in_scope`, `attach`. The split by seed: `seed_bucket`, `split_of` (§10) |
| `download.py` | `download(ids, out_dir)`: polite fetching of `/export/<id>` into a permanent cache. One request at a time, oldest first, at most 0.5 request/s by default (start to start, 1.0 hard ceiling), stops at the first error with no retries, resumes from the cache. More than 20 missing exports (a bulk run) need a **terms file** recording the owner's approval, which can also cap the rate and set a UTC window. `--listing` takes the targets from the listing (`in_scope`) and checks each export's players and seed against its row. `--max-requests` for a pilot run. Every request goes to `download_log.jsonl` |
| `trajectory.py` | The adapter (§8.8): `trajectory(record, viewer)`, `encode_move`/`decode_move`, `write_shards`/`read_shards`/`load_shard`, `render`. The only module that imports numpy, and `__init__.py` doesn't import it, so the rest of the package (and the review tool) runs without numpy. Later: the reference vocabulary |

```bash
python3 -m hanabi_data decision examples/export_78921.json 4 --pretty   # UI turn 4
python3 -m hanabi_data convert-export export.json > game.json
python3 -m hanabi_data convert-live capture.txt --players a,b
python3 -m hanabi_data decisions game.json > decisions.jsonl
python3 -m hanabi_data check game.json ...
python3 -m hanabi_data filters data/exports/*.json                                # moves the filters catch
python3 -m hanabi_data trajectory examples/export_78921.json --seat 1 --turn 4    # one seat's frames, readable
python3 -m hanabi_data trajectories data/exports/*.json --out data/trajectories/hanabi-trajectory-v0
python3 -m hanabi_data listing data/history/*.html --out data/history/listing.jsonl   # parse saved history pages
python3 -m hanabi_data check --listing data/history/listing.jsonl data/exports/*.json # any game command takes --listing
python3 -m pytest                                                                  # tests/
```

Python 3.9. numpy is the one dependency (since 2026-09-26, for trajectories), and may be used freely,
tests included. The review tool (`review/server/bundle.py`) uses the same package, so
hanab.live's reducer checks the engine on every position of every bundled game.

---

## 6. Layer 1: GameRecord

An **event log** of what happened, in order, as seen from one point of view. The point of view is either
full information (an export) or one seated player (a live stream). Seats are absolute here (seat 0 =
`players[0]`); decision records make them relative (§7).

Game 78922 as a full-information record, abridged:

```jsonc
{
  "schema": "hanabi-game/v0",
  "source": {"kind": "export", "server": "new.playhanabi.com", "game_id": 78922, "table_id": null,
             "fetched_at": "2026-09-24T00:40:00Z"},
  "view": null,                                   // null = full information; a seat index = that player's view
  "players": ["harikari.live", "d3m0n"],
  "options": {"variant": "No Variant", "suits": 5, "hand_size": 5, "all_or_nothing": false,
              "speedrun": false, "starting_player": 0},
  "events": [
    {"e": "draw", "seat": 0, "card": 0, "id": "P4"},  // one event per card; initial deal first
    ...
    {"e": "draw", "seat": 1, "card": 9, "id": "P2"},
    {"e": "clue", "by": 0, "to": 1, "kind": "rank", "value": 5, "touched": [7]},
    {"e": "clue", "by": 1, "to": 0, "kind": "color", "value": "Y", "touched": [1]},
    ...
    {"e": "discard", "by": 1, "card": 5, "id": "Y3"},
    {"e": "draw", "seat": 1, "card": 10, "id": "R5"},
    ...
    {"e": "play", "by": 1, "card": 9, "id": "P2", "ok": false},  // a misplay
    ...
    {"e": "end", "condition": 2, "seat": null}      // Strikeout
  ],
  "listing": {"game_id": 78922, "num_players": 2, "score": 0, "variant": "No Variant",
              "datetime": "2026-09-24T00:35:32Z", "players": ["d3m0n", "harikari.live"],
              "seed": "p2v0s713", "seed_games": 8},
  "raw": { /* the /export/<id> response, unchanged */ }
}
```

| Field | Contents |
|---|---|
| `source` | `kind` (`export` / `live`), `server`, `game_id` (`null` until the game ends), `table_id` (live only), `fetched_at` |
| `view` | `null` for full information, or the seat whose view this is |
| `players`, `options` | Seat order, and the settings the engine needs: `variant`, `suits`, `hand_size`, `all_or_nothing`, `speedrun`, `starting_player`. Everything else (timer settings, …) stays only in `raw` |
| `events[]` | `draw {seat, card, id}` · `clue {by, to, kind, value, touched}` · `play {by, card, id, ok}` · `discard {by, card, id}` · `end {condition, seat}` |
| `listing` | The `/history` row (exports only): `game_id`, `num_players`, `score`, `variant`, `datetime` (end time, UTC), `players` (sorted by name), `seed`, `seed_games` ("Other Scores", this game included). `null` when the game isn't in a saved history page, or when no listing was given (`--listing`) |
| `raw` | What the converter read, unchanged: the export, or the captured stream messages. **Never the seed for a live record** (§3.4) |

**Rules for `events`:**
- `id` is `"R3"`-style, or `null` if this view doesn't know it. In a player's view that's their own cards
  until they're played or discarded; the `play`/`discard` event then carries the identity.
- **`touched` is stored, not derived.** In a live game it is the only source for which of the player's own
  hidden cards a clue touched. For an export, the converter fills it in by replaying against the full
  deck. It is in **ascending card ID** order (oldest card first), as the stream's `list` sends it. Decision
  records list it in slot order instead (§7).
- Draws are in deck order (`card` = the next deck index), and the deal comes first: seat 0 gets the first
  `hand_size` cards, then seat 1, and so on.
- A misplay is `play` with `ok: false`. That is one event, although the stream sends `strike` + `discard`.
- `end` comes from the stream's `gameOver`, from an export end action (types 4/5), or from the engine
  when the rules end the game (strikeout, All or Nothing fail or softlock), since exports record no end
  action then. A live record of a game still in progress has no `end`. `seat` is the player who ended the
  game, when there is one: the player who timed out or terminated it, or the active player in an All or
  Nothing softlock (the server's `EndPlayer`); otherwise `null`.
- Undrawn cards are not in `events`. For exports they remain in `raw.deck`.

**What each converter does:**

| | Export → GameRecord | Live stream → GameRecord |
|---|---|---|
| Draws | Replays the deal and every draw from `deck` | Copies `draw` events; `-1/-1` becomes `null` |
| `touched` | Computed by replaying clues against the full deck | Copied from `list` |
| Misplays | Play action whose card isn't playable | `strike` + `discard {failed: true}` |
| `end` | Detected by the engine, or from type 4/5 | Copied from `gameOver` |
| `view` | Always `null` | The one seat whose draws arrive hidden; `null` if nothing is hidden (a finished game, reloaded). Must equal `init.ourPlayerIndex` |
| Messages | | `gameActionList` replaces everything so far (it's re-sent on every reload); `gameAction` adds one event; `init` gives the players and options, and its seed is dropped |
| Checks | `listing` agrees (score, game ID, players, variant, seed); the events equal a fresh conversion of `raw` | Clue count, score and `maxScore` = the `status` events; whose turn = the `turn` events; `touched` = the engine's result for every clue to a visible hand |

A full-information record can produce decision records for **every** seat. A player's-view record can
produce them only for that player, and only up to the current turn. For a game with both, the decision
records for that player must come out identical, apart from `private` and `meta`. That makes a direct test
of the live path (§10).

---

## 7. Layer 2: DecisionRecord

### 7.1 Principles

1. **The acting player's view only.** The actor's own current cards have `id: null`. Their true identities
   are in `private` and are **never** model input.
2. **Relative seats.** `seat 0` = the acting player, `seat 1` = the next player, and so on. Player names
   appear only in `meta`.
3. **Permanent card IDs everywhere.** Every card has a fixed ID (its position in the deck). History events
   refer to card IDs, not slots, because slots shift after every draw. Slots appear only where the UI or
   the label uses them.
4. **Identities are looked up once.** `obs.cards[id]` gives the identity the actor knows *now*: `null` for
   the actor's own cards in hand, otherwise known, including the actor's own cards after they're played or
   discarded. History events don't repeat identities.
5. **Rules-only knowledge, no conventions.** `know`, `status`, `unseen`, `gone` and `legal` follow purely
   from the rules. What clues *mean* under the group's conventions is left to the model, which sees the
   full history.
6. **The full history is always included.** Conventions assign meaning from context, so the history is
   never cut off.

### 7.2 Fields

| Field | Contents |
|---|---|
| `key` | `server`, `game_id`, `turn` (1-based; matches the UI's "Turn N": the number of actions already taken + 1); `table_id` too for a live record |
| `obs.rules` | `players`, `suits`, `hand_size`, `all_or_nothing`, `max_score` |
| `obs.board` | `turn`, `score`, `clues`, `strikes`, `deck` (cards left), `stacks` (top rank per suit), `discards` (card IDs, in order, including misplays), `pace` (as the UI shows it: `score + deck + players − reachable max score`, where a suit's reachable max stops below its first rank with every copy discarded; `null` once the deck is empty. See Q4), `gone` (identities whose copies are all on the stacks or in the discard pile) |
| `obs.cards` | An array indexed by card ID, covering every card drawn so far: `"R3"` or `null` (unknown to the actor) |
| `obs.hands[]` | One entry per relative seat, `slots` in UI order (slot 1 = newest). Each slot has: `card` (ID); `id` (identity or `null` for the actor); `status` (for others' cards: `playable` / `critical` / `trash` flags, `[]` for none); `drawn_t` (turn drawn, 0 = initial deal); `touched_t` (turns of every clue that touched it); `know` (what the holder can deduce from clues alone: `{"suits": "RYGBPT", "ranks": "2345"}`) |
| `obs.history[]` | Every public event since the deal: `deal` (card IDs per seat, slot order) · `clue` (`by`, `to`, `kind`, `value`, `touched` card IDs) · `play` (`by`, `card`, `slot`, `ok`, `drew`) · `discard` (`by`, `card`, `slot`, `drew`) |
| `obs.unseen` | Per suit, per rank 1–5: copies the actor cannot see anywhere (not in others' hands, the stacks or the discard pile). This is card counting from the actor's point of view |
| `obs.legal[]` | Every legal move (§4 rules). Empty once the game is over, and in another seat's view |
| `label` | The move actually made: `play`/`discard` with `slot` and `card` ID, or `clue` with `to` (relative seat), `kind`, `value` (suit letter or rank). `null` if no move follows (the end of the record) |
| `private` | `own_hand`: the actor's true cards, slot order. Only for extra training targets; **never an input**. `null` when the record doesn't know them (a player's view) |
| `meta` | Filtering fields, not model input (§7.3) |

**Live decision records.** Built from a player's-view GameRecord (§6), a decision record for the current
turn has the same `obs`, but `label` and `private` are `null`, and so are `meta`'s result fields.
`key.game_id` is `null` until the game ends, and `key.table_id` identifies the live table.

**Which positions get a record.** `decisions(record)` gives one per action of a full-information record,
from the actor's view. For a player's-view record it gives that player's turns only, plus the current
turn if it's theirs and the game is still running. `decision_record(record, turn, viewer=)` gives any
position from any seat's view (the review tool uses this); `label` and `legal` are filled in only for the
seat that is to act.

**Why `know` is a pair of sets.** Every clue narrows colour and rank independently, so what a holder
learns from clues about one card is exactly *allowed suits × allowed ranks*. Combining this with
`board.gone` gives the holder's clue-plus-public view. Deeper deductions (such as the holder counting the
cards they can see in other hands) are left to the model for v0 (see Q6).

### 7.3 Filtering metadata (`meta`)

Filtering is still to be discussed, so the record stores everything a filter might need. Fields that
aren't known yet are `null`: the result fields until the game has ended, the label fields when there is
no label.

| Field | Contents | Purpose |
|---|---|---|
| `players` | Names in relative seat order | Filter or weight by player; no player identity in `obs` |
| `actor` | The name of the player to act | |
| `datetime` | From `listing` (`null` without one) | Handle convention drift (weight recent games, or add an era marker) |
| `seed` | The export's seed; `null` for live records | Split train/test by deck (§3.5) |
| `end_condition` | §4 code, derived by the engine | Filter by game result |
| `final_score` | As recorded: 0 unless `end_condition` is Normal | |
| `won` | Normal ending at the variant's max score | |
| `total_turns` | Actions in the game | |
| `turns_to_end` | Actions after this one (the last move has 0) | Find moves close to a loss |
| `label_effect` | Flags: `misplay`; `lost_critical` (the reachable max score fell); `ended_game` (the rules ended the game right after it) | Flag moves that are probably mistakes |
| `misplay_knowable` | A misplay of a card whose clue knowledge (`know`), minus identities in `gone`, allows no playable identity | Flag likely deliberate strikeouts (`label-filtering.md` §5.1) |
| `misplay_run_to_end` | The move is part of the unbroken run of misplays (by anyone) that ended the game | |
| `end_misplay_run` | Length of that run for the whole game (0 if the game didn't end on a misplay) | |
| `filters` | The **agreed** label filters (`label-filtering.md` §3) that catch this move; `[]` if none | Drop provably bad labels from pretraining |
| `raw_action` | The export's action for this move | Trace back to the export |

---

## 8. Layer 3: Trajectory (model input)

*Decided 2026-09-26; built 2026-09-30 (`hanabi_data/trajectory.py`, `tests/test_trajectory.py`). The
tables below describe what is stored. Vocabulary details are open (Q14).*

This is what models train on. The decisions behind it (§2):
- **Models are trained from scratch.** Sequence models get their own vocabulary rather than an existing
  LLM's tokenizer. Recurrent and other tensor models read the same data. An off-the-shelf LLM may be tried
  on the text rendering (§8.6) as a side experiment.
- **One stored format serves every kind of model.** A frame is a fixed set of small categorical fields,
  stored as integer codes. Token IDs and one-hot or embedded tensors are both derived from those codes at
  training time (§8.6). The codes are stored; the model's view of them is not.
- **A frame for every turn**, whoever acts, not just the viewer's turns. Teammates' moves are the main
  source of information in Hanabi.
- **numpy for storage** (§8.5). The training code converts to torch.

### 8.1 Unit: one trajectory per (game, viewer seat)

A trajectory is a whole game as one seat saw it, step by step:

| Array | Length | Contents |
|---|---|---|
| frames | T + 1 | Frame *t* is the position before action *t*, as the viewer saw it at that moment. The last frame is the position after the last action, or the current position in a live game |
| steps | T | Step *t* is action *t* (UI turn *t* + 1), whoever made it, as the viewer saw it: the action fields (§8.3) plus labels and targets (§8.4) |

- **Where trajectories come from.** A full-information GameRecord gives one trajectory per seat. A
  player's-view record gives only that seat's, up to the current turn.
- **How decisions are read off it.** The input for the viewer's decision at step *t* is frames 0..*t* plus
  steps 0..*t*−1. That is the same information as that turn's DecisionRecord, so the Markovian goal still
  holds. A causal model gets every one of the viewer's decisions in one pass over the trajectory. Separate
  decision records would repeat the history once per decision.
- **Frames and actions are both needed.** The difference between two frames doesn't always show what
  happened. For example, a clue that touches only cards the holder already fully knows changes no `know`
  field. Conventions also depend on who gave a clue and which cards it touched and missed.
- **Seats are relative to the viewer**, and stay that way for the whole trajectory: seat 0 = the viewer,
  seat 1 = the next player, and so on. (A DecisionRecord makes seats relative to the actor, but at the
  viewer's own decisions the two are the same.)

### 8.2 Frame fields

Everything is padded to 5 seats × 5 slots × 6 suits. Every field fits in `uint8`, except `pace`. Two
codes are reserved: **`NONE` = 255** (no card in the slot, a seat, slot or suit this game doesn't have, or a
field that doesn't apply) and **`UNKNOWN` = 254** (the viewer's own cards). Every per-slot field of an empty
slot is `NONE`, so `suit` alone says whether a slot holds a card; `n_players`, `hand_size` and `n_suits`
give the padding masks.

| Group | Field | Shape | Values |
|---|---|---|---|
| Board | `actor` | — | Relative seat to act (in the last frame of a finished game: the seat that would be next) |
| | `score`, `clues`, `strikes`, `deck` | — | As in `obs.board`; `deck` = cards left |
| | `stacks` | [6] | Top rank per suit, 0–5; `NONE` for a suit the game doesn't have (also in `discards` and `unseen`) |
| | `discards` | [6, 5] | Copies of each (suit, rank) in the discard pile, misplays included, 0–3. The order is in the steps |
| | `pace` | — | `int8`, as in `obs.board`. `−128` = none (the deck is empty). Q4 |
| Each slot, [5 seats, 5 slots] in UI order (slot 1 = newest) | `suit`, `rank` | [5, 5] | The identity the viewer sees. Codes: `NONE` (no card in that slot, e.g. a hand shrinking under All or Nothing), `UNKNOWN` (the viewer's own cards, **always**), else the suit index 0–5 / rank 1–5 |
| | `know_suits`, `know_ranks` | [5, 5] | The holder's clue-only knowledge (`know`, §7.2) as bitmasks: 6 bits of suits, 5 bits of ranks. **Kept for every seat**, because what teammates know about their own cards matters as much as the cards |
| | `touches` | [5, 5] | How many clues have touched the card, capped at 7 |
| | `age` | [5, 5] | Turns since the card was drawn (*t* − `drawn_t`; 0 = drawn after the last action, or dealt at frame 0), capped at 253 |
| | `touch_age` | [5, 5] | Turns since a clue last touched the card (0 = the last action), capped at 253; `NONE` = never |
| | `card` | [5, 5] | Deck index (§7.1). Public, since draws are in deck order. It lets frames be joined to steps and to the GameRecord. Not meant as model input |
| Derived (rules only) | `status` | [5, 5] | `playable` (1) / `critical` (2) / `trash` (4) bits for cards the viewer sees; 0 for own cards. Exact, because the viewer sees these cards. The bits come from the card's **true identity, not from the holder's clue knowledge**: a teammate's B3 whose twin is discarded is `critical` even if its holder only knows "a 3". So for now they apply **only to teammates' hands**. Bits computed from clue knowledge, which would also cover the viewer's own cards, are a candidate under Q6. **`trash` = can never be played**: already played, or dead (a lower rank of its suit has every copy discarded). `critical` is never set on a trash card. This fixes Q13 for trajectories; DecisionRecords keep their narrower `trash` |
| | `unseen` | [6, 5] | As `obs.unseen` |

The derived fields follow from the other fields and the rules, so no convention creeps in (§7.1 principle
5). They're stored because they're cheap. Each model format decides whether it uses them.

Suit and rank are stored **as separate fields**, not as one `"G3"` code. A single card token is easy to
build from them (§8.6), and a suit permutation (augmentation, Q7) is then just one lookup table applied to
`suit`, the bits of `know_suits`, the per-suit arrays, and clue values.

### 8.3 Step fields: the action

| Field | Values |
|---|---|
| `actor` | Relative seat (relative to the viewer) |
| `type` | 0 play · 1 discard · 2 colour clue · 3 rank clue |
| `slot` | Play and discard: the slot 1–5 the card left, in the actor's hand before the action. Otherwise `NONE` |
| `target`, `value` | Clues: the recipient's relative seat (0 when the viewer is clued), and the suit index or rank. Otherwise `NONE` |
| `touched` | Clues: a 5-bit mask of the touched slots in the recipient's hand, before the action (bit 0 = slot 1). Missed slots are the zeros. 0 for plays and discards |
| `card_suit`, `card_rank` | Play and discard: the card's identity, which the action reveals to everyone. This is where the viewer's own cards become known. Otherwise `NONE` |
| `ok` | Play: 1 success, 0 misplay. Otherwise `NONE` |
| `drew` | 1 if a card was drawn after the action (0 once the deck is empty) |

**Actions are stored factored, and no action class numbering is stored** (decided 2026-09-26). The same
fields describe every move in every configuration. How a model outputs a move is part of the model, and
each option below is built from these fields and the legal masks (§8.4):

| Output | How it works | Handles variable players and hand sizes |
|---|---|---|
| Flat classes | One softmax over a fixed index, e.g. 5 plays + 5 discards + 4 targets × 11 clue values = 54, relative to the actor | Only through masking. Unused classes (teal in 5-suit games, slot 5 in 4-card hands, seats 3–4 in small games) are harmless once masked, but related classes share nothing: "clue 3 to the 4th next player" learns only from 5-player games |
| Factored | Choose the type, then the slot, or the target and then the clue value; the probability is the product | Sharing within each part: "rank 3" is the same output for every target |
| Scoring each candidate | Score each legal move from what it refers to: a play or discard from that slot's card (age, clues, knowledge), a clue from the target's hand and the cards it would touch | No padding; one scorer for every seat and hand size |
| Tokens | The move is written with the same tokens that describe actions in the input, and the policy is next-token prediction with masked decoding | The factored option applied to text, at no extra cost |

One thing to know about slot numbers: slots count from the newest card, so the oldest card is slot 5 in a
5-card hand but slot 4 in a 4-card hand, and hands shrink at the end of All or Nothing games. A model that
needs a card's position relative to the oldest end gets it from `age` and the number of cards in the hand.
Scoring each candidate avoids the question.

### 8.4 Step fields: labels and targets

These are **never** model input.

| Field | Shape | Contents |
|---|---|---|
| `is_label` | — | 1 where `actor` = 0, the viewer's own decision. The step's action fields (§8.3) are then the label |
| `legal_play`, `legal_discard` | [5] | Slots the viewer may play or discard |
| `legal_clue` | [4, 11] | Clues the viewer may give: the target's relative seat 1–4 × value (0–5 a colour, 6–10 rank 1–5) |
| `effect` | — | Bits as in `meta.label_effect`: misplay (1), lost_critical (2), ended_game (4), plus `misplay_run_to_end` (8). Filled for **every** step, teammates' too (from the game summary); on the viewer's steps it equals the DecisionRecord's `meta`. Bit 8 knows how the game ended, so it is the one field a prefix may disagree on (§8.7) |
| `filters` | — | Bitmask of the agreed filters that catch the move (`label-filtering.md` §3); bit *i* = `FILTERS[i]`. The viewer's steps only, like `meta.filters` |
| `own_suit`, `own_rank` | [5] per frame | The viewer's true hand (`private`, §7.2). Only in full-information records (`has_private`). Used as an auxiliary belief target: predicting your own cards |

The legal masks are filled on the viewer's steps only, and are zeros elsewhere. The viewer can't know which
clues a teammate could legally give them, because that depends on the viewer's hidden cards. They're stored
in this structured shape so that storage fixes no action numbering. Flat, factored and token masks are all
simple reshapes of them.

Steps where a teammate acts have no label for the viewer, but their action fields can still be used as an
auxiliary target (predicting teammates' moves). Game-level results (`end_condition`, `final_score`, `won`,
`total_turns`) are stored per trajectory, as value targets and for filtering.

### 8.5 Storage

```text
data/trajectories/hanabi-trajectory-v0/          (derived, not committed; regenerate from GameRecords)
  shard-00000.npz      np.savez_compressed, loaded with allow_pickle=False
    schema              "hanabi-trajectory/v0"
    traj_*   [N]        int64, −1 = unknown: viewer (absolute seat), n_players, n_suits, hand_size,
                        all_or_nothing, game_id, has_private, end_condition, final_score, won;
                        frame_start, step_start, n_steps
    frame_*  [ΣT+N, …]  every frame field of §8.2 (and own_suit, own_rank), trajectories concatenated
    step_*   [ΣT, …]    every step field of §8.3–8.4, concatenated
  index.jsonl          one line per trajectory: shard, row, server, game_id, table_id, viewer, players, seed, datetime
```

- **Ragged data is flattened with offsets.** Trajectory *i*'s frames are
  `frame_*[frame_start[i] : frame_start[i] + n_steps[i] + 1]`. That's one array per field, not one file per
  game, so loading is a slice.
- **Text metadata stays out of the arrays.** The seed, names and date go in `index.jsonl`, where filters
  and the train/test split by seed (§3.5) read them.
- **Versioned** as `hanabi-trajectory/v0` (the directory name and a `schema` entry in each shard), like the
  other layers. The vocabulary (§8.6) is versioned with it.
- **Size.** A frame is about 300 bytes (25 slots × 9 fields, plus the board, `discards` and `unseen`).
  pour1out4bga's 6-suit games alone make about 28k trajectories (games × seats) with roughly 60 steps each,
  so about 1.7 M frames: roughly 0.5 GB before compression, and much less after. So it fits in memory.
  *Measured 2026-09-30:* 307 bytes per frame uncompressed; the 12 games on hand (31 trajectories, 1,190
  frames) make a 47 KB shard, about 40 bytes per frame.
- **numpy is a dependency** of the package (`pyproject.toml`), and tests use it freely.

### 8.6 Serializations (derived in the training code, not stored)

- **Tokens (from scratch).** A reference vocabulary lives with the schema in `trajectory.py`, so training
  and the live client produce identical tokens. Each (field, value) pair is a token, plus a few structural
  tokens (frame start, action start, seat marker, `PAD`). A frame is written in a fixed field order with
  absent seats dropped, followed by its step's action tokens. A card can be a single fused token (30
  identities + `UNKNOWN` + `EMPTY`) followed by its knowledge tokens. For illustration only (the details are
  Q14), seat 1's hand in the §9 example might start
  `<S1> <R3> <k:RYGBPT> <k:12345> <P2> <k:RYGBPT> <k:2345> <T1> <k:RYGBPT> <k:1> <c1> …`.
  Estimate: about 70 tokens per frame with 2 players and 130 with 5, so a whole game is roughly 5k–10k
  tokens.
- **Tensors.** Each field goes through `one_hot` or an embedding, and the results are concatenated into
  one vector per frame, `[T+1, F]`, plus one per step. `torch.from_numpy` works on the arrays without
  copying.
- **Text.** A readable rendering of the same tokens, for debugging, and for trying off-the-shelf LLMs.

### 8.7 No hindsight: rules and tests

- **Frame *t* holds only what the viewer knew at *t*.** The viewer's own slots are always `UNKNOWN`. An own
  card's identity first appears in the step that plays or discards it. (A DecisionRecord's `obs.cards` is
  different: it shows what the actor knows *now*, including cards revealed since, which is right for one
  decision but would leak between frames.)
- **Never model input:** anything in §8.4, the seed, `game_id`, player names, the date (Q2) and deck
  indices (`card`).
- **Tests** (`tests/test_trajectory.py`, on the example games and every export in `data/exports/`):
  - *Prefix:* the trajectory of a record cut off after *k* actions equals the first *k* + 1 frames and *k*
    steps of the full trajectory (the game-level results and `effect` bit 8 aside). This shows no frame
    depends on the future. The live captures of game 78922 are checked the same way against the export.
  - *View:* seat *s*'s trajectory from a full-information record equals the one from
    `player_view(record, s)`, apart from the private targets. This is the §10 test of the live path, applied
    to trajectories.
  - *Round trip:* decoding a frame and its step (§8.8) gives back the DecisionRecord it was encoded from, as
    far as the frame holds it: hands, `know`, board, `unseen`, legal moves, the label. `status` agrees too,
    except that trajectory `trash` also covers dead cards.

### 8.8 Adapter: structured records → arrays

*Decided 2026-09-26; built 2026-09-30.* The pipeline keeps two kinds of data. JSON-like records are for storage, inspection
and the review tool (§6, §7, and the labeling tool's events). Dense arrays are for models (§8.2–8.5).
`trajectory.py` is the **adapter** between them. It holds no game rules of its own, beyond the few derived
fields.

**The frame's JSON form is a DecisionRecord from the viewer's seat.** `decision_record(record, t, viewer=s)`
already holds everything frame *t* needs, for any seat and any turn: hands, `know`, `drawn_t`, `touched_t`,
board, `unseen`, `status`, legal moves, label, `private` and `meta`. So the adapter reads the sequence of
viewer views and never replays games itself:

```text
GameRecord ──► views(record, viewer)                ──► encode ──► Trajectory (numpy arrays) ──► shards
               one replay, a DecisionRecord per
               position (T + 1) from one seat's view
labeling tool events ──► encode_move ──► step-shaped fields and masks (the human-label file, later)
model output ──► decode_move ──► a move in `legal` format (for the live client)
```

| Piece | What it does |
|---|---|
| `views(record, viewer)` (in `decision.py`) | Every position from one seat's view in a single replay (T + 1 records), as `decisions()` does for actors. With `private` for full-information records |
| `encode_frame(view)` | One DecisionRecord → the frame fields (§8.2). `age`, `touches` and `touch_age` come from `drawn_t` and `touched_t`, `actor` from `meta`, and dead cards (for `trash`) from the stacks and discards (`status_bits`) |
| `encode_step(before, after)` | Two consecutive views → the step fields (§8.3, §8.4). The action is the history event `after` adds; its card IDs become slots using the hands in `before`, and the revealed identity comes from `after`. `trajectory` fills in `effect` from the game summary |
| `encode_move(move, view, seat=)` | Any move, in any of the three forms the repo uses, → factored fields and a position in the legal-mask shape (§8.4), or no position when a teammate moves. The forms are: a GameRecord event (absolute seats, card IDs), a DecisionRecord `label`/`legal[]` entry (relative seats, slots), and a labeling-tool `choice` or `also_ok` entry (absolute `to_seat`, card IDs). The absolute forms need `seat`, the view's absolute seat, since a DecisionRecord doesn't hold it. Used for labels, legal masks and human labels alike |
| `decode_move(fields, view)` | The inverse: factored fields → a move in `legal` format, refused if it isn't legal in the view. The live client uses it to turn model output into a move to send. `decode_legal(steps, t)` does the same for a step's masks |
| `trajectory(record, viewer)` | `views` → `encode_*` → a `Trajectory` (named numpy arrays, §8.1–8.4) |
| `write_shards` / `read_shards` / `load_shard` | §8.5. `load_shard` returns a shard's arrays as stored, for training code |
| `render(traj, t)` | A readable printout of frame *t* and step *t*, for checking by eye and in tests (`decode_frame` turns a frame back into DecisionRecord terms) |
| CLI | `trajectory GAME --seat S [--turn N]` prints frames; `trajectories GAMES… --out DIR` writes shards (every seat of a full record; a game that fails to convert is reported and skipped) |

**First implementation (done 2026-09-30):** the pieces above, the §8.7 tests, and the CLI commands.
**Later:** the token vocabulary (Q14), and a human-label file built with `encode_move`, keyed by
(`game_id`, `viewer`, step) with `also_ok` stored as masks in the legal shape.

Building every frame through a full DecisionRecord repeats the history list at each position, so it's
O(T²) per trajectory. With T below about 100 that's fine: trajectories are built once and cached (§8.5).

---

## 9. Worked example: game 78921, turn 4 (screenshot 1)

harikari.live (seat 0) is to act. pour1out4bga (seat 1) holds R3 P2 T1 P1 T1. Both players have received a
1s clue, and Y1 has been played. harikari.live's actual move was a **Purple clue** to pour1out4bga,
touching P2 and P1.

This record was produced by the engine, not written by hand. Every number shown in screenshot 1 matches
it, and a test (`tests/test_worked_example.py`) keeps this block, the file
`examples/decision_78921_turn4.json` and the engine's output identical. To regenerate it:

```bash
python3 -m hanabi_data decision examples/export_78921.json 4 --pretty
```

```json
{
  "schema": "hanabi-decision/v0",
  "key": {"server": "new.playhanabi.com", "game_id": 78921, "turn": 4},
  "obs": {
    "rules": {"players": 2, "suits": 6, "hand_size": 5, "all_or_nothing": true, "max_score": 30},
    "board": {
      "turn": 4,
      "score": 1,
      "clues": 6,
      "strikes": 0,
      "deck": 49,
      "stacks": {"R": 0, "Y": 1, "G": 0, "B": 0, "P": 0, "T": 0},
      "discards": [],
      "pace": 22,
      "gone": []
    },
    "cards": ["Y1", "T1", "P1", "T1", "P2", null, null, null, null, null, "R3"],
    "hands": [
      {
        "seat": 0,
        "slots": [
          {
            "slot": 1,
            "card": 9,
            "id": null,
            "status": null,
            "drawn_t": 0,
            "touched_t": [],
            "know": {"suits": "RYGBPT", "ranks": "2345"}
          },
          {
            "slot": 2,
            "card": 8,
            "id": null,
            "status": null,
            "drawn_t": 0,
            "touched_t": [],
            "know": {"suits": "RYGBPT", "ranks": "2345"}
          },
          {
            "slot": 3,
            "card": 7,
            "id": null,
            "status": null,
            "drawn_t": 0,
            "touched_t": [1],
            "know": {"suits": "RYGBPT", "ranks": "1"}
          },
          {
            "slot": 4,
            "card": 6,
            "id": null,
            "status": null,
            "drawn_t": 0,
            "touched_t": [],
            "know": {"suits": "RYGBPT", "ranks": "2345"}
          },
          {
            "slot": 5,
            "card": 5,
            "id": null,
            "status": null,
            "drawn_t": 0,
            "touched_t": [1],
            "know": {"suits": "RYGBPT", "ranks": "1"}
          }
        ]
      },
      {
        "seat": 1,
        "slots": [
          {
            "slot": 1,
            "card": 10,
            "id": "R3",
            "status": [],
            "drawn_t": 3,
            "touched_t": [],
            "know": {"suits": "RYGBPT", "ranks": "12345"}
          },
          {
            "slot": 2,
            "card": 4,
            "id": "P2",
            "status": [],
            "drawn_t": 0,
            "touched_t": [],
            "know": {"suits": "RYGBPT", "ranks": "2345"}
          },
          {
            "slot": 3,
            "card": 3,
            "id": "T1",
            "status": ["playable"],
            "drawn_t": 0,
            "touched_t": [2],
            "know": {"suits": "RYGBPT", "ranks": "1"}
          },
          {
            "slot": 4,
            "card": 2,
            "id": "P1",
            "status": ["playable"],
            "drawn_t": 0,
            "touched_t": [2],
            "know": {"suits": "RYGBPT", "ranks": "1"}
          },
          {
            "slot": 5,
            "card": 1,
            "id": "T1",
            "status": ["playable"],
            "drawn_t": 0,
            "touched_t": [2],
            "know": {"suits": "RYGBPT", "ranks": "1"}
          }
        ]
      }
    ],
    "history": [
      {"t": 0, "e": "deal", "hands": {"1": [4, 3, 2, 1, 0], "0": [9, 8, 7, 6, 5]}},
      {"t": 1, "by": 1, "e": "clue", "to": 0, "kind": "rank", "value": 1, "touched": [7, 5]},
      {"t": 2, "by": 0, "e": "clue", "to": 1, "kind": "rank", "value": 1, "touched": [3, 2, 1, 0]},
      {"t": 3, "by": 1, "e": "play", "card": 0, "slot": 5, "ok": true, "drew": 10}
    ],
    "unseen": {
      "R": [3, 2, 1, 2, 1],
      "Y": [2, 2, 2, 2, 1],
      "G": [3, 2, 2, 2, 1],
      "B": [3, 2, 2, 2, 1],
      "P": [2, 1, 2, 2, 1],
      "T": [1, 2, 2, 2, 1]
    },
    "legal": [
      {"type": "play", "slot": 1},
      {"type": "play", "slot": 2},
      {"type": "play", "slot": 3},
      {"type": "play", "slot": 4},
      {"type": "play", "slot": 5},
      {"type": "discard", "slot": 1},
      {"type": "discard", "slot": 2},
      {"type": "discard", "slot": 3},
      {"type": "discard", "slot": 4},
      {"type": "discard", "slot": 5},
      {"type": "clue", "to": 1, "kind": "color", "value": "R"},
      {"type": "clue", "to": 1, "kind": "color", "value": "P"},
      {"type": "clue", "to": 1, "kind": "color", "value": "T"},
      {"type": "clue", "to": 1, "kind": "rank", "value": 1},
      {"type": "clue", "to": 1, "kind": "rank", "value": 2},
      {"type": "clue", "to": 1, "kind": "rank", "value": 3}
    ]
  },
  "label": {"type": "clue", "to": 1, "kind": "color", "value": "P"},
  "private": {"own_hand": ["G2", "P2", "Y1", "G3", "B1"]},
  "meta": {
    "players": ["harikari.live", "pour1out4bga"],
    "actor": "harikari.live",
    "datetime": null,
    "seed": "p2v1s4001",
    "end_condition": 2,
    "final_score": 0,
    "won": false,
    "total_turns": 11,
    "turns_to_end": 7,
    "label_effect": [],
    "misplay_knowable": false,
    "misplay_run_to_end": false,
    "end_misplay_run": 2,
    "filters": [],
    "raw_action": {"type": 2, "target": 0, "value": 4}
  }
}
```

Things to note:
- `cards[5..9]` are `null`: harikari.live's own hand. Their true identities are only in `private.own_hand`.
- Slots 3 and 5 of seat 0 have `know.ranks: "1"` and `touched_t: [1]`: the 1s clue from turn 1. The
  untouched slots have ranks `"2345"`; this "not a 1" information never appears on screen, but it matters.
- `unseen.T[0] = 1`: two T1s are visible in pour1out4bga's hand, so only one copy is unaccounted for.
- The label is also in `legal`. The history has 4 events. As compact JSON, `obs` is ~2.4 KB and the
  whole record ~3.0 KB.
- `meta` already knows how the game ends: a strikeout on turn 11 (`turns_to_end: 7`), whose last two
  misplays form the run to the end (`end_misplay_run: 2`, `label-filtering.md` §5.1).

---

## 10. Validation, size and storage

All of these are implemented: the engine raises `InvalidGame` while replaying, and `check_record` (or
`python3 -m hanabi_data check`) runs the rest.

**Checks for every game record** (reject the game on any failure):
- Every action is legal under §4 (the actor's turn, card in the actor's hand, clue to another seat,
  clue touches ≥1 card and exactly the cards the rules say, a clue token available, no discard at 8
  clues, a valid clue value), draws follow the deck order, and no identity has more copies than the deck.
- A play's recorded `ok` agrees with the stacks, and a recorded `end` agrees with the rules (or is one of
  the outside endings 3, 4, 6, 10).
- The engine's end state is consistent: it reaches a rule-based ending (win, strikeout, All or Nothing
  fail/softlock) or finishes with an explicit end action (type 4/5). Otherwise flag the game as
  truncated.
- The listing agrees with the record: final score, game ID, player count, players (sorted), variant, seed.
- Export records: the events equal a fresh conversion of `raw`.

**Extra checks for live records** (§6): the engine's clue count and score match every `status` event, its
reachable max score matches `status.maxScore`, whose turn it is matches every `turn` event, and its list
of touched cards matches `touched` for every clue to a visible hand. A clue to the player's own hidden
cards can only be checked for count (≥1 touched).

**Test of the live path.** For any game captured live and later exported, the player's-view record and
the export record must give identical `obs` for that player's turns. Game 78922 is the first such pair
(§3.4): all four captures pass (`tests/test_live.py`), and the capture taken after the game ended converts
to exactly the export's events. The same test runs on every synthetic game for every seat, with
`player_view` hiding that seat's draws.

**Tests** (`python3 -m pytest`, 63 tests):
- **Golden decisions:** every position of the three example games, from every seat, gives the same
  `obs`, `label`, `private` and `key` as `tests/data/golden_decisions.jsonl`: the output of the original
  prototype (checked against screenshots and hanab.live's reducer, since removed), with 78922's
  `all_or_nothing` corrected to `false`.
- **Endings the examples don't reach**, on synthetic games in `tests/data/`, found by a random
  full-information search:
  - wins for 2–5 players, 5 and 6 suits, with and without All or Nothing
  - under All or Nothing, play past the end of the deck, and a softlock
  - without it, the full final round, and one that ends early because nothing can be played
- **hanab.live's reducer** (`review/server/bundle.py`) agrees with the engine at every position of the
  examples and the synthetic games. It doesn't decide endings, so the tests check those against
  `game.go` directly.
- **Rejected input:** illegal moves, bad decks, unsupported options, tampered records, broken streams.

**Train/test split by seed** (§3.5, Q12). Every record carries its seed (`raw.seed` for exports) so the
split can group games by deck. Seeds stay out of `obs`. Fixed 2026-10-01 in `listing.py`: a seed's bucket
(0–99) is a salted SHA-256 of it, buckets 0–4 are `test`, 5–9 `valid` and the rest `train`. The split is
computed from the seed wherever it's needed (`meta.seed`, the trajectory index) rather than stored. Tests
freeze three seeds' buckets, so the split can't move silently. On harikari.live's 13,975 in-scope games:
test 665 · valid 650 · train 12,660.

**Size.** The worked example is ~3.0 KB (~2.4 KB of it `obs`). A late-game 4–5 player record, with ~80 history events and 20
slots, should come to about 10–15 KB of compact JSON. At ~9.5k games × ~60 decisions that is several GB
uncompressed, which is why §5 stores game records (~5 KB each, ≈50 MB in total) and derives decision
records on demand, with an optional cache. Trajectories are far more compact (about 300 bytes per frame,
~0.5 GB before compression for the same games, §8.5), so they are cached.

---

## 11. Label quality

Moved to **`label-filtering.md`**: the two training phases, the filtering decisions, the filters,
deliberate strikeouts (its §5.1), proposals, open questions and progress.

---

## 12. Open questions

1. **Label filtering policy.** Tracked in `label-filtering.md` (decisions §2, open questions §7).
2. **Convention drift.** Filter by date, weight toward recent games, or add a date/era marker to `obs`?
   *Data, 2026-10-01:* every listed game now has its end time (`meta.datetime`, the trajectory index). In-scope
   games per year: 2021 5 · 2022 4,124 · 2023 1,681 · 2024 1,887 · 2025 2,657 · 2026 3,621. Still open: when
   the conventions changed (the group could say), and what to do about it.
3. **Model format and token budget.** Which model reads this, and at what context length? That decides
   the compact format's size. JSON field names are a large share of the ~2.4 KB of `obs`.
   *Answered 2026-09-26:* models are trained from scratch: sequence models with their own vocabulary, and
   tensor models. Both read integer-coded numpy trajectories, one per (game, viewer seat), with a frame for
   every turn (§8). A whole game is roughly 5k–10k tokens (§8.6). What's left is in Q14.
4. **Pace under All or Nothing.** There is no final round, so the displayed pace (`score + deck + players
   − max`) doesn't measure a real limit. Keep it because players see it and may react to it, drop it, or
   replace it with a measure suited to All or Nothing?
   *Checked 2026-09-23:* hanab.live computes pace against the **reachable** max score, not 30 (game
   78822, turn 10: +19, where `score + deck + players − 30` gives +15), and shows nothing once the deck is
   empty. The engine does the same.
5. **5-suit games.** *Answered 2026-09-25:* include them. Whether a game is All or Nothing doesn't matter
   in practice: a game is won only at the max score (25 with 5 suits), whatever the options (§2). The
   two 5-suit games in the sample (12437, 13398) are not All or Nothing. Filtering note (Q1): without All
   or Nothing a game goes on after the max score has become unreachable, so the moves after that point
   belong to a game that is already lost (`label_effect.lost_critical` marks the move that lost it).
6. **Deeper knowledge features.** Should `know` also account for what the holder can see in other hands
   (per-holder card counting), or leave that to the model?
   *Candidate, 2026-09-30:* status bits computed from the holder's knowledge instead of the true identity
   (§8.2), so they also apply to the viewer's own cards. Take the identities `know` allows, minus those
   whose copies are all played or discarded. Then *certainly playable* = every remaining identity is
   playable, *possibly critical* = at least one is critical, and *certainly trash* = all are trash. These
   follow from the rules only. "Probably" (weighted by unseen copies or by conventions) is left to the
   model.
7. **Augmentation.** Suits play identical roles in these variants. Should training shuffle suit letters
   consistently across a record? Shuffling seats is not valid, because seat order matters.
   *Note 2026-09-26:* trajectories keep suit and rank as separate fields, so a permutation is a single
   lookup table (§8.2). Whether the group's conventions treat every suit alike is still to check.
8. **Player identity in `obs`.** Leave players anonymous (current plan), or add per-player tokens so the
   model can learn individual styles?
9. **Where the shared schema lives.** The live client repo (formerly the screenshot parser) and this repo
   both need the GameRecord definition and the live-stream converter. Proposal: keep both here, as a small
   package the client depends on, versioned by `schema`. The client then only captures messages and sends
   moves. *Provisionally done:* `hanabi_data/` (§5.1) has no dependencies and can be installed from this
   repo (`pyproject.toml`), so the client can import it. Say if it should move.
10. **Server version.** Confirm new.playhanabi.com follows the §4 rules by replaying a sample of games
    before any bulk collection.
    *Evidence so far (2026-09-23):* the site's log wording differs from hanab.live `c1d970b` ("fails to play
    … (clued)" on the site, no suffix at `c1d970b`), so the site runs a different, probably older, version.
    Game state matched the `c1d970b` reducer at every position of games 78921 and 78822.
    The live stream of game 78922 matched `c1d970b`'s message formats and its hiding rules (§3.4).
    The engine now implements §4 and agrees with the `c1d970b` reducer on every position of the three
    examples and five longer synthetic games (§10). What's still missing is a sample of real games,
    especially ones that run past the end of the deck.
11. **The live client.** Would the bot play from its own account or a player's? It needs a logged-in
    websocket connection either way. Are the server admins and the group OK with a bot connecting? Should
    it send moves over the websocket, or click in the browser?
12. **Train/test split by seed.** Same-seed games share a deck (§3.5). Split by seed (recommended), and
    check how often seeds repeat within our data once the listing is parsed. `meta.seed` carries it.
    *Answered 2026-10-01:* split by seed, fixed in `listing.split_of` (§10). Within harikari.live's 13,975
    in-scope games **no seed repeats**: the server picks a seed new to the table. 6,105 of them (44%) are on
    a seed that other tables also played, so repeats appear only if other players' games are added. The
    split covers that case already.
13. **`status` misses dead cards.** `trash` means "already played". A card above a rank whose copies are
    all discarded (e.g. B3 after both B2s are gone) can never be played either, but gets no flag, and
    `critical` can still be set on it. The engine keeps the original prototype's behaviour here, which
    `tests/data/golden_decisions.jsonl` freezes. Should `trash` also cover these cards? That would be
    `hanabi-decision/v1`. Under All or Nothing such a game ends at once, so it matters mainly for 5-suit
    games without All or Nothing.
    *Trajectories, 2026-09-26:* their `status.trash` means "can never be played" and covers dead cards
    (§8.2). A separate `dead` bit was considered and dropped: for a move, trash and dead cards are the same
    (never play them; discarding them is safe), and the difference, that a suit's max is capped, is
    already in `stacks` and `discards`. DecisionRecords are unchanged for now.
14. **Vocabulary details** (§8.6). These can wait until the first model trains, since none of them change
    what is stored:
    - One fused token per card (`<G3>`), or separate suit and rank tokens?
    - Drop absent seats and empty slots, or pad every frame to a fixed length (the position then
      identifies the field, with no marker tokens)?
    - Buckets for `age` and `touch_age`.
    - Which derived fields (`status`, `unseen`, `pace`) go into the tokens.
    - The output: flat classes, factored, scoring each candidate, or tokens (§8.3). This is a model choice,
      and different models can make different ones.
    - Every step has a frame (decided). If 5-player games turn out too long as tokens, a frame could be
      written as its changes from the previous frame.
