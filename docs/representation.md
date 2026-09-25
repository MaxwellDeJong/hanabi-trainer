# Hanabi decision representation: design v0 (draft)

*Status: draft for discussion · 2026-09-23 (updated the same day: live websocket stream, event-log GameRecord;
engine implemented in `hanabi_data/`, see §5.1)*

This document defines how a recorded Hanabi game becomes training examples. Each example is one decision:
everything the acting player could know at that moment, plus the move they actually made. The goal is
an input rich enough to choose the best move from it alone (a Markovian state), without hardcoding any
conventions.

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

All four are implemented in the `hanabi_data/` package (§5.1).

**Deferred**
- The compact format the model reads, and the token budget (see Q3).
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
| Formats | Verbose JSON for storage (this doc); a compact format for the model, derived from it later |
| Code | The schema, converters, engine and decision records are one Python package, `hanabi_data/`, in this repo (provisional answer to Q9) |
| Raw data | Exports are cached in `data/exports/export_<id>.json`, unchanged. **Not committed** (`.gitignore`); they can be downloaded again |

---

## 3. Data source

### 3.1 Endpoints (checked 2026-09-23)

| Endpoint on `new.playhanabi.com` | Status | Returns |
|---|---|---|
| `/export/<gameID>` | ✅ works, **finished games only** | `id`, `players`, `deck`, `actions`, `options`, `seed` |
| `/history/<player>` | ✅ works | One HTML page listing **every** game (ID, player count, score, variant, date/time, players, "Other Scores" = the number of other games played on the same seed). 11.6 MB / 16,731 rows for pour1out4bga |
| `/game/<tableID>`, `/game/<tableID>/shadow/<seat>` | ✅ (logged in) | The live game client. Its game state arrives over the websocket (§3.4) |
| `/api/v1/history*`, `/api/v1/variants/*` | ❌ 404 | These exist only on hanab.live |

- **Game IDs are unique across the whole server**: one increasing sequence shared by every group of players.
- **A game gets its game ID only when it ends.** The server writes the database row at game end
  (`game_end.go:69`), and `/export` reads only from the database. A running game has a **table ID**, which
  is a different numbering: live table 43267 became game 78922, and `/export/43267` returns an unrelated
  old game.
- **No rate-limit headers and no `robots.txt`.** It's a small nginx server, so collection must be throttled
  (about 1 request per second, one connection) and cached permanently, since finished games never change.
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
- The captures are in `prototype/examples/live_43267_player0_*.txt`. The last one was taken after the
  game ended, so it is unhidden.

Not yet seen in a capture: a successful `play`, the `init` message, a 3–5 player game, and an All or
Nothing game.

### 3.5 Seeds repeat, and so do decks

A seed such as `p2v0s713` fixes the deck. The server picks a seed the players at the table haven't played
before, but other players will have played it: game 78922's seed shows "Other Scores: 8". So the dataset
will contain **several games with the same deck**, played by different groups of players. Two
consequences:
- **Split training and test data by seed**, not by game. Otherwise the model can learn to recognise decks
  it has already seen and infer hidden cards from them (Q12).
- The seed must never be model input (§3.4).

### 3.6 The 10-game sample (2026-09-25)

Bulk downloading waits for permission (§1), so these 10 games were picked to cover the common cases.
`hanabi_data/download.py` fetched them politely: one request at a time, ≥5 s apart, cached, stopping at the
first error. All 10 pass `check` and match hanab.live's reducer (§9).

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
don't know which version new.playhanabi.com runs**, so replay checks (§9) have to confirm that it behaves
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
 export JSON ──► converter ─┐
                            ├─►  GameRecord  ──►  Engine (deterministic replay)  ──►  DecisionRecord × turns  ──►  model serializer(s)
 live stream ──► converter ─┘    event log;       knows the rules; rejects                derived, versioned;         deferred
 (websocket,                     stored, the      invalid games                           regenerated from records
  player's view)                 source of truth
```

- **Store only game records.** Decision records are derived from them. Regenerating them after a schema
  change needs no downloading.
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
| `convert_live.py` | `parse_capture(text)`, `from_live(messages)`, `check_stream(record)` (§9) |
| `decision.py` | `decisions(record)`, `decision_record(record, turn, viewer=)`, `summarize(record)` |
| `check.py` | `check_record(record)`: the §9 checks |
| `record.py` | `load_game(path)` (a GameRecord or a raw export), `player_view(record, seat)` |
| `download.py` | `download(ids, out_dir)`: polite fetching of `/export/<id>` into a permanent cache (one request at a time, ≥5 s apart by default, a cap per run, stops at the first error). For small hand-picked samples until bulk collection is approved |

```bash
python3 -m hanabi_data decision prototype/examples/export_78921.json 4 --pretty   # UI turn 4
python3 -m hanabi_data convert-export export.json > game.json
python3 -m hanabi_data convert-live capture.txt --players a,b
python3 -m hanabi_data decisions game.json > decisions.jsonl
python3 -m hanabi_data check game.json ...
python3 -m pytest                                                                  # tests/
```

Python 3.9, standard library only. The review tool (`review/server/bundle.py`) uses the same package, so
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
  "listing": {"datetime": "2026-09-24T00:35:32Z", "num_players": 2, "score": 0, "variant": "No Variant"},
  "raw": { /* the /export/<id> response, unchanged */ }
}
```

| Field | Contents |
|---|---|
| `source` | `kind` (`export` / `live`), `server`, `game_id` (`null` until the game ends), `table_id` (live only), `fetched_at` |
| `view` | `null` for full information, or the seat whose view this is |
| `players`, `options` | Seat order, and the settings the engine needs: `variant`, `suits`, `hand_size`, `all_or_nothing`, `speedrun`, `starting_player`. Everything else (timer settings, …) stays only in `raw` |
| `events[]` | `draw {seat, card, id}` · `clue {by, to, kind, value, touched}` · `play {by, card, id, ok}` · `discard {by, card, id}` · `end {condition, seat}` |
| `listing` | The `/history` row (exports only; `null` until the history-page parser exists) |
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
| Checks | Final score = `listing.score`; the events equal a fresh conversion of `raw` | Clue count, score and `maxScore` = the `status` events; whose turn = the `turn` events; `touched` = the engine's result for every clue to a visible hand |

A full-information record can produce decision records for **every** seat. A player's-view record can
produce them only for that player, and only up to the current turn. For a game with both, the decision
records for that player must come out identical, apart from `private` and `meta`. That makes a direct test
of the live path (§9).

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
| `datetime` | From `listing` (`null` until the history-page parser exists) | Handle convention drift (weight recent games, or add an era marker) |
| `seed` | The export's seed; `null` for live records | Split train/test by deck (§3.5) |
| `end_condition` | §4 code, derived by the engine | Filter by game result |
| `final_score` | As recorded: 0 unless `end_condition` is Normal | |
| `won` | Normal ending at the variant's max score | |
| `total_turns` | Actions in the game | |
| `turns_to_end` | Actions after this one (the last move has 0) | Find moves close to a loss |
| `label_effect` | Flags: `misplay`; `lost_critical` (the reachable max score fell); `ended_game` (the rules ended the game right after it) | Flag moves that are probably mistakes |
| `misplay_knowable` | A misplay of a card whose clue knowledge (`know`), minus identities in `gone`, allows no playable identity | Flag likely deliberate strikeouts (§10) |
| `misplay_run_to_end` | The move is part of the unbroken run of misplays (by anyone) that ended the game | |
| `end_misplay_run` | Length of that run for the whole game (0 if the game didn't end on a misplay) | |
| `raw_action` | The export's action for this move | Trace back to the export |

## 8. Worked example: game 78921, turn 4 (screenshot 1)

harikari.live (seat 0) is to act. pour1out4bga (seat 1) holds R3 P2 T1 P1 T1. Both players have received a
1s clue, and Y1 has been played. harikari.live's actual move was a **Purple clue** to pour1out4bga,
touching P2 and P1.

This record was produced by the engine, not written by hand. Every number shown in screenshot 1 matches
it, and a test (`tests/test_worked_example.py`) keeps this block, the file
`prototype/examples/decision_78921_turn4.json` and the engine's output identical. To regenerate it:

```bash
python3 -m hanabi_data decision prototype/examples/export_78921.json 4 --pretty
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
  misplays form the run to the end (`end_misplay_run: 2`, §10).

---

## 9. Validation, size and storage

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
- The engine's final score matches `listing.score`.
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
- **Prototype parity:** every position of the three example games, from every seat, gives the same
  `obs`, `label`, `private` and `key` as `prototype/replay.py`. The one exception is 78922's
  `all_or_nothing`, which the prototype hardcoded to `true`.
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
split can group games by deck. Seeds stay out of `obs`.

**Size.** The worked example is ~3.0 KB (~2.4 KB of it `obs`). A late-game 4–5 player record, with ~80 history events and 20
slots, should come to about 10–15 KB of compact JSON. At ~9.5k games × ~60 decisions that is several GB
uncompressed, which is why §5 stores game records (~5 KB each, ≈50 MB in total) and derives decision
records on demand, with an optional cache.

---

## 10. Label quality: deliberate strikeouts

The group **sometimes strikes out on purpose** to abandon a deal. Those moves are real actions in the
export and look no different from genuine misplays, but they aren't attempts to play well. Using them as
labels would teach the model to throw games away.

Game 78921 is an example. It ends in three misplays on turns 8, 10 and 11:
1. a clued Y1 after Y1 was already played
2. G2 with G1 not yet played
3. a duplicate T1

The last two look deliberate. The first may be a genuine mistake, which is also a poor label, for a
different reason.

What this means for the design:
- **The export doesn't say which misplays were deliberate.** Any detection has to be a heuristic, or a
  manual label on a sample of games.
- **The damage is probably concentrated at the end of a game.** The moves before a deliberate strikeout
  may be perfectly good labels. So the filter should probably drop a *run* of moves before the end, not
  the whole game. Most of the 9,084 zero-score games could still contribute.
- **Signals the engine computes,** in `meta` (§7.3):
  - `misplay_run_to_end`: whether the move is part of an unbroken run of misplays that ends the game.
  - `misplay_knowable`: whether a misplay was of a card the actor could already know was unplayable
    (from `know` and `board.gone`), e.g. a known duplicate.
  - `end_misplay_run`: how many misplays that final run has. With `turns_to_end`, this gives the
    distance from the start of the run to the end.

  On 78921 they flag turns 10 and 11 as the run to the end, and not turn 8. None of the three is
  `misplay_knowable`: the clues said "1" or "2" but never the colour, so each card could still have been
  playable. Only a holder counting the cards they could see would know better (Q6).
- Whether to keep **genuine** mistakes, which are still bad moves but are not throw-aways, belongs to the
  wider filtering discussion (Q1).

---

## 11. Open questions

1. **Label filtering policy.** Which moves from lost games are usable? Deliberate strikeouts do happen
   (§10): which heuristic detects them, and should it be checked against hand-labelled games? Keep or drop
   genuine mistakes? Weight moves by result or by player?
2. **Convention drift.** Filter by date, weight toward recent games, or add a date/era marker to `obs`?
3. **Model format and token budget.** Which model reads this, and at what context length? That decides
   the compact format's size. JSON field names are a large share of the ~2.4 KB of `obs`.
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
7. **Augmentation.** Suits play identical roles in these variants. Should training shuffle suit letters
   consistently across a record? Shuffling seats is not valid, because seat order matters.
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
    examples and five longer synthetic games (§9). What's still missing is a sample of real games,
    especially ones that run past the end of the deck.
11. **The live client.** Would the bot play from its own account or a player's? It needs a logged-in
    websocket connection either way. Are the server admins and the group OK with a bot connecting? Should
    it send moves over the websocket, or click in the browser?
12. **Train/test split by seed.** Same-seed games share a deck (§3.5). Split by seed (recommended), and
    check how often seeds repeat within our data once the listing is parsed. `meta.seed` carries it.
13. **`status` misses dead cards.** `trash` means "already played". A card above a rank whose copies are
    all discarded (e.g. B3 after both B2s are gone) can never be played either, but gets no flag, and
    `critical` can still be set on it. The engine copies the prototype here to keep the parity tests
    exact. Should `trash` also cover these cards? That would be `hanabi-decision/v1`. Under All or Nothing
    such a game ends at once, so it matters mainly for 5-suit games without All or Nothing.
