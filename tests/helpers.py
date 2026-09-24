"""Test helpers: real example files, and synthetic exports for rules the examples don't reach."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from hanabi_data.convert_export import from_export
from hanabi_data.engine import replay
from hanabi_data.rules import COPIES, SUIT_LETTERS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "prototype" / "examples"
GAMES = ("78921", "78822", "78922")


def load_export(game_id: str) -> dict:
    return json.loads((EXAMPLES / f"export_{game_id}.json").read_text())


def norm(x):
    """What the JSON file would hold (int dict keys become strings, tuples become lists)."""
    return json.loads(json.dumps(x))


def sorted_deck(suits: int) -> List[str]:
    """One of each card in suit/rank order, then the spare copies. Easy to win with full information."""
    firsts = [f"{SUIT_LETTERS[s]}{r}" for s in range(suits) for r in range(1, 6)]
    spares = [f"{SUIT_LETTERS[s]}{r}" for s in range(suits) for r in range(1, 6) for _ in range(COPIES[r] - 1)]
    return firsts + spares


def make_export(deck: List[str], actions: List[dict], players: int = 2, options: Optional[dict] = None,
                game_id: int = 1) -> dict:
    export = {"id": game_id, "players": [f"p{i}" for i in range(players)],
              "deck": [{"suitIndex": SUIT_LETTERS.index(c[0]), "rank": int(c[1])} for c in deck],
              "actions": actions, "seed": "test"}
    if options:
        export["options"] = options
    return export


def play(card):
    return {"type": 0, "target": card, "value": 0}


def discard(card):
    return {"type": 1, "target": card, "value": 0}


def color_clue(seat, suit):
    return {"type": 2, "target": seat, "value": SUIT_LETTERS.index(suit)}


def rank_clue(seat, rank):
    return {"type": 3, "target": seat, "value": rank}


def greedy_actions(deck: List[str], players: int = 2, options: Optional[dict] = None,
                   max_actions: int = 500) -> List[dict]:
    """A full-information player: play anything playable, else discard something safe, else clue.
    Stops when the rules end the game. Used to reach endings no example game reaches."""
    ids = [(SUIT_LETTERS.index(c[0]), int(c[1])) for c in deck]
    actions: List[dict] = []
    engine = replay(from_export(make_export(deck, actions, players, options)))
    while not engine.over and len(actions) < max_actions:
        hand, nxt = engine.hands[engine.active], (engine.active + 1) % players
        public = engine.public_count()
        playable = [c for c in hand if engine.playable(ids[c])]
        trash = [c for c in hand if engine.stacks[ids[c][0]] >= ids[c][1]]
        spare = [c for c in hand if COPIES[ids[c][1]] - public[ids[c]] >= 2]
        if playable:
            action = play(playable[-1])
        elif engine.clues < 8 and (trash or spare):
            action = discard((trash or spare)[-1])
        elif engine.clues > 0 and engine.hands[nxt]:
            action = rank_clue(nxt, ids[engine.hands[nxt][0]][1])
        else:
            action = discard(hand[-1])
        actions.append(action)
        engine = replay(from_export(make_export(deck, actions, players, options)))
    return actions
