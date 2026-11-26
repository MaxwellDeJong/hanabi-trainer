"""Human labels from the review tool, in trajectory terms (B8), and how well they agree with the moves played.

The review tool stores append-only events per game (`review/labels/<game_id>.jsonl`, `review-tool.md` §8).
This module turns the label in force at each turn into one row keyed like a trajectory step:
(`game_id`, `viewer`, `step`), where `viewer` is the labeled seat (absolute) and `step` = UI turn − 1. The
labeler's choice is stored as factored fields (`type`, `slot`, `target`, `value`, as in a step), and the
choice plus every `also_ok` move as masks in the legal shape (`ok_play`, `ok_discard`, `ok_clue`).

    uv run python -m hanabi_data labels review/labels --out data/human_labels     # the label file
    uv run python -m hanabi_data agreement review/labels                          # agreement report

`agreement_report` compares each label with the move actually recorded at that turn, at three levels:
- **exact**: the same move, or a move the labeler marked `also_ok`;
- **touch**: also counts a clue to the same player that touches the same cards (colour vs rank, say);
- **type**: the same kind of move (play, discard, colour clue, rank clue).
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Union

import numpy as np

from .decision import summarize, views
from .record import load_game
from .rules import parse_identity
from .trajectory import (CLUE_TARGETS, CLUE_VALUES, MAX_SLOTS, MAX_SUITS, NONE, TYPE_NAMES, encode_move)

SCHEMA = "hanabi-human-labels/v0"
# Arrays in labels.npz: one row per label. Every field is uint8 except these.
ARRAY_FIELDS = {"game_id": np.int32, "viewer": np.uint8, "step": np.int16,
                "type": np.uint8, "slot": np.uint8, "target": np.uint8, "value": np.uint8,
                "ok_play": np.uint8, "ok_discard": np.uint8, "ok_clue": np.uint8}
# Text and per-label facts in labels.jsonl, in the same order as the arrays.
INDEX_FIELDS = ("server", "game_id", "viewer", "step", "turn", "player", "labeller", "session_id", "chosen_by",
                "submitted", "at", "hint_used", "after_reveal", "ms_to_choice", "choice", "also_ok")
DEFAULT_EXPORTS = (Path("data/exports"), Path("examples"))


# ---------------------------------------------------------------------------------------------------
# Reading the review tool's events

def current_labels(events: Iterable[dict]) -> Dict[tuple, dict]:
    """The label in force per (session, turn). Later label events replace earlier ones, and `retract`
    removes one: the same rule as the review server's `Store.current_labels`, across every session."""
    labels, by_id = {}, {}
    for e in events:
        key = (e.get("session_id"), e.get("turn"))
        if e["kind"] == "label":
            labels[key] = e
            by_id[e["event_id"]] = e
        elif e["kind"] == "retract":
            old = by_id.get(e["replaces"])
            if old is not None and labels.get((old["session_id"], old["turn"])) is old:
                del labels[(old["session_id"], old["turn"])]
    return labels


def read_label_dir(root: Union[str, Path]) -> tuple:
    """`(events by game ID, sessions by session ID)` from a review-tool label directory."""
    root = Path(root)
    events = {}
    for path in sorted(root.glob("*.jsonl")):
        with path.open() as f:
            events[int(path.stem)] = [json.loads(line) for line in f if line.strip()]
    sessions = {}
    for path in sorted((root / "sessions").glob("*.json")):
        s = json.loads(path.read_text())
        sessions[s.get("session_id", path.stem)] = s
    return events, sessions


def find_export(game_id: int, dirs: Sequence[Path] = DEFAULT_EXPORTS) -> Optional[Path]:
    for d in dirs:
        p = Path(d) / f"export_{game_id}.json"
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------------------------------
# Labels -> rows

def _mask_set(moves: Iterable[dict], view: dict, seat: int) -> set:
    return {_key(encode_move(m, view, seat=seat)["mask"]) for m in moves}


def _key(mask) -> tuple:
    head, index = mask
    return (head,) + (tuple(index) if isinstance(index, tuple) else (index,))


def _touched(view: dict, target: int, kind: str, value) -> frozenset:
    """The slots of teammate `target` (relative) a clue would touch, from the cards the viewer sees."""
    suits = view["obs"]["rules"]["suits"]
    out = set()
    for sl in view["obs"]["hands"][target]["slots"]:
        suit, rank = parse_identity(sl["id"], suits)
        if (kind == "color" and suit == value) or (kind == "rank" and rank == value):
            out.add(sl["slot"])
    return frozenset(out)


def _move_facts(fields: dict, view: dict) -> dict:
    """Comparable facts about one encoded move of the viewer's."""
    t = fields["type"]
    facts = {"type": TYPE_NAMES[t], "mask": _key(fields["mask"]), "touch": None}
    if t >= 2:  # a clue: what it touches
        kind = "color" if t == 2 else "rank"
        facts["touch"] = (fields["target"], _touched(view, fields["target"], kind, fields["value"]))
    return facts


def label_rows(record: dict, events: List[dict], sessions: Dict[str, dict]) -> List[dict]:
    """One row per label in force in `events` (all from `record`'s game), with the encoded choice, the
    `also_ok` masks and the move actually recorded at that turn. Labels whose seat isn't the actor at
    their turn, or whose move isn't legal there, raise ValueError (the tool never writes them)."""
    in_force = current_labels(events)
    by_seat = defaultdict(list)
    for (_, turn), e in in_force.items():
        by_seat[e["seat"]].append(e)
    summary = summarize(record)
    rows = []
    for seat, labels in sorted(by_seat.items()):
        vs = list(views(record, seat, summary))
        for e in sorted(labels, key=lambda e: (e["turn"], e["session_id"])):
            step = e["turn"] - 1
            if not 0 <= step < len(vs) - 1:
                raise ValueError(f"game {e['game_id']} turn {e['turn']}: outside the game")
            view = vs[step]
            if view["meta"]["actor"] != record["players"][seat]:
                raise ValueError(f"game {e['game_id']} turn {e['turn']}: seat {seat} isn't the actor")
            legal = _mask_set(view["obs"]["legal"], view, 0)
            choice = encode_move(e["choice"], view, seat=seat)
            ok = {_key(choice["mask"])} | _mask_set(e.get("also_ok") or [], view, seat)
            if not ok <= legal:
                raise ValueError(f"game {e['game_id']} turn {e['turn']}: a labeled move isn't legal")
            actual = encode_move(view["label"], view)
            s = sessions.get(e["session_id"], {})
            rows.append({
                "server": e.get("server"), "game_id": e["game_id"], "viewer": seat, "step": step,
                "turn": e["turn"], "player": record["players"][seat], "labeller": e["labeller"],
                "session_id": e["session_id"], "chosen_by": s.get("chosen_by"),
                "submitted": bool(s.get("submitted")), "at": e.get("at"), "hint_used": bool(e.get("hint_used")),
                "after_reveal": bool(e.get("after_reveal", e.get("changed_after_reveal", False))),
                "ms_to_choice": e.get("ms_to_choice"), "choice": e["choice"], "also_ok": e.get("also_ok") or [],
                "fields": {k: choice[k] for k in ("type", "slot", "target", "value")}, "ok": sorted(ok),
                "labelled": _move_facts(choice, view), "also_ok_facts": [
                    _move_facts(encode_move(m, view, seat=seat), view) for m in e.get("also_ok") or []],
                "recorded": _move_facts(actual, view), "meta": view["meta"],
            })
    return rows


def collect(label_dir: Union[str, Path], exports: Sequence[Path] = DEFAULT_EXPORTS,
            submitted_only: bool = False) -> tuple:
    """Every label row in `label_dir`, and a list of problems (games without an export, bad labels)."""
    events, sessions = read_label_dir(label_dir)
    rows, problems = [], []
    for game_id, evs in sorted(events.items()):
        if submitted_only:
            evs = [e for e in evs if sessions.get(e.get("session_id"), {}).get("submitted")]
        if not any(e["kind"] == "label" for e in evs):
            continue
        path = find_export(game_id, exports)
        if path is None:
            problems.append(f"game {game_id}: no export found")
            continue
        try:
            rows += label_rows(load_game(path), evs, sessions)
        except ValueError as err:
            problems.append(str(err))
    return rows, problems


# ---------------------------------------------------------------------------------------------------
# Rows -> the label file

def to_arrays(rows: List[dict]) -> Dict[str, np.ndarray]:
    n = len(rows)
    out = {"game_id": np.zeros(n, np.int32), "viewer": np.zeros(n, np.uint8), "step": np.zeros(n, np.int16)}
    for k in ("type", "slot", "target", "value"):
        out[k] = np.full(n, NONE, np.uint8)
    out["ok_play"] = np.zeros((n, MAX_SLOTS), np.uint8)
    out["ok_discard"] = np.zeros((n, MAX_SLOTS), np.uint8)
    out["ok_clue"] = np.zeros((n, CLUE_TARGETS, CLUE_VALUES), np.uint8)
    for i, r in enumerate(rows):
        out["game_id"][i], out["viewer"][i], out["step"][i] = r["game_id"], r["viewer"], r["step"]
        for k, v in r["fields"].items():
            if v is not None:
                out[k][i] = v
        for head, *index in r["ok"]:
            out["ok_" + head][(i, *index)] = 1
    return out


def write_label_file(rows: List[dict], out_dir: Union[str, Path]) -> None:
    """`labels.npz` (arrays, ARRAY_FIELDS) and `labels.jsonl` (a header line, then INDEX_FIELDS per row,
    in the same order)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_dir / "labels.npz", **to_arrays(rows))
    with (out_dir / "labels.jsonl").open("w") as f:
        f.write(json.dumps({"schema": SCHEMA, "rows": len(rows)}) + "\n")
        for r in rows:
            f.write(json.dumps({k: r[k] for k in INDEX_FIELDS}) + "\n")


# ---------------------------------------------------------------------------------------------------
# Agreement

def agrees(row: dict) -> dict:
    """Whether the recorded move agrees with the label at each level (exact ⊆ touch ⊆ type, except that a
    matching `also_ok` move of another type counts for exact and touch but not for type)."""
    rec, mine = row["recorded"], [row["labelled"]] + row["also_ok_facts"]
    exact = rec["mask"] in {tuple(k) for k in row["ok"]}
    touch = exact or (rec["touch"] is not None and any(m["touch"] == rec["touch"] for m in mine))
    return {"exact": exact, "touch": touch, "type": rec["type"] == row["labelled"]["type"]}


def cohen_kappa(pairs: List[tuple]) -> Optional[float]:
    """Cohen's kappa over categorical pairs (labeler, recorded)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in ca) / n / n
    return None if pe == 1 else (po - pe) / (1 - pe)


MIN_GAMES = 5  # fewer games than this: no interval (a bootstrap over 2-4 games is meaningless)


def cluster_interval(hits_by_game: Dict[int, List[bool]], reps: int = 2000, seed: int = 0) -> tuple:
    """A 95% bootstrap interval for the agreement rate, resampling whole games (moves in a game aren't
    independent). With fewer than MIN_GAMES games the interval is (None, None)."""
    games = list(hits_by_game.values())
    if len(games) < MIN_GAMES:
        return None, None
    rng = random.Random(seed)
    rates = []
    for _ in range(reps):
        pick = [games[rng.randrange(len(games))] for _ in games]
        n = sum(map(len, pick))
        rates.append(sum(map(sum, pick)) / n)
    rates.sort()
    return rates[int(0.025 * reps)], rates[int(0.975 * reps) - 1]


def shrink(groups: Dict[str, tuple]) -> Dict[str, float]:
    """Empirical-Bayes (beta-binomial) estimates for `{group: (hits, n)}`: each rate is pulled towards the
    pooled rate, more so for small groups. The prior's strength comes from the spread between groups
    (method of moments); when the groups don't differ more than chance would make them, every group gets
    the pooled rate."""
    hits, n = sum(h for h, _ in groups.values()), sum(k for _, k in groups.values())
    if not n:
        return {}
    m = hits / n
    sizes = [k for _, k in groups.values() if k]
    rates = [h / k for h, k in groups.values() if k]
    if len(rates) < 2 or m in (0, 1):
        return {g: m for g in groups}
    spread = sum(k * (r - m) ** 2 for r, k in zip(rates, sizes)) / sum(sizes)  # weighted observed variance
    chance = m * (1 - m) * len(rates) / sum(sizes)                                  # its binomial part
    tau2 = spread - chance
    if tau2 <= 0:
        return {g: m for g in groups}
    strength = max(m * (1 - m) / tau2 - 1, 0.0)
    return {g: (h + strength * m) / (k + strength) for g, (h, k) in groups.items()}


def _buckets(row: dict) -> List[str]:
    """The `meta` buckets a label falls in (agreement idea 5)."""
    m = row["meta"]
    out = ["all"]
    out.append("won game" if m.get("won") else "lost game")
    tte = m.get("turns_to_end")
    if tte is not None:
        out.append("last move of the game" if tte == 0 else
                   "2-3 moves from the end" if tte <= 2 else "earlier")
    out += [f"recorded move: {e}" for e in m.get("label_effect") or []]
    if m.get("misplay_run_to_end"):
        out.append("recorded move: in final misplay run")
    out += [f"filter: {f}" for f in m.get("filters") or []]
    out.append(f"recorded: {row['recorded']['type']}")
    return out


def _pct(x: Optional[float]) -> str:
    return "  –  " if x is None else f"{100 * x:4.0f}%"


def agreement_report(rows: List[dict], level: str = "exact") -> List[str]:
    """A plain-text report: overall agreement, per player (raw, interval by game, shrunk), labeler ×
    player, and by `meta` bucket. `level` (exact, touch or type) is the one used for the per-group tables."""
    if not rows:
        return ["no labels"]
    ag = [agrees(r) for r in rows]
    lines = [f"{len(rows)} labels · {len({r['game_id'] for r in rows})} games · "
             f"{len({(r['game_id'], r['viewer']) for r in rows})} seats · "
             f"labelers: {', '.join(f'{k} {v}' for k, v in Counter(r['labeller'] for r in rows).most_common())}",
             ""]
    lines.append("Agreement with the recorded move")
    for lv in ("exact", "touch", "type"):
        lines.append(f"  {lv:<6} {_pct(sum(a[lv] for a in ag) / len(ag))}")
    kappa = cohen_kappa([(r["labelled"]["type"], r["recorded"]["type"]) for r in rows])
    lines.append(f"  kappa over move types (play / discard / colour / rank): "
                 f"{'–' if kappa is None else f'{kappa:.2f}'}")
    types = list(TYPE_NAMES)
    confusion = Counter((r["labelled"]["type"], r["recorded"]["type"]) for r in rows)
    lines += ["", "Move types: labeler (rows) vs recorded (columns)",
              "  " + " " * 9 + "".join(f"{t:>9}" for t in types)]
    for a in types:
        lines.append(f"  {a:<9}" + "".join(f"{confusion[(a, b)]:>9}" for b in types))

    def table(key, title):
        groups = defaultdict(lambda: defaultdict(list))
        for r, a in zip(rows, ag):
            groups[key(r)][r["game_id"]].append(a[level])
        counts = {g: (sum(map(sum, by_game.values())), sum(map(len, by_game.values())))
                  for g, by_game in groups.items()}
        shrunk = shrink(counts)
        out = ["", f"{title} ({level} agreement; interval: 95% bootstrap over games, "
               f"shown from {MIN_GAMES} games; shrunk: empirical Bayes)",
               f"  {'':<28}{'labels':>7}{'games':>6}{'raw':>7}{'interval':>15}{'shrunk':>8}"]
        for g, (h, n) in sorted(counts.items(), key=lambda kv: -kv[1][1]):
            lo, hi = cluster_interval(groups[g])
            ci = "      –      " if lo is None else f"{_pct(lo)}–{_pct(hi).strip():>4}"
            out.append(f"  {str(g)[:28]:<28}{n:>7}{len(groups[g]):>6}{_pct(h / n):>7}{ci:>15}{_pct(shrunk[g]):>8}")
        return out

    lines += table(lambda r: r["player"], "By player (whose move was recorded)")
    lines += table(lambda r: r["labeller"], "By labeler")
    lines += table(lambda r: f"{r['labeller']} / {r['player']}", "Labeler / player")

    by = defaultdict(list)
    for r, a in zip(rows, ag):
        for b in _buckets(r):
            by[b].append(a[level])
    lines += ["", f"By position and outcome ({level} agreement; idea 5)",
              f"  {'':<44}{'labels':>7}{'agree':>7}"]
    for b, hits in sorted(by.items(), key=lambda kv: (kv[0] != "all", -len(kv[1]), kv[0])):
        lines.append(f"  {b[:44]:<44}{len(hits):>7}{_pct(sum(hits) / len(hits)):>7}")
    small = [g for g, n in Counter(r["player"] for r in rows).items() if n < 100]
    if small:
        lines += ["", f"Note: {len(small)} of {len({r['player'] for r in rows})} players have fewer than 100 labels; "
                      "their rates are mostly noise."]
    if len({r["labeller"] for r in rows}) > 1:
        lines.append("Note: several labelers, so per-player rates mix labeler and player (see Labeler / player).")
    return lines
