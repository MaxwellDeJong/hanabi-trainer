# hanabi-trainer

Tools for building a dataset to train a model that plays **Hanabi** with 2–5 players, learned from
real games played by one group of experienced players. Both the 6-suit ("6 Suits") and 5-suit
("No Variant") games are supported; variants with special rules (Rainbow, Brown, …) are not.

*Status: early. The data pipeline and the labeling tool work; the model and its training live elsewhere
and haven't started yet.*

## Goals

- **Learn a group's conventions from their games, not from hand-written rules.** The group plays with one
  shared (and slowly changing) set of conventions. No convention is hardcoded here: the model gets the
  full game history plus facts that follow from the rules, and has to learn the rest.
- **Decide from the current state alone.** Every training example is a single decision: everything the
  acting player could know at that moment, plus the move they made. It is rich enough to choose a move
  from without any other context.
- **Use the same input in training and in live play.** Recorded games and live games go through the same
  format and the same rules engine, so the model sees the same kind of input in both.
- **Keep labels trustworthy.** Most games under All or Nothing are lost, often through a misplay, but most
  of their moves are still good. So the pipeline marks individual bad moves instead of dropping lost
  games, and a review tool collects high-quality labels from human labelers.

## Approach

```
 game export (JSON) ──► converter ─┐
                                   ├─► GameRecord ──► rules engine ──► DecisionRecord per turn ──► model format
 live game (websocket) ─► converter ┘   event log       replays and       the player's view +          (later)
                                        (stored)         validates         the move made (the label)
```

1. **Collect games.** Game exports come from [new.playhanabi.com](https://new.playhanabi.com), a server
   running the Hanabi Live codebase. `hanabi_data/download.py` fetches a small hand-picked sample
   politely (one request at a time, cached on disk). Bulk collection waits for the server owner's
   permission.
2. **Convert to a GameRecord.** An event log that is the hand-off format for both recorded games and live
   games. In a live game the player's own cards are unknown, so identities may be `null`.
3. **Replay with a rules engine.** A deterministic Python engine replays every record and rejects anything
   the rules don't allow, including the All or Nothing endings the group plays with. Its rules follow the
   Hanabi Live server source at a pinned commit, and it is checked position by position against Hanabi
   Live's own reducer.
4. **Derive decision records.** One per turn, from the acting player's point of view, with the move
   actually made as the label and metadata (score, result, filter hits, …) so training can filter or
   weight moves later. Only game records are stored; decision records are regenerated from them.
5. **Filter bad labels.** Strict, rules-based filters mark moves that are provably bad under every
   identity the player's information allows. Filters start strict and widen only after their catches are
   reviewed on real games.
6. **Review and label.** A browser tool that looks and plays like a live game on the site. A labeler sits
   in one seat of a recorded game and picks the best move at each of their turns. Labels feed
   fine-tuning; the full corpus of played moves feeds pretraining.

Out of scope for this repo: the model format, training, and the live client that plays games.

## Repository layout

| Path | What it is |
|---|---|
| `hanabi_data/` | Python package: converters, rules engine, decision records, label filters, downloader. Python 3.9+, standard library only |
| `review/` | The review and labeling tool: a local Python server and a TypeScript/Vite web app. See [`review/README.md`](review/README.md) |
| `docs/` | Design documents: [`representation.md`](docs/representation.md) (data format and rules), [`review-tool.md`](docs/review-tool.md), [`label-filtering.md`](docs/label-filtering.md) |
| `examples/` | Real game exports and live captures used by the tests and docs. See [`examples/README.md`](examples/README.md) |
| `tests/` | `pytest` suite, including golden decision records and synthetic endgames |

## Quick start

```bash
# Decision record for UI turn 4 of an example game
python3 -m hanabi_data decision examples/export_78921.json 4 --pretty

# Every decision record of a game, one JSON per line
python3 -m hanabi_data decisions examples/export_78921.json > decisions.jsonl

# Validate games, and list the moves the label filters catch
python3 -m hanabi_data check examples/export_*.json
python3 -m hanabi_data filters examples/export_*.json

# Tests
python3 -m pytest

# Review tool (needs Node.js; the first run fetches Hanabi Live's source and installs npm packages)
bash review/run.sh    # then open http://127.0.0.1:8765/
```

## Credits

This project is built on **[Hanabi Live](https://github.com/Hanabi-Live/hanabi-live)** (hanab.live), the
open-source Hanabi server and client, by the Hanabi Live contributors. Thank you for making it free
software. Specifically:

- **Game data.** Every game here was played on new.playhanabi.com, which runs the Hanabi Live codebase,
  and uses its export format and websocket protocol.
- **Rules.** The rules engine in `hanabi_data/` follows the Hanabi Live server's rules (hand sizes, clue
  and strike limits, All or Nothing endings, end conditions) at commit
  [`c1d970b`](https://github.com/Hanabi-Live/hanabi-live/commit/c1d970bd610395087958fe7d1bb96f52244f7b04).
- **Reference reducer.** `review/oracle/` runs Hanabi Live's own game reducer (`packages/game`) to check
  our engine, and is adapted from its `loadGameJSON.ts` test helper.
- **Look, feel and assets.** The review tool draws cards with Hanabi Live's card-drawing code, and uses
  its images and sounds unmodified. It copies the site's layout, speedrun controls and log wording.
  `review/setup.sh` fetches these files from the pinned commit; they are not committed here.

Hanabi Live is licensed under the GNU GPL v3.0. Hanabi itself is a card game designed by Antoine Bauza.

## License

[GPL-3.0](LICENSE), the same license as Hanabi Live.
