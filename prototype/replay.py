"""PROTOTYPE - not the real engine. See prototype/README.md for its known shortcomings.

Replays a hanab.live-format export and prints the v0 decision record (docs/representation.md §7)
for one turn. It exists to generate and check the worked example in docs/representation.md §8.

    python3 prototype/replay.py prototype/examples/export_78921.json 3 --pretty

The turn argument is 0-based (the number of actions already taken); the record's `turn` is 1-based.
"""
import json
import sys

SUITS = "RYGBPT"
COPIES = {1: 3, 2: 2, 3: 2, 4: 2, 5: 1}
HAND_SIZE = {2: 5, 3: 5, 4: 4, 5: 4, 6: 3}


def card_str(c):
    return f"{SUITS[c['suitIndex']]}{c['rank']}"


def max_reachable(stacks, discards, deck):
    """Max score still reachable, as hanab.live computes it (for pace and "Score x / max"): a suit stops
    below its first unplayed rank whose copies are all in the discard pile."""
    total = 0
    for s, height in enumerate(stacks):
        top = 5
        for r in range(height + 1, 6):
            gone = sum(1 for cid in discards if deck[cid]["suitIndex"] == s and deck[cid]["rank"] == r)
            if gone == COPIES[r]:
                top = r - 1
                break
        total += top
    return total


def replay(export, stop_turn):
    # Fail loudly on inputs the prototype does not handle rather than producing a wrong record.
    if export.get("options", {}).get("startingPlayer", 0) != 0:
        raise NotImplementedError("prototype assumes seat 0 moves first")
    if any(a["type"] > 3 for a in export["actions"][:stop_turn + 1]):
        raise NotImplementedError("prototype does not handle end-game actions (types 4/5)")
    deck = export["deck"]
    n = len(export["players"])
    num_suits = 1 + max(c["suitIndex"] for c in deck)
    hands = [[] for _ in range(n)]  # slot order: index 0 == slot 1 (newest, leftmost)
    drawn_turn, touched_by = {}, {}
    pos_color, pos_rank, neg_color, neg_rank = {}, {}, {}, {}
    stacks, discards, events = [0] * num_suits, [], []
    state = dict(clues=8, strikes=0, next_card=0)

    def draw(seat, turn):
        if state["next_card"] < len(deck):
            cid = state["next_card"]
            state["next_card"] += 1
            hands[seat].insert(0, cid)
            drawn_turn[cid] = turn
            touched_by[cid] = []
            for d in (pos_color, pos_rank):
                d[cid] = None
            for d in (neg_color, neg_rank):
                d[cid] = set()
            return cid
        return None

    for seat in range(n):
        for _ in range(HAND_SIZE[n]):
            draw(seat, 0)
    events.append({"t": 0, "e": "deal", "hands": {s: list(hands[s]) for s in range(n)}})

    for turn, a in enumerate(export["actions"]):
        if turn == stop_turn:
            break
        actor = turn % n  # startingPlayer defaults to 0
        ev = {"t": turn + 1, "by": actor}
        if a["type"] in (0, 1):
            cid = a["target"]
            slot = hands[actor].index(cid) + 1
            hands[actor].remove(cid)
            c = deck[cid]
            if a["type"] == 0 and stacks[c["suitIndex"]] == c["rank"] - 1:
                stacks[c["suitIndex"]] += 1
                ev.update(e="play", card=cid, slot=slot, ok=True)
                if c["rank"] == 5 and state["clues"] < 8:
                    state["clues"] += 1
            elif a["type"] == 0:
                state["strikes"] += 1
                discards.append(cid)
                ev.update(e="play", card=cid, slot=slot, ok=False)
            else:
                state["clues"] += 1
                discards.append(cid)
                ev.update(e="discard", card=cid, slot=slot)
            ev["drew"] = draw(actor, turn + 1)
        else:
            tgt, v = a["target"], a["value"]
            kind = "color" if a["type"] == 2 else "rank"
            touched, missed = [], []
            for cid in hands[tgt]:
                c = deck[cid]
                hit = c["suitIndex"] == v if kind == "color" else c["rank"] == v
                if hit:
                    touched.append(cid)
                    touched_by[cid].append(turn + 1)
                    (pos_color if kind == "color" else pos_rank)[cid] = v
                else:
                    missed.append(cid)
                    (neg_color if kind == "color" else neg_rank)[cid].add(v)
            state["clues"] -= 1
            ev.update(e="clue", to=tgt, kind=kind, value=SUITS[v] if kind == "color" else v, touched=touched,
                      missed=missed)
        events.append(ev)

    return dict(deck=deck, n=n, num_suits=num_suits, hands=hands, stacks=stacks,
                discards=discards, events=events, drawn_turn=drawn_turn,
                touched_by=touched_by, pos_color=pos_color, pos_rank=pos_rank,
                neg_color=neg_color, neg_rank=neg_rank, **state)


def decision_record(export, turn, viewer=None):
    """Record for the position before action `turn` (0-based), seen by seat `viewer` (default: the actor).

    `turn == len(actions)` gives the final position. `label`, `legal` and `raw_action` are only filled
    in when the viewer is the actor and an action follows.
    """
    g = replay(export, turn)
    deck, n, S = g["deck"], g["n"], g["num_suits"]
    actor = turn % n
    viewer = actor if viewer is None else viewer
    acting = viewer == actor and turn < len(export["actions"])
    rel = lambda seat: (seat - viewer) % n
    # Card identities the viewer knows now: everything except cards currently in the viewer's hand.
    hidden = set(g["hands"][viewer])
    dealt = g["next_card"]
    cards = [None if cid in hidden else card_str(deck[cid]) for cid in range(dealt)]

    # Public-only elimination: identities whose copies are all on the stacks or in the discard pile.
    public_count = {}
    for s in range(S):
        for r in range(1, g["stacks"][s] + 1):
            public_count[(s, r)] = public_count.get((s, r), 0) + 1
    for cid in g["discards"]:
        k = (deck[cid]["suitIndex"], deck[cid]["rank"])
        public_count[k] = public_count.get(k, 0) + 1

    def know(cid):
        suits = "".join(SUITS[s] for s in range(S)
                        if g["pos_color"][cid] in (None, s) and s not in g["neg_color"][cid])
        ranks = "".join(str(r) for r in range(1, 6)
                        if g["pos_rank"][cid] in (None, r) and r not in g["neg_rank"][cid])
        return {"suits": suits, "ranks": ranks}

    gone = [f"{SUITS[s]}{r}" for (s, r), k in sorted(public_count.items()) if k >= COPIES[r]]

    def status(cid):
        c = deck[cid]
        s, r = c["suitIndex"], c["rank"]
        if g["stacks"][s] >= r:
            return ["trash"]
        flags = ["playable"] if g["stacks"][s] == r - 1 else []
        if COPIES[r] - public_count.get((s, r), 0) == 1:
            flags.append("critical")
        return flags

    hands = []
    for seat_abs in [(viewer + k) % n for k in range(n)]:
        slots = []
        for i, cid in enumerate(g["hands"][seat_abs]):
            slots.append({
                "slot": i + 1, "card": cid,
                "id": None if seat_abs == viewer else card_str(deck[cid]),
                "status": None if seat_abs == viewer else status(cid),
                "drawn_t": g["drawn_turn"][cid],
                "touched_t": g["touched_by"][cid],
                "know": know(cid),
            })
        hands.append({"seat": rel(seat_abs), "slots": slots})

    def rel_event(ev):
        ev = {k: v for k, v in ev.items() if k != "missed"}  # not in schema v0 (see docs/review-tool.md §6.1)
        if "by" in ev:
            ev["by"] = rel(ev["by"])
        if "to" in ev:
            ev["to"] = rel(ev["to"])
        if ev["e"] == "deal":
            ev["hands"] = {rel(s): v for s, v in ev["hands"].items()}
        return ev

    unseen = {}
    for s in range(S):
        unseen[SUITS[s]] = [COPIES[r] - sum(1 for c in cards if c == f"{SUITS[s]}{r}")
                            for r in range(1, 6)]
    deck_left = len(deck) - dealt
    score = sum(g["stacks"])
    pace = score + deck_left + n - max_reachable(g["stacks"], g["discards"], deck) if deck_left > 0 else None

    a = export["actions"][turn] if acting else None
    if a is None:
        label = None
    elif a["type"] in (0, 1):
        label = {"type": "play" if a["type"] == 0 else "discard",
                 "slot": g["hands"][actor].index(a["target"]) + 1, "card": a["target"]}
    else:
        label = {"type": "clue", "to": rel(a["target"]),
                 "kind": "color" if a["type"] == 2 else "rank",
                 "value": SUITS[a["value"]] if a["type"] == 2 else a["value"]}

    legal = [{"type": "play", "slot": i + 1} for i in range(len(g["hands"][actor]))] if acting else []
    if acting and g["clues"] < 8:
        legal += [{"type": "discard", "slot": i + 1} for i in range(len(g["hands"][actor]))]
    if acting and g["clues"] > 0:
        for seat_abs in range(n):
            if seat_abs == actor:
                continue
            h = [deck[c] for c in g["hands"][seat_abs]]
            legal += [{"type": "clue", "to": rel(seat_abs), "kind": "color", "value": SUITS[s]}
                      for s in sorted({c["suitIndex"] for c in h})]
            legal += [{"type": "clue", "to": rel(seat_abs), "kind": "rank", "value": r}
                      for r in sorted({c["rank"] for c in h})]

    return {
        "schema": "hanabi-decision/v0",
        "key": {"server": "new.playhanabi.com", "game_id": export["id"], "turn": turn + 1},
        "obs": {
            "rules": {"players": n, "suits": S, "hand_size": HAND_SIZE[n],
                      "all_or_nothing": True, "max_score": 5 * S},
            "board": {"turn": turn + 1, "score": score, "clues": g["clues"],
                      "strikes": g["strikes"], "deck": deck_left,
                      "stacks": {SUITS[s]: g["stacks"][s] for s in range(S)},
                      "discards": g["discards"],
                      "pace": pace, "gone": gone},
            "cards": cards,
            "hands": hands,
            "history": [rel_event(e) for e in g["events"]],
            "unseen": unseen,
            "legal": legal,
        },
        "label": label,
        "private": {"own_hand": [card_str(deck[cid]) for cid in g["hands"][viewer]]},
        "meta": {"players": [export["players"][(viewer + k) % n] for k in range(n)],
                 "raw_action": a},
    }


def pretty(x, indent=0, width=104):
    """JSON with short containers kept on one line (the layout used in docs/representation.md)."""
    pad = "  " * indent
    one = json.dumps(x, separators=(", ", ": "))
    if len(one) + len(pad) <= width or not isinstance(x, (dict, list)):
        return one
    if isinstance(x, dict):
        items = [f"{pad}  {json.dumps(k)}: {pretty(v, indent + 1, width)}" for k, v in x.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    items = [f"{pad}  {pretty(v, indent + 1, width)}" for v in x]
    return "[\n" + ",\n".join(items) + f"\n{pad}]"


if __name__ == "__main__":
    export = json.load(open(sys.argv[1]))
    record = decision_record(export, int(sys.argv[2]))
    print(pretty(record) if "--pretty" in sys.argv[3:] else json.dumps(record))
