# Label filtering for the pretraining corpus

*Status: in progress · last updated 2026-09-25*

This document tracks everything about filtering bad moves out of the training data: the approach,
decisions made, proposals not yet decided, open questions and progress. The data format is in
`representation.md`, and the reviewed labels come from the tool in `review-tool.md`.

---

## 1. Context

Training happens outside this repo, in two phases:

1. **Pretraining** on a very large corpus that trusts the moves players actually made.
2. **Fine-tuning** on reviewed labels: high-trust moves chosen by labelers in the review tool.

Most moves are good, but most games are lost (~96% of the group's 6-suit games score 0 under All or
Nothing), often through misplays. Dropping lost games would throw away most of the data. So the
pretraining corpus needs a way to drop **individual** obviously bad moves.

**This repo's job:** mark bad moves and store enough metadata for training to filter or weight moves.
**Not this repo's job:** the filtering or weighting policy itself. Training decides that.

---

## 2. Decisions

| # | Date | Decision | Why |
|---|---|---|---|
| D1 | 2026-09-25 | **Provably bad moves only, for now.** A filter catches a move only if it is bad under every identity the actor's information allows. It judges the decision, not the outcome: a lucky blind play isn't caught, and neither is an unlucky but reasonable one | There's no convention write-up yet, and conventions have changed over time. Rules-based facts hold whatever convention was in use |
| D2 | 2026-09-25 | **Start with the strictest conditions and widen one step at a time.** Each widening is explored on real games before it's agreed | The group sometimes plays a card it knows is unplayable **on purpose** (situationally). So even a provably unplayable play isn't always a bad label |
| D3 | 2026-09-25 | **Candidate → agreed.** A new filter is a `candidate`. `python3 -m hanabi_data filters <games>` lists every move it catches. Once those moves have been looked at, the filter can become `agreed`. Only agreed filters appear in `meta.filters` | A candidate must never reach a training corpus by accident |
| D4 | 2026-09-25 | **No "suspicious move" flags yet.** They're wanted, but the design is open (Q2) | The root cause of a bomb takes interpretation. Automatic blame would often point at the wrong player (§4) |
| D5 | 2026-09-25 | **Clue ≠ play.** No filter may assume a clued card is meant to be played | A 1 clue can touch a card the cluer knows will bomb. A 1 clue after all 1s are played marks trash |
| D6 | 2026-09-25 | **Weighting is decided at training time.** This repo only stores enough metadata (score, result, …) to derive a weight | Keeps policy out of the data pipeline |
| D7 | 2026-09-23 | **Store the metadata now, filter later.** Filtering fields go in the decision record's `meta` (`representation.md` §7.3) | Any later filter can be applied without reprocessing the games |

---

## 3. Filters

Code: `hanabi_data/filters.py`. Tests: `tests/test_filters.py`.

| Filter | Status | Catches |
|---|---|---|
| `play_clued_5_no_4` | candidate | A play of a card a **rank clue** called 5, while no suit the card's clues allow has its stack at 4 |
| `discard_clued_5_live` | candidate | A discard of a card a rank clue called 5, while every suit its clues allow (excluding suits whose 5 is already played) can still reach 5. The max score is then certainly lost |

Both use **clue knowledge only**: the card's positive and negative clues, the stacks and the discard
pile. Card counting isn't used. The card must have had a positive "5" clue, so a card known to be a 5
only because every other rank was ruled out isn't caught (see W1).

### 3.1 Workflow for a new filter

1. Add it to `FILTERS` in `filters.py` with status `candidate`, plus tests: one where it fires, and one
   for each edge case where it mustn't.
2. Run `python3 -m hanabi_data filters data/exports/*.json`. Each caught move is printed with its
   board state and a review-tool Inspect link (`#/game/<id>/<turn>?pov=<seat>`), then totals per filter,
   including how many catches fall inside a final misplay run (§5). Duplicate game IDs are read once.
3. Look at the caught moves. Especially look for deliberate plays that aren't throw-aways (D2).
4. Once agreed, change the status to `agreed` and record the decision in §2 and the progress log (§8).

---

## 4. Why suspicious moves are hard

A bomb from playing a clued card can come from any of these, and the record looks the same for all:

1. A cluer **accepted** the bomb, e.g. a clue that gets several cards played at the cost of one bomb.
2. A player played a clued card that couldn't be known to be a misplay, **to alert** a teammate that
   something is off.
3. A player played a card **too early**.
4. A player played a card that **couldn't be playable** given the clue.

Only case 4 is provable from the rules (D1). Cases 1–3 need the cluer's intent, and whether the bomb was
"priced into" the clue. So a filter that blamed the player for a misplay would sometimes remove a move that
correctly followed the conventions. The facts an interpreter needs are already in every record: which clues
touched the card, from whom, and what else they touched.

---

## 5. Signals already in `meta`

Existing metadata that a filter or training can use (`representation.md` §7.3):

| Field | Meaning | Kind |
|---|---|---|
| `label_effect` | `misplay`, `lost_critical` (the reachable max score fell), `ended_game` | Outcome |
| `misplay_knowable` | A misplay the card's clue knowledge (minus `gone`) already showed to be unplayable | Actor's information |
| `misplay_run_to_end` | Part of the unbroken run of misplays that ended the game | Game ending |
| `end_misplay_run` | Length of that run | Game ending |
| `turns_to_end`, `total_turns`, `end_condition`, `final_score`, `won` | The game's result and where this move sits in it | Result |
| `filters` | Agreed filters that catch the move | Filter |

### 5.1 Deliberate strikeouts

The group **sometimes strikes out on purpose** to abandon a deal. Those moves are real actions in the
export and look no different from genuine misplays, but they aren't attempts to play well. Training on
them would teach the model to throw games away.

Game 78921 is an example. It ends in three misplays on turns 8, 10 and 11:
1. a clued Y1 after Y1 was already played
2. G2 with G1 not yet played
3. a duplicate T1

The last two look deliberate. The first may be a genuine mistake, which is a poor label for a different
reason.

- **The export doesn't say which misplays were deliberate.** Any detection has to be a heuristic, or
  manual labeling of a sample of games.
- **The damage is probably concentrated at the end of a game.** The moves before a deliberate strikeout
  may be good labels, so drop the *run*, not the whole game.
- `misplay_run_to_end` flags turns 10 and 11 of 78921, but not turn 8. None of the three is
  `misplay_knowable`: the clues said "1" or "2" but never the colour.

---

## 6. Proposals (not yet decided)

Ideas raised in discussion that are waiting for a decision.

| # | Proposal | Notes |
|---|---|---|
| P1 | **Add how far a lost game got to `meta`**: the number of cards played when it ended | Under All or Nothing, `final_score` is 0 for almost every loss, so training can't tell a game lost at 28 from one lost at 3. Supports D6 |
| P2 | **Copy the move's position into `meta`** (score, strikes, clues, deck left at this move) | These are already in `obs.board`. Copying them saves a filter from parsing `obs` |
| P3 | **Keep the moves that follow a blunder**, except the deliberate strikeout run, and in games without All or Nothing the moves after the max score became unreachable | Each decision is Markovian. Positions after a teammate's mistake are still real attempts to play well, and the model has to learn to recover from them |
| P4 | **Check filters against reviewed labels.** A move caught by an agreed filter should almost never match the reviewer's move | Estimates precision, and helps decide candidates. Needs reviewed games that include caught moves |
| P5 | **Record the knowledge level a catch relies on** (W-list below) | Training could then trust a level more or less |

### 6.1 Possible widenings (W-list)

Candidates for later filters, roughly from safest to riskiest. Each needs exploring on real data first
(D2).

| # | Widening | Risk |
|---|---|---|
| W1 | Rank 5 known from negative clues too (every other rank ruled out) | Low: the fact is the same, only how the player learned it differs |
| W2 | Known rank *r* for *r* = 2–4 with no stack at *r* − 1 in any possible suit | Deliberate plays may start slipping in |
| W3 | Known trash: every identity the clues allow is already played (e.g. a 1 clue after all 1s are played). A **play** of it is provably bad; a discard is fine | Same concern as W2 |
| W4 | Knowably critical discards beyond clued 5s: every identity the clues allow is the last copy of a card that is still reachable | Rare. Chop discards of unknown cards are never caught |
| W5 | **Card counting (K1)**: also rule out identities whose remaining copies the actor can all see | Players read clues by hovering (pip-updating is off) and may not count every card. A K1 catch is still a real mistake |
| W6 | **Whole-hand deduction (K2)**: identities that no assignment of the whole hand allows, given the copies left | More subtle (D5). A small search: ≤5 cards, 30 identities |

---

## 7. Open questions

1. **Order of widening.** Which of W1–W6 comes next, once there's more data?
2. **Suspicious-move flags.** How to implement them (§4)? Options: descriptive facts only, a manual
   interpretation pass, or leave them to the reviewed fine-tuning set.
3. **Detecting deliberate strikeouts.** Is `misplay_run_to_end` enough, and should it be checked against
   hand-labeled games (§5.1)? Can a deliberate throw-away happen in some other way, such as a double
   discard?
4. **Genuine mistakes that aren't provable** (gambles, convention misreads): under D1 they stay in
   pretraining. Is that acceptable, or should reviewed labels also be used to find patterns in them?
5. **Games without All or Nothing** (5-suit): what happens to moves after the max score became
   unreachable (P3)? `label_effect.lost_critical` marks the move that lost it.
6. **Deliberate plays of known-unplayable cards** (D2): which situations are they? Examples from the
   group would help design the W-list filters to leave them alone.

---

## 8. Progress log

Newest first.

### 2026-09-25 · Tracking document

This document created. §10 of `representation.md` moved here.

### 2026-09-25 · First two candidate filters

- `hanabi_data/filters.py`: `play_clued_5_no_4` and `discard_clued_5_live`, both candidates.
- `python3 -m hanabi_data filters <games>`: the report described in §3.1.
- `meta.filters` (agreed only, so `[]` for now). `examples/decision_78921_turn4.json` regenerated.
- `tests/test_filters.py` (9 tests): each filter fires, and doesn't when a possible suit is at 4 (play)
  or dead (discard). Only agreed filters reach `meta`. `python3 -m pytest`: 93 passed.

**On the 12 sample games (443 moves):** one catch, 78922 turn 10: a known P5 played with every stack at
0 and 2 strikes. It's the third strike that ended the game, and looks deliberate.

### 2026-09-25 · First probe of the sample

A throwaway script tried broader card-by-card rules (clue knowledge, then card counting) on the sample.

| | Count |
|---|---|
| Misplays | 18 |
| Plays unplayable for every identity the clues allow | 2 (78876 turn 13, 78922 turn 10: both the third strike that ended the game, and both look deliberate) |
| Discards of a card known to be the last copy of a reachable card | 0 |
| `lost_critical` discards | 4 (none knowable) |

The other 16 misplays could all have been playable as far as the rules go: gambles, or plays on a
misread convention (§4). So rules-based filters are precise but catch little. Most real mistakes are
convention mistakes, which the reviewed labels have to handle.

### Next

- Run the report on a larger download once bulk collection is permitted.
- Decide whether to agree the two candidates.
- Pick the next widening (Q1) and decide P1–P5.
