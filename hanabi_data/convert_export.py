"""Export JSON (new.playhanabi.com/export/<id>) -> GameRecord (docs/representation.md §6).

The converter replays the export through the engine as it builds the events, so it fills in what an
export leaves out (which cards each clue touched, whether a play failed, rule-based endings) and
rejects illegal games on the way.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from .engine import Engine
from .record import SCHEMA, SERVER
from .rules import COPIES, End, InvalidGame, Rules, identity_str


def from_export(export: dict, *, listing: Optional[dict] = None, fetched_at: Optional[str] = None,
                server: str = SERVER) -> dict:
    """Raises Unsupported (variant/options) or InvalidGame (illegal or inconsistent export).

    A game whose actions stop before any ending gets no `end` event; `check.check_record` reports
    it as truncated.
    """
    players = export["players"]
    rules = Rules.from_site_options(export.get("options", {}), len(players))
    deck = export["deck"]
    _check_deck(deck, rules)
    ids = [(c["suitIndex"], c["rank"]) for c in deck]
    name = lambda cid: identity_str(ids[cid])

    engine, events = Engine(rules), []

    def emit(ev):
        engine.apply(ev)
        events.append(ev)

    for cid in range(rules.players * rules.hand_size):
        emit({"e": "draw", "seat": cid // rules.hand_size, "card": cid, "id": name(cid)})

    for i, a in enumerate(export["actions"]):
        if engine.over:
            raise InvalidGame(f"action {i} ({a}) after the game ended")
        kind, target, value, by = a.get("type"), a.get("target"), a.get("value"), engine.active
        if kind in (0, 1):
            if not isinstance(target, int) or not 0 <= target < len(deck):
                raise InvalidGame(f"action {i}: card {target}")
            ev = {"e": "play" if kind == 0 else "discard", "by": by, "card": target, "id": name(target)}
            if kind == 0:
                ev["ok"] = target in engine.hands[by] and engine.playable(ids[target])
            emit(ev)
            if engine.dealt and engine.pending_draw is not None:
                emit({"e": "draw", "seat": by, "card": engine.next_card, "id": name(engine.next_card)})
        elif kind in (2, 3):
            if not isinstance(target, int) or not 0 <= target < rules.players:
                raise InvalidGame(f"action {i}: clue to seat {target}")
            if kind == 2:
                if not isinstance(value, int) or not 0 <= value < rules.suits:
                    raise InvalidGame(f"action {i}: colour clue value {value}")
                clue_value, matches = rules.letters[value], lambda c: ids[c][0] == value
            else:
                clue_value, matches = value, lambda c: ids[c][1] == value
            touched = sorted(c for c in engine.hands[target] if matches(c))
            emit({"e": "clue", "by": by, "to": target, "kind": "color" if kind == 2 else "rank",
                  "value": clue_value, "touched": touched})
        elif kind in (4, 5):
            if i != len(export["actions"]) - 1:
                raise InvalidGame(f"action {i}: end-game action before the last action")
            condition = End.TERMINATED_BY_VOTE if kind == 5 else value
            emit({"e": "end", "condition": condition, "seat": target if isinstance(target, int) and target >= 0 else None})
        else:
            raise InvalidGame(f"action {i}: unknown type {kind}")
        if engine.rule_end is not None and engine.end is None:
            condition, seat = engine.rule_end
            emit({"e": "end", "condition": condition, "seat": seat})

    return {
        "schema": SCHEMA,
        "source": {"kind": "export", "server": server, "game_id": export.get("id"), "table_id": None,
                   "fetched_at": fetched_at},
        "view": None,
        "players": list(players),
        "options": rules.to_record(),
        "events": events,
        "listing": listing,
        "raw": export,
    }


def _check_deck(deck: list, rules: Rules) -> None:
    if len(deck) != rules.deck_size:
        raise InvalidGame(f"deck has {len(deck)} cards; {rules.variant} has {rules.deck_size}")
    try:
        count = Counter((c["suitIndex"], c["rank"]) for c in deck)
    except (KeyError, TypeError) as e:
        raise InvalidGame(f"bad deck entry: {e}") from None
    expected = {(s, r): COPIES[r] for s in range(rules.suits) for r in COPIES}
    if count != expected:
        wrong = sorted(set(count) ^ set(expected) | {k for k in count if count[k] != expected.get(k)})
        raise InvalidGame(f"deck composition is wrong for {rules.variant}: {wrong[:5]}")
