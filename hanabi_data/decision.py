"""GameRecord -> DecisionRecords (docs/representation.md §7).

    for d in decisions(record): ...           # one per action the record's view can act on
    decision_record(record, turn=4)           # one position (UI turn, 1-based), the actor's view
    decision_record(record, turn=4, viewer=1) # any seat's view of that position

A decision record shows one position as one seat sees it. `label` (the move made) and `legal` are
filled in only when that seat is the one to act.
"""
from __future__ import annotations

from typing import Iterator, List, Optional

from .engine import ACTIONS, Engine, positions, replay
from .rules import COPIES, RANKS, SUIT_LETTERS, End, identity_str

SCHEMA = "hanabi-decision/v0"


def summarize(record: dict) -> dict:
    """Game-level facts the metadata needs: how the game ended and what each action did."""
    engine = replay(record)
    end = engine.end
    summary = {"end": end, "total_turns": engine.turn, "actions": engine.actions,
               "final_score": None, "won": None, "end_misplay_run": None}
    if end is not None:
        normal = end["condition"] == End.NORMAL
        summary["final_score"] = engine.score if normal else 0  # game_end.go: any other ending scores 0
        summary["won"] = normal and engine.score == engine.rules.max_score
        # Consecutive misplays at the very end, counted only if the last one ended the game.
        run = 0
        if engine.actions and engine.actions[-1]["ended"]:
            for a in reversed(engine.actions):
                if not a["misplay"]:
                    break
                run += 1
        summary["end_misplay_run"] = run
    return summary


def decisions(record: dict, summary: Optional[dict] = None) -> Iterator[dict]:
    """One record per position where a seat this record can see as is to act: every action of a
    full-information record; the viewing seat's actions (and its current turn, if the game is still
    running) for a player's-view record."""
    summary = summary or summarize(record)
    for engine, action in positions(record):
        if action is None and engine.over:
            break
        if record.get("view") is None or engine.active == record["view"]:
            yield build(record, engine, engine.active, action, summary)


def decision_record(record: dict, turn: int, viewer: Optional[int] = None) -> dict:
    """The position before UI turn `turn` (1-based; total actions + 1 is the final position)."""
    summary = summarize(record)
    for engine, action in positions(record):
        if engine.turn == turn - 1:
            return build(record, engine, engine.active if viewer is None else viewer, action, summary)
    raise ValueError(f"turn {turn} is past the end of the record ({summary['total_turns'] + 1} positions)")


def build(record: dict, engine: Engine, viewer: int, action: Optional[dict], summary: dict) -> dict:
    """The decision record for `viewer` at the engine's current position. `action` is the event that
    follows (None at the end of the record)."""
    rules, n = engine.rules, engine.rules.players
    view = record.get("view")
    if view is not None and viewer != view:
        raise ValueError(f"a record from seat {view}'s view can't show seat {viewer}'s view")
    actor = engine.active
    acting = viewer == actor and not engine.over
    labelled = acting and action is not None and action.get("e") in ACTIONS
    rel = lambda seat: (seat - viewer) % n
    own = set(engine.hands[viewer])
    ident = lambda cid: identity_str(engine.ids[cid])

    cards = [None if cid in own else ident(cid) for cid in range(engine.next_card)]
    public = engine.public_count()

    def status(cid):
        s, r = engine.ids[cid]
        if engine.stacks[s] >= r:
            return ["trash"]
        flags = ["playable"] if engine.stacks[s] == r - 1 else []
        if COPIES[r] - public[(s, r)] == 1:
            flags.append("critical")
        return flags

    def know(cid):
        suits, ranks = engine.know(cid)
        return {"suits": "".join(SUIT_LETTERS[s] for s in suits), "ranks": "".join(map(str, ranks))}

    hands = []
    for seat in [(viewer + k) % n for k in range(n)]:
        slots = [{"slot": i + 1, "card": cid,
                  "id": None if seat == viewer else ident(cid),
                  "status": None if seat == viewer else status(cid),
                  "drawn_t": engine.drawn_t[cid],
                  "touched_t": list(engine.touched_t[cid]),
                  "know": know(cid)} for i, cid in enumerate(engine.hands[seat])]
        hands.append({"seat": rel(seat), "slots": slots})

    unseen = {SUIT_LETTERS[s]: [COPIES[r] - cards.count(f"{SUIT_LETTERS[s]}{r}") for r in RANKS]
              for s in range(rules.suits)}
    deck_left = engine.deck_left
    pace = engine.score + deck_left + n - engine.max_score if deck_left > 0 else None

    label = _label(engine, action, rel) if labelled else None
    legal = _legal(engine, rel) if acting else []
    own_ids = [engine.ids[c] for c in engine.hands[viewer]]
    private = {"own_hand": [identity_str(i) for i in own_ids]} if None not in own_ids else None

    source = record["source"]
    key = {"server": source["server"], "game_id": source["game_id"], "turn": engine.turn + 1}
    if source["kind"] == "live":
        key["table_id"] = source["table_id"]

    return {
        "schema": SCHEMA,
        "key": key,
        "obs": {
            "rules": {"players": n, "suits": rules.suits, "hand_size": rules.hand_size,
                      "all_or_nothing": rules.all_or_nothing, "max_score": rules.max_score},
            "board": {"turn": engine.turn + 1, "score": engine.score, "clues": engine.clues,
                      "strikes": engine.strikes, "deck": deck_left,
                      "stacks": {SUIT_LETTERS[s]: h for s, h in enumerate(engine.stacks)},
                      "discards": list(engine.discards), "pace": pace,
                      "gone": [identity_str(k) for k in engine.gone()]},
            "cards": cards,
            "hands": hands,
            "history": [_relative(ev, rel) for ev in engine.history],
            "unseen": unseen,
            "legal": legal,
        },
        "label": label,
        "private": private,
        "meta": _meta(record, engine, viewer, labelled, summary),
    }


def _relative(ev: dict, rel) -> dict:
    out = {k: v for k, v in ev.items() if k != "missed"}  # `missed` is for the review tool only
    if "by" in out:
        out["by"] = rel(out["by"])
    if "to" in out:
        out["to"] = rel(out["to"])
    if out["e"] == "deal":
        out["hands"] = {rel(s): list(v) for s, v in out["hands"].items()}
    if "touched" in out:
        out["touched"] = list(out["touched"])
    return out


def _label(engine: Engine, action: dict, rel) -> dict:
    if action["e"] == "clue":
        return {"type": "clue", "to": rel(action["to"]), "kind": action["kind"], "value": action["value"]}
    return {"type": action["e"], "slot": engine.hands[action["by"]].index(action["card"]) + 1,
            "card": action["card"]}


def _legal(engine: Engine, rel) -> List[dict]:
    actor, size = engine.active, len(engine.hands[engine.active])
    legal = [{"type": "play", "slot": i + 1} for i in range(size)]
    if engine.clues < 8:
        legal += [{"type": "discard", "slot": i + 1} for i in range(size)]
    if engine.clues > 0:
        for seat in range(engine.rules.players):
            if seat == actor:
                continue
            hand = [engine.ids[c] for c in engine.hands[seat]]
            legal += [{"type": "clue", "to": rel(seat), "kind": "color", "value": SUIT_LETTERS[s]}
                      for s in sorted({i[0] for i in hand})]
            legal += [{"type": "clue", "to": rel(seat), "kind": "rank", "value": r}
                      for r in sorted({i[1] for i in hand})]
    return legal


def _meta(record: dict, engine: Engine, viewer: int, labelled: bool, summary: dict) -> dict:
    names, n = record["players"], engine.rules.players
    t = engine.turn  # index of the action about to be taken
    end, total = summary["end"], summary["total_turns"]
    a = summary["actions"][t] if labelled else None
    raw = record.get("raw") or {}
    run = summary["end_misplay_run"]
    return {
        "players": [names[(viewer + k) % n] for k in range(n)],
        "actor": names[engine.active],
        "datetime": (record.get("listing") or {}).get("datetime"),
        "seed": raw.get("seed") if record["source"]["kind"] == "export" else None,
        "end_condition": end["condition"] if end else None,
        "final_score": summary["final_score"],
        "won": summary["won"],
        "total_turns": total if end else None,
        "turns_to_end": total - (t + 1) if end and labelled else None,
        "label_effect": [f for f, on in (("misplay", a["misplay"]), ("lost_critical", a["max_drop"]),
                                         ("ended_game", a["ended"])) if on] if a else None,
        "misplay_knowable": a["knowable"] if a else None,
        "misplay_run_to_end": bool(a and run and t >= total - run) if a and end else None,
        "end_misplay_run": run,
        "raw_action": raw["actions"][t] if labelled and record["source"]["kind"] == "export" else None,
    }
