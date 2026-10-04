# While waiting for the bulk download

*Created 2026-10-01 · a checklist to work through until the server owner answers*

*2026-10-02: the owner agreed (`data/download_terms.json`). The open items stay here until they're done.*

The bulk download (`progress.md`, 2026-10-01) is blocked on the owner's permission. This list collects
everything from `progress.md`, `representation.md` §12, `label-filtering.md` §6–7 and `review-tool.md` §10
that **doesn't** need more games. Tick items off here, and record the outcome where it belongs: decisions in
the design doc the item names, work in `progress.md`.

IDs used below: **RQ** = `representation.md` §12, **LF** = `label-filtering.md` §7, **P/W** =
`label-filtering.md` §6. A *Suggest* line is Claude's proposal, not a decision.

State at creation: 191 tests pass; 12 games on hand; listing of 25,412 games (13,975 in scope) parsed;
76 labels from test labelers.

---

## 1. Open questions that can be answered without more data

### 1.1 Decisions for you

These need a choice, not data. Several unblock code in §2 (noted as →).

- [ ] **RQ4 · Pace under All or Nothing.** Keep the site's pace, drop it, or replace it?
  *Suggest:* keep it. Players see it and may react to it, and it's already computed and checked against
  the oracle.
- [ ] **RQ6 · Status bits from clue knowledge.** Add *certainly playable*, *possibly critical* and
  *certainly trash* computed from `know` (minus identities all gone), so they also cover the viewer's own
  cards? *Suggest:* yes. They follow from the rules only, and own cards currently get no status at all.
  → §2 B6.
- [ ] **RQ13 · DecisionRecord `trash` and dead cards.** Bump to `hanabi-decision/v1` so `trash` covers dead
  cards, as trajectories already do? *Suggest:* yes, now, before 14k games make the two definitions'
  disagreement matter. → §2 B7.
- [ ] **RQ8 · Player identity.** Keep inputs anonymous, or add per-player tokens? *Suggest:* keep frames
  anonymous; names are in `meta.players` and the trajectory index, so training can add them later without
  rebuilding anything. The listing can say how many games each partner has (no new data needed).
- [ ] **RQ9 · Shared schema location.** Confirm `hanabi_data/` stays here as the package the live client
  imports.
- [ ] **RQ11 · Live client.** Bot on its own account or yours? Websocket or browser clicks? Ask the owner
  about a bot connecting while you're already talking to them.
- [ ] **Labeling pool at scale.** With 14k games, which go into the review tool? All of them won't work
  (see §2 B3). And should labels come from the `test` split (to evaluate models) or from `train`? Ties into
  `review-tool.md` phase 5 "prioritised sampling".
- [ ] **Labels in git.** Commit `review/labels/` or ignore it (`progress.md` known gap)? They contain the
  labelers' typed names. Same decision for the untracked `message_to_server_owner.md`, `target_games.txt`
  and the three screenshots. → §2 B18.
- [ ] **Sign-in before hosting.** How labelers and the admin authenticate if the tool goes beyond a
  quick tunnel to friends (e.g. Cloudflare Access in front of the tunnel, or per-labeler tokens). Only
  needed before wider sharing. → §2 B14.

Label filtering (`label-filtering.md`):

- [ ] **P1 · How far a lost game got.** Add the cards played at the end to `meta`? *Suggest:* yes. Under
  All or Nothing `final_score` is 0 for ~96% of games, so training can't otherwise tell a loss at 28 from
  one at 3. → §2 B5.
- [ ] **P2 · Copy the position into `meta`.** *Suggest:* no. It's in `obs.board` and in the frames already.
- [ ] **P3 · Keep moves after a blunder** (except the final strikeout run, and moves after the max became
  unreachable in non-All-or-Nothing games). *Suggest:* yes; decide together with LF Q5.
- [ ] **P5 · Record the knowledge level a catch relies on.** Decide before writing W-list filters (§2 B12),
  since it shapes their output.
- [ ] **LF Q2 · Suspicious-move flags.** Descriptive facts only, a manual interpretation pass, or leave
  them to the reviewed fine-tuning set?
- [ ] **LF Q4 · Unprovable mistakes** (gambles, convention misreads) stay in pretraining? *Suggest:* yes;
  that's what the reviewed labels are for.
- [ ] **LF Q5 · Non-All-or-Nothing games after the max is lost.** Drop, keep, or flag those moves?
  Affects only the 5-suit games.

### 1.2 Questions for the group

Only the players can answer these. One conversation could cover all four.

- [ ] **RQ2 · When did the conventions change?** Rough dates or eras. Until then, the listing alone can
  suggest boundaries (games per month, which partners played when), as a starting point for the
  conversation. In-scope games per year: 2022 4,124 · 2023 1,681 · 2024 1,887 · 2025 2,657 · 2026 3,621.
- [ ] **RQ7 · Do the conventions treat every suit alike?** If any suit is special (e.g. the 6th suit, or
  stack order mattering), suit-shuffling augmentation would teach the wrong thing. → §2 B9.
- [ ] **LF Q6 · Deliberate plays of known-unplayable cards.** In which situations does the group do this
  other than to abandon a deal? Needed to keep the W-list filters (W2, W3) from catching them.
- [ ] **LF Q3 · Other ways to throw a game away.** Besides a strikeout: a deliberate double discard? Is
  surrender always used instead when nobody wants to finish?

### 1.3 Checks on the site by hand

A few replay page views in a browser, not a download.

- [ ] **Failed-play wording.** The last line of game 78822 (misplay of the last, clued B2): the site
  prints "(clued)" or "(critical)"? And game 78876 turn 1 (unclued misplay): " (blind)" or nothing?
  (`progress.md` 2026-09-23, phase 1)
- [ ] **Surrender wording.** The end of game 78916: the exact "… terminated the game!" line. → §2 B13.
- [ ] **Replay URL.** Does `new.playhanabi.com/replay/<id>#<turn>` open at that turn? Inspect's
  "Open on site ↗" depends on it.

### Not answerable yet (for reference)

These wait for the pilot run or the full download: **RQ10** (server version, especially games past the end
of the deck), agreeing the two candidate filters, **LF Q1** (order of widening), **P4** (filters vs
reviewed labels, which also needs reviewed games with catches), and **RQ14** (vocabulary, which waits for
the first model).

---

## 2. Code changes that can be made without more data

Roughly in order of value. Items marked *after …* wait on a decision from §1.

### Ready for the download

- [x] **B1 · One command to build the corpus.** *Done 2026-10-01:* `python3 -m hanabi_data corpus`
  (`hanabi_data/corpus.py`, `progress.md`). It runs in parallel instead of resuming: 1,200 games took 23 s
  on 20 cores, so a full rebuild after each download session takes minutes.
- [x] **B2 · Pilot report.** A summary for the 100-game pilot that answers RQ10: counts of options,
  variants, player counts and end conditions, `check` failures by kind, listing mismatches, and how many
  games run past the end of the deck. *Done 2026-10-02:* `check --summary`, with the same counts by year
  (`progress.md`).
- [ ] **B3 · Review tool at 14k games.** `review/run.sh` rebuilds a bundle for every export, through the
  Node oracle, on every start, and the board lists every game. With the full download that's about 14k
  bundles of 1–2 MB each (the 12 on hand reach 1.8 MB), so roughly 14 GB. Build bundles only for a labeling pool (after the "labeling pool"
  decision), cache them, and skip unchanged ones.
- [ ] **B4 · Synthetic games at scale.** The random full-information search that found
  `tests/data/synthetic_*.json` lives only in the tests. As a generator it could make a few thousand legal
  games to time B1 and to compare the engine with the oracle far beyond 12 games.

### Representation and metadata

- [ ] **B5 · P1 field** (*after P1*): cards played when the game ended, in `meta` and the trajectory index.
- [ ] **B6 · Knowledge-based status bits** (*after RQ6*): in trajectories (§8.2), with tests on the 12
  games and the synthetic ones.
- [ ] **B7 · `hanabi-decision/v1`** (*after RQ13*): `trash` covers dead cards. Regenerate
  `tests/data/golden_decisions.jsonl` and review the diff.
- [ ] **B8 · Human-label file** (`representation.md` §8.8 "later"): turn `review/labels/` events into
  step-shaped fields with `encode_move`, keyed by (`game_id`, viewer, step), `also_ok` as masks. The 76
  existing labels are enough to build and test it.
- [ ] **B9 · Suit permutation** (*after RQ7*): one function applying a suit permutation to a trajectory
  (`suit`, `know_suits` bits, per-suit arrays, clue values), with a test that a permuted game still
  replays.
- [ ] **B10 · Era marker** (*after RQ2*): if the group gives eras, add them to `meta` and the index.

### Label filtering

- [ ] **B11 · Filters vs reviewed labels (P4).** A report comparing filter catches, and the recorded moves
  in general, with labelers' choices. Agreement between labeler and recorded move is useful on its own.
  Built now, meaningful once there are catches in reviewed games.
- [ ] **B12 · W1 and W3 as candidates** (*after P5*). W1: a 5 known from negative clues. W3: a play of
  known trash. Candidates only, so they never reach `meta.filters` (D3). Test on synthetic positions;
  explore on real games after the download (D2).

### Review tool

- [ ] **B13 · Surrender and score.** Add the "… terminated the game!" log line (wording from §1.3), and
  show the recorded score, not the board score, in the admin view's Inspect list.
- [ ] **B14 · Sign-in** (*after the sign-in decision*). Admin view and Inspect behind an admin role.
- [ ] **B15 · Phase 4 leftovers** (`review-tool.md` §10): check failures marked on the timeline, a
  "changes since the previous turn" panel, searching and filtering the admin games list (more useful once
  B3 lands).
- [ ] **B16 · One seat to two labelers on purpose**, to measure agreement between labelers (open since
  4a).
- [ ] **B17 · Efficiency in Inspect** (shown as "–" now). Low priority.

### Housekeeping

- [ ] **B18 · Untracked files** (*after "labels in git"*): `.gitignore` or commit `review/labels/` and the
  other untracked files.
- [ ] **B19 · `compute` setup.** Install numpy (for `trajectory.py`) and node/npm (for `review/run.sh`) on
  `compute`, and run the test suite there.
