"""GameRecord -> trajectories, the model input (docs/representation.md §8).

A trajectory is a whole game seen from one seat: T + 1 frames (the position before each action, and the
one after the last) and T steps (the actions), stored as integer-coded numpy arrays. Seats are relative
to the viewer throughout. Tokens and tensors are derived from these codes in the training code.

    traj = trajectory(record, viewer=1)      # a full-information record: any seat
    traj = trajectory(live_record)           # a player's view: that seat, up to the current turn
    print(render(traj, 3))                   # frame 3 and step 3 (UI turn 4)
    write_shards(trajs, out_dir); for traj in read_shards(out_dir): ...
    encode_move(move, view, seat=)           # any of the repo's move formats -> factored fields
    decode_move(fields, view)                # factored fields -> a move in `legal` format

This module is the adapter between the JSON records and the arrays (§8.8). It never replays a game: each
frame is encoded from a DecisionRecord taken from the viewer's seat (`decision.views`), and each step from
the history event between two of them. Its only rules are the derived `status` bits.

Not imported by `hanabi_data/__init__.py`, so the rest of the package (and the review tool) works
without numpy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Union

import numpy as np

from .decision import summarize, views
from .filters import FILTERS
from .rules import COPIES, RANKS, SUIT_LETTERS, parse_identity

SCHEMA = "hanabi-trajectory/v0"

MAX_SEATS, MAX_SLOTS, MAX_SUITS = 5, 5, 6
NONE = 255     # no card in this slot, a seat/slot/suit this game doesn't have, or a field that doesn't apply
UNKNOWN = 254  # the identity of one of the viewer's own cards in hand
NO_PACE = -128  # `pace` once the deck is empty
MAX_TOUCHES, MAX_AGE = 7, 253

# `status` bits (teammates' cards only; 0 for the viewer's own cards)
PLAYABLE, CRITICAL, TRASH = 1, 2, 4
# Step `type`
PLAY, DISCARD, COLOR_CLUE, RANK_CLUE = 0, 1, 2, 3
TYPE_NAMES = ("play", "discard", "color", "rank")
# Step `effect` bits
MISPLAY, LOST_CRITICAL, ENDED_GAME, MISPLAY_RUN_TO_END = 1, 2, 4, 8
# `legal_clue` is [target, value]: the target's relative seat 1-4, then a colour 0-5 or rank 1-5 at 6-10
CLUE_TARGETS, CLUE_VALUES = MAX_SEATS - 1, MAX_SUITS + 5

_SLOTS = (MAX_SEATS, MAX_SLOTS)
_PER_SUIT = (MAX_SUITS, 5)

# name -> shape of one frame or one step. Every field is uint8 except `pace` (int8).
FRAME_FIELDS = {
    "actor": (), "score": (), "clues": (), "strikes": (), "deck": (), "pace": (),
    "stacks": (MAX_SUITS,), "discards": _PER_SUIT, "unseen": _PER_SUIT,
    "suit": _SLOTS, "rank": _SLOTS, "know_suits": _SLOTS, "know_ranks": _SLOTS, "touches": _SLOTS,
    "age": _SLOTS, "touch_age": _SLOTS, "status": _SLOTS, "card": _SLOTS,
    "own_suit": (MAX_SLOTS,), "own_rank": (MAX_SLOTS,),
}
STEP_FIELDS = {
    "actor": (), "type": (), "slot": (), "target": (), "value": (), "touched": (),
    "card_suit": (), "card_rank": (), "ok": (), "drew": (),
    "is_label": (), "legal_play": (MAX_SLOTS,), "legal_discard": (MAX_SLOTS,),
    "legal_clue": (CLUE_TARGETS, CLUE_VALUES), "effect": (), "filters": (),
}
# Never model input (§8.4, §8.7): deck indices, labels and targets.
NOT_INPUT = {
    "frame": ("card", "own_suit", "own_rank"),
    "step": ("is_label", "legal_play", "legal_discard", "legal_clue", "effect", "filters"),
}
# Game-level numbers stored per trajectory; None is stored as -1.
TRAJ_FIELDS = ("viewer", "n_players", "n_suits", "hand_size", "all_or_nothing", "game_id", "has_private",
               "end_condition", "final_score", "won")
_BOOL_FIELDS = ("all_or_nothing", "has_private", "won")
# Text kept out of the arrays, in index.jsonl.
INDEX_FIELDS = ("server", "game_id", "table_id", "viewer", "players", "seed", "datetime")


def _dtype(name: str):
    return np.int8 if name == "pace" else np.uint8


def _blank(fields: Dict[str, tuple]) -> Dict[str, np.ndarray]:
    return {k: np.full(shape, NO_PACE if k == "pace" else NONE, dtype=_dtype(k)) for k, shape in fields.items()}


@dataclass
class Trajectory:
    """One game from one seat. `frames[k]` has T + 1 rows, `steps[k]` has T. `meta` holds the game-level
    fields of TRAJ_FIELDS and INDEX_FIELDS, plus `schema`."""
    meta: dict
    frames: Dict[str, np.ndarray]
    steps: Dict[str, np.ndarray]

    @property
    def n_steps(self) -> int:
        return len(self.steps["type"])


# ---------------------------------------------------------------------------------------------------
# Records -> arrays

def trajectory(record: dict, viewer: Optional[int] = None) -> Trajectory:
    """The trajectory of `record` from seat `viewer` (absolute; default: the record's view)."""
    viewer = record.get("view") if viewer is None else viewer
    summary = summarize(record)
    vs = list(views(record, viewer, summary))
    frames = {k: np.stack([f[k] for f in map(encode_frame, vs)]) for k in FRAME_FIELDS}
    encoded = [encode_step(a, b) for a, b in zip(vs, vs[1:])]
    for t, s in enumerate(encoded):
        s["effect"][()] = _effect(summary, t)
    steps = {k: np.stack([s[k] for s in encoded]) if encoded else np.zeros((0,) + shape, _dtype(k))
             for k, shape in STEP_FIELDS.items()}

    first, rules = vs[0], vs[0]["obs"]["rules"]
    m, key = first["meta"], first["key"]
    meta = {"schema": SCHEMA, "server": key["server"], "game_id": key["game_id"], "table_id": key.get("table_id"),
            "viewer": viewer, "players": list(record["players"]), "seed": m["seed"], "datetime": m["datetime"],
            "n_players": rules["players"], "n_suits": rules["suits"], "hand_size": rules["hand_size"],
            "all_or_nothing": rules["all_or_nothing"], "has_private": first["private"] is not None,
            "end_condition": m["end_condition"], "final_score": m["final_score"], "won": m["won"]}
    return Trajectory(meta, frames, steps)


def encode_frame(view: dict) -> Dict[str, np.ndarray]:
    """One DecisionRecord (any seat's view) -> the frame fields (§8.2), padded to 5 seats × 5 slots × 6 suits."""
    obs, meta = view["obs"], view["meta"]
    board, suits = obs["board"], obs["rules"]["suits"]
    t = board["turn"] - 1  # actions taken so far
    f = _blank(FRAME_FIELDS)

    f["actor"][()] = meta["players"].index(meta["actor"])  # names in relative seat order
    for k in ("score", "clues", "strikes", "deck"):
        f[k][()] = board[k]
    if board["pace"] is not None:
        f["pace"][()] = board["pace"]
    stacks = [board["stacks"][SUIT_LETTERS[s]] for s in range(suits)]
    discards = np.zeros((suits, 5), np.uint8)
    for cid in board["discards"]:
        s, r = parse_identity(obs["cards"][cid], suits)
        discards[s, r - 1] += 1
    f["stacks"][:suits] = stacks
    f["discards"][:suits] = discards
    f["unseen"][:suits] = [obs["unseen"][SUIT_LETTERS[s]] for s in range(suits)]

    for hand in obs["hands"]:
        seat = hand["seat"]
        for i, sl in enumerate(hand["slots"]):
            at = (seat, i)
            if sl["id"] is None:
                f["suit"][at] = f["rank"][at] = UNKNOWN
                f["status"][at] = 0
            else:
                s, r = parse_identity(sl["id"], suits)
                f["suit"][at], f["rank"][at] = s, r
                f["status"][at] = status_bits(s, r, stacks, discards)
            know = sl["know"]
            f["know_suits"][at] = sum(1 << SUIT_LETTERS.index(c) for c in know["suits"])
            f["know_ranks"][at] = sum(1 << (int(c) - 1) for c in know["ranks"])
            f["touches"][at] = min(len(sl["touched_t"]), MAX_TOUCHES)
            f["age"][at] = min(t - sl["drawn_t"], MAX_AGE)
            if sl["touched_t"]:
                f["touch_age"][at] = min(t - sl["touched_t"][-1], MAX_AGE)
            f["card"][at] = sl["card"]

    if view["private"] is not None:
        for i, ident in enumerate(view["private"]["own_hand"]):
            f["own_suit"][i], f["own_rank"][i] = parse_identity(ident, suits)
    return f


def status_bits(suit: int, rank: int, stacks, discards) -> int:
    """`playable` / `critical` / `trash` from a card's true identity (§8.2). Trash means it can never be
    played: already played, or dead (a lower rank of its suit has every copy discarded). A trash card
    is never critical."""
    height = stacks[suit]
    if rank <= height or any(discards[suit][q - 1] >= COPIES[q] for q in range(height + 1, rank)):
        return TRASH
    bits = PLAYABLE if rank == height + 1 else 0
    if COPIES[rank] - discards[suit][rank - 1] == 1:
        bits |= CRITICAL
    return bits


def encode_step(before: dict, after: dict) -> Dict[str, np.ndarray]:
    """The action between two consecutive views from the same seat -> the step fields (§8.3, §8.4).
    `effect` is left at 0: it needs the game summary (`trajectory` fills it in)."""
    hb, ha = before["obs"]["history"], after["obs"]["history"]
    if len(ha) != len(hb) + 1 or ha[:-1] != hb:
        raise ValueError("the views are not consecutive positions")
    ev, suits = ha[-1], before["obs"]["rules"]["suits"]
    s = _blank(STEP_FIELDS)
    s["actor"][()] = ev["by"]
    s["touched"][()] = 0
    s["drew"][()] = ev.get("drew") is not None
    if ev["e"] in ("play", "discard"):
        s["type"][()] = PLAY if ev["e"] == "play" else DISCARD
        s["slot"][()] = ev["slot"]
        s["card_suit"][()], s["card_rank"][()] = parse_identity(after["obs"]["cards"][ev["card"]], suits)
        if ev["e"] == "play":
            s["ok"][()] = ev["ok"]
    else:
        s["type"][()] = COLOR_CLUE if ev["kind"] == "color" else RANK_CLUE
        s["target"][()] = ev["to"]
        s["value"][()] = SUIT_LETTERS.index(ev["value"]) if ev["kind"] == "color" else ev["value"]
        slots = before["obs"]["hands"][ev["to"]]["slots"]
        s["touched"][()] = sum(1 << i for i, sl in enumerate(slots) if sl["card"] in ev["touched"])

    s["is_label"][()] = ev["by"] == 0
    s["legal_play"][:] = s["legal_discard"][:] = 0
    s["legal_clue"][:] = 0
    for move in before["obs"]["legal"]:
        head, index = encode_move(move, before)["mask"]
        s["legal_" + head][index] = 1
    names = before["meta"]["filters"] or []
    s["filters"][()] = sum(1 << i for i, f in enumerate(FILTERS) if f.name in names)
    s["effect"][()] = 0
    return s


def _effect(summary: dict, t: int) -> int:
    """`effect` bits for action t (0-based), as `meta.label_effect` and `meta.misplay_run_to_end` have them."""
    a, run, end = summary["actions"][t], summary["end_misplay_run"], summary["end"]
    bits = MISPLAY * a["misplay"] | LOST_CRITICAL * a["max_drop"] | ENDED_GAME * a["ended"]
    if end and run and t >= summary["total_turns"] - run:
        bits |= MISPLAY_RUN_TO_END
    return bits


# ---------------------------------------------------------------------------------------------------
# Moves

def encode_move(move: dict, view: dict, seat: Optional[int] = None) -> dict:
    """A move in any of the repo's three forms -> factored step fields, relative to the view's seat.

    - a GameRecord event: `{"e": "play", "by", "card", ...}`, `{"e": "clue", "by", "to", "kind", "value", ...}`
    - a DecisionRecord `label` or `legal[]` entry: `{"type": "play", "slot"}`, `{"type": "clue", "to", "kind", "value"}`
    - a labeling-tool `choice` or `also_ok` entry: `{"type": "play", "card"}`, `{"type": "clue", "to_seat", "kind", "value"}`

    The first and last use absolute seats, so they need `seat`: the view's absolute seat. The other two
    are always the view's own move. Returns `actor`, `type`, `slot`, `target`, `value` (as in a step;
    `None` where a step has NONE) and `mask`: where the move sits in the legal-mask arrays, `("play", i)`,
    `("discard", i)` or `("clue", (i, j))`, or `None` when someone other than the viewer moves.
    """
    obs = view["obs"]
    n, suits = obs["rules"]["players"], obs["rules"]["suits"]

    def rel(absolute):
        if seat is None:
            raise ValueError(f"{move}: a move with absolute seats needs the view's seat")
        return (absolute - seat) % n

    if "e" in move:
        kind, actor = move["e"], rel(move["by"])
        to = rel(move["to"]) if kind == "clue" else None
    else:
        kind, actor = move.get("type"), 0
        to = rel(move["to_seat"]) if "to_seat" in move else move.get("to")

    out = {"actor": actor, "type": None, "slot": None, "target": None, "value": None, "mask": None}
    if kind in ("play", "discard"):
        slots = obs["hands"][actor]["slots"]
        if "slot" in move and "e" not in move:
            slot = move["slot"]
        else:
            slot = next((sl["slot"] for sl in slots if sl["card"] == move.get("card")), None)
        if not isinstance(slot, int) or not 1 <= slot <= len(slots):
            raise ValueError(f"{move}: no such card or slot in seat {actor}'s hand")
        out.update(type=PLAY if kind == "play" else DISCARD, slot=slot)
        if actor == 0:
            out["mask"] = (kind, slot - 1)
    elif kind == "clue":
        if not isinstance(to, int) or not 0 <= to < n or to == actor:
            raise ValueError(f"{move}: bad clue target")
        if move["kind"] == "color" and isinstance(move["value"], str) and len(move["value"]) == 1 \
                and move["value"] in SUIT_LETTERS[:suits]:
            value = SUIT_LETTERS.index(move["value"])
            out.update(type=COLOR_CLUE, target=to, value=value)
            column = value
        elif move["kind"] == "rank" and move["value"] in RANKS:
            out.update(type=RANK_CLUE, target=to, value=move["value"])
            column = MAX_SUITS + move["value"] - 1
        else:
            raise ValueError(f"{move}: bad clue")
        if actor == 0:
            out["mask"] = ("clue", (to - 1, column))
    else:
        raise ValueError(f"{move}: unknown move")
    return out


def decode_move(fields: dict, view: Optional[dict] = None) -> dict:
    """Factored fields (`type`, `slot`, `target`, `value`, as in a step) -> a move in `legal` format. With
    a view, the move must be one of its legal moves."""
    kind = int(fields["type"])
    if kind in (PLAY, DISCARD):
        move = {"type": TYPE_NAMES[kind], "slot": int(fields["slot"])}
    elif kind == COLOR_CLUE:
        move = {"type": "clue", "to": int(fields["target"]), "kind": "color", "value": SUIT_LETTERS[int(fields["value"])]}
    elif kind == RANK_CLUE:
        move = {"type": "clue", "to": int(fields["target"]), "kind": "rank", "value": int(fields["value"])}
    else:
        raise ValueError(f"unknown move type {kind}")
    if view is not None and move not in view["obs"]["legal"]:
        raise ValueError(f"{move} is not a legal move here")
    return move


def decode_legal(steps: Dict[str, np.ndarray], t: int) -> List[dict]:
    """Step t's legal masks -> moves in `legal` format: plays, discards, then clues by relative target.
    (A DecisionRecord lists clue targets by absolute seat, so its order can differ.)"""
    moves = [{"type": head, "slot": i + 1} for head in ("play", "discard")
             for i in np.flatnonzero(steps["legal_" + head][t]).tolist()]
    for to, column in zip(*map(np.ndarray.tolist, np.nonzero(steps["legal_clue"][t]))):
        if column < MAX_SUITS:
            moves.append({"type": "clue", "to": to + 1, "kind": "color", "value": SUIT_LETTERS[column]})
        else:
            moves.append({"type": "clue", "to": to + 1, "kind": "rank", "value": column - MAX_SUITS + 1})
    return moves


# ---------------------------------------------------------------------------------------------------
# Arrays -> readable form

def decode_frame(frames: Dict[str, np.ndarray], t: int, n_suits: int) -> dict:
    """Frame t, in the DecisionRecord's vocabulary as far as the frame holds it (for tests and `render`)."""
    f = {k: v[t] for k, v in frames.items()}
    letters = SUIT_LETTERS[:n_suits]
    board = {"actor": int(f["actor"]), "score": int(f["score"]), "clues": int(f["clues"]),
             "strikes": int(f["strikes"]), "deck": int(f["deck"]),
             "pace": None if f["pace"] == NO_PACE else int(f["pace"]),
             "stacks": {c: int(f["stacks"][s]) for s, c in enumerate(letters)},
             "discards": {f"{c}{r + 1}": int(f["discards"][s, r]) for s, c in enumerate(letters)
                          for r in range(5) if f["discards"][s, r]}}
    hands = []
    for seat in range(MAX_SEATS):
        slots = []
        for i in range(MAX_SLOTS):
            if f["suit"][seat, i] == NONE:
                continue
            known = f["suit"][seat, i] != UNKNOWN
            bits = int(f["status"][seat, i])
            slots.append({
                "slot": i + 1, "card": int(f["card"][seat, i]),
                "id": f"{SUIT_LETTERS[f['suit'][seat, i]]}{f['rank'][seat, i]}" if known else None,
                "status": [name for name, b in (("playable", PLAYABLE), ("critical", CRITICAL), ("trash", TRASH))
                           if bits & b] if known else None,
                "know": {"suits": "".join(c for s, c in enumerate(letters) if f["know_suits"][seat, i] >> s & 1),
                         "ranks": "".join(str(r) for r in RANKS if f["know_ranks"][seat, i] >> (r - 1) & 1)},
                "touches": int(f["touches"][seat, i]), "age": int(f["age"][seat, i]),
                "touch_age": None if f["touch_age"][seat, i] == NONE else int(f["touch_age"][seat, i]),
            })
        if slots or seat == 0:
            hands.append({"seat": seat, "slots": slots})
    unseen = {c: f["unseen"][s].tolist() for s, c in enumerate(letters)}
    own = [None if s == NONE else f"{SUIT_LETTERS[s]}{r}" for s, r in zip(f["own_suit"], f["own_rank"])]
    return {"board": board, "hands": hands, "unseen": unseen, "own_hand": own}


def describe_step(steps: Dict[str, np.ndarray], t: int) -> str:
    s = {k: v[t] for k, v in steps.items()}
    who = f"seat {s['actor']}"
    if s["type"] in (PLAY, DISCARD):
        card = f"{SUIT_LETTERS[s['card_suit']]}{s['card_rank']}"
        verb = "discards" if s["type"] == DISCARD else "plays" if s["ok"] else "misplays"
        text = f"{who} {verb} {card} from slot {s['slot']}" + ("" if s["drew"] else " (no draw)")
    else:
        value = SUIT_LETTERS[s["value"]] if s["type"] == COLOR_CLUE else str(s["value"])
        touched = [str(i + 1) for i in range(MAX_SLOTS) if s["touched"] >> i & 1]
        text = f"{who} clues {value} to seat {s['target']}, touching slot{'s' * (len(touched) > 1)} {', '.join(touched)}"
    effects = [name for name, b in (("misplay", MISPLAY), ("lost_critical", LOST_CRITICAL),
                                    ("ended_game", ENDED_GAME), ("misplay_run_to_end", MISPLAY_RUN_TO_END))
               if s["effect"] & b]
    return text + (" · label" if s["is_label"] else "") + (f" · {', '.join(effects)}" if effects else "")


def render(traj: Trajectory, t: int) -> str:
    """A readable printout of frame t and step t (UI turn t + 1)."""
    m = traj.meta
    d = decode_frame(traj.frames, t, m["n_suits"])
    b = d["board"]
    names = [m["players"][(m["viewer"] + k) % m["n_players"]] for k in range(m["n_players"])]
    pace = "none" if b["pace"] is None else f"{b['pace']:+d}"
    lines = [
        f"game {m['game_id']} · seat {m['viewer']}'s view ({names[0]}) · frame {t} of {traj.n_steps} (UI turn {t + 1})",
        f"seat {b['actor']} to act · score {b['score']} · clues {b['clues']} · strikes {b['strikes']} · "
        f"deck {b['deck']} · pace {pace}",
        "stacks " + " ".join(f"{c}{h}" for c, h in b["stacks"].items()) + " · discards "
        + (" ".join(f"{k}×{v}" for k, v in b["discards"].items()) or "none"),
    ]
    for hand in d["hands"]:
        lines.append(f"seat {hand['seat']} ({names[hand['seat']]}){' (viewer)' if hand['seat'] == 0 else ''}")
        for sl in hand["slots"]:
            know = f"{sl['know']['suits']}/{sl['know']['ranks']}"
            clued = f"clued {sl['touches']}×, {sl['touch_age']} ago" if sl["touches"] else ""
            lines.append(f"  {sl['slot']}  {sl['id'] or '??'}  know {know:<12} {clued:<20} "
                         f"age {sl['age']:<3} card {sl['card']:<3} {' '.join(sl['status'] or [])}".rstrip())
    lines.append(f"step {t}: {describe_step(traj.steps, t)}" if t < traj.n_steps else "end of the record")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------
# Storage (§8.5)

def write_shards(trajs: Iterable[Trajectory], out_dir: Union[str, Path], per_shard: int = 1000) -> int:
    """Write trajectories as `shard-NNNNN.npz` files plus `index.jsonl`. Refuses a directory that
    already has shards. Returns the number written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "index.jsonl").exists() or any(out.glob("shard-*.npz")):
        raise FileExistsError(f"{out} already holds trajectories")
    count, batch, index = 0, [], []

    def flush():
        name = f"shard-{len(index):05d}.npz"
        _write_shard(out / name, batch)
        index.append([{"shard": name, "row": i, **{k: tr.meta[k] for k in INDEX_FIELDS}} for i, tr in enumerate(batch)])
        batch.clear()

    for tr in trajs:
        batch.append(tr)
        count += 1
        if len(batch) == per_shard:
            flush()
    if batch:
        flush()
    (out / "index.jsonl").write_text("".join(json.dumps(row) + "\n" for rows in index for row in rows))
    return count


def _write_shard(path: Path, trajs: List[Trajectory]) -> None:
    n_steps = np.array([tr.n_steps for tr in trajs], np.int64)
    arrays = {"schema": np.array(SCHEMA), "traj_n_steps": n_steps,
              "traj_step_start": np.concatenate([[0], np.cumsum(n_steps)[:-1]]).astype(np.int64),
              "traj_frame_start": np.concatenate([[0], np.cumsum(n_steps + 1)[:-1]]).astype(np.int64)}
    for k in TRAJ_FIELDS:
        arrays["traj_" + k] = np.array([-1 if tr.meta[k] is None else int(tr.meta[k]) for tr in trajs], np.int64)
    for k in FRAME_FIELDS:
        arrays["frame_" + k] = np.concatenate([tr.frames[k] for tr in trajs])
    for k in STEP_FIELDS:
        arrays["step_" + k] = np.concatenate([tr.steps[k] for tr in trajs])
    np.savez_compressed(path, **arrays)


def load_shard(path: Union[str, Path]) -> Dict[str, np.ndarray]:
    """Every array of one shard, as stored (§8.5). For training code that wants whole fields at once."""
    with np.load(path, allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    if str(arrays["schema"]) != SCHEMA:
        raise ValueError(f"{path}: schema {arrays['schema']}, expected {SCHEMA}")
    return arrays


def read_shards(directory: Union[str, Path]) -> Iterator[Trajectory]:
    """The trajectories of a directory written by `write_shards`, in order."""
    directory = Path(directory)
    rows = [json.loads(line) for line in (directory / "index.jsonl").read_text().splitlines()]
    arrays, loaded = None, None
    for row in rows:
        if row["shard"] != loaded:
            arrays, loaded = load_shard(directory / row["shard"]), row["shard"]
        i = row["row"]
        fs, ss, n = (int(arrays[f"traj_{k}"][i]) for k in ("frame_start", "step_start", "n_steps"))
        meta = {"schema": SCHEMA, **{k: row[k] for k in INDEX_FIELDS}}
        for k in TRAJ_FIELDS:
            v = int(arrays["traj_" + k][i])
            meta[k] = None if v == -1 else bool(v) if k in _BOOL_FIELDS else v
        yield Trajectory(meta, {k: arrays["frame_" + k][fs:fs + n + 1] for k in FRAME_FIELDS},
                         {k: arrays["step_" + k][ss:ss + n] for k in STEP_FIELDS})
