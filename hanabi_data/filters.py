"""Filters for the pretraining corpus (docs/label-filtering.md).

A filter marks a move whose label is bad under every identity the actor's clue knowledge allows,
so it holds whatever conventions the group uses. Filters start as `candidate`: they are explored on
real games (`python3 -m hanabi_data filters ...`) and only become `agreed` once every move they
catch has been looked at. Only agreed filters appear in a decision record's `meta.filters`.

Filters start as narrow as possible and are widened one at a time, because the group does sometimes
play a card it knows is unplayable on purpose.

    fired(engine, action)      # names of every filter (any status) that catches the move
    agreed(engine, action)     # the agreed ones only
    firings(record, summary)   # every caught move of a game, for exploring
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterator, List

from .engine import Engine, positions
from .rules import COPIES, SUIT_LETTERS

CANDIDATE, AGREED = "candidate", "agreed"


@dataclass(frozen=True)
class Filter:
    name: str
    status: str
    doc: str
    test: Callable[[Engine, dict], bool]


def _clued_5(engine: Engine, cid: int) -> bool:
    """A rank clue said "5". Knowing it only from negative clues doesn't count (not yet)."""
    return engine.pos_rank[cid] == 5


def _play_clued_5_no_4(engine: Engine, action: dict) -> bool:
    cid = action["card"]
    if action["e"] != "play" or not _clued_5(engine, cid):
        return False
    suits, _ = engine.know(cid)
    return all(engine.stacks[s] != 4 for s in suits)


def _discard_clued_5_live(engine: Engine, action: dict) -> bool:
    cid = action["card"]
    if action["e"] != "discard" or not _clued_5(engine, cid):
        return False
    discarded = Counter(engine.ids[c] for c in engine.discards)
    suits = [s for s in engine.know(cid)[0] if engine.stacks[s] < 5]  # a played 5 can't be this card
    live = lambda s: all(discarded[(s, r)] < COPIES[r] for r in range(1, 5))
    return bool(suits) and all(map(live, suits))


FILTERS = (
    Filter("play_clued_5_no_4", CANDIDATE,
           "Plays a card a rank clue called 5 while no suit it could be has its stack at 4.",
           _play_clued_5_no_4),
    Filter("discard_clued_5_live", CANDIDATE,
           "Discards a card a rank clue called 5 while every suit it could be can still reach 5, "
           "so the max score is certainly lost.",
           _discard_clued_5_live),
)
BY_NAME = {f.name: f for f in FILTERS}


def fired(engine: Engine, action: dict) -> List[str]:
    """Names of the filters that catch `action`, the move about to be made at the engine's position."""
    if action.get("e") not in ("play", "discard"):
        return []
    return [f.name for f in FILTERS if f.test(engine, action)]


def agreed(engine: Engine, action: dict) -> List[str]:
    return [name for name in fired(engine, action) if BY_NAME[name].status == AGREED]


def firings(record: dict, summary: dict) -> Iterator[dict]:
    """Every move of `record` that any filter catches, with what's needed to judge it by eye."""
    run, total = summary["end_misplay_run"], summary["total_turns"]
    for engine, action in positions(record):
        if action is None:
            break
        names = fired(engine, action)
        if not names:
            continue
        suits, ranks = engine.know(action["card"])
        t = engine.turn
        yield {
            "filters": names,
            "game_id": record["source"]["game_id"],
            "turn": t + 1,
            "seat": engine.active,
            "move": action["e"],
            "card": action["id"],
            "know": "".join(SUIT_LETTERS[s] for s in suits) + "/" + "".join(map(str, ranks)),
            "stacks": "".join(f"{SUIT_LETTERS[s]}{h}" for s, h in enumerate(engine.stacks)),
            "clues": engine.clues,
            "strikes": engine.strikes,
            "end_run": bool(run) and t >= total - run,  # part of the misplay run that ended the game
            "turns_to_end": total - (t + 1),
            "end_condition": (summary["end"] or {}).get("condition"),
        }
