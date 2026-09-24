"""Label mode: sessions and label events (docs/review-tool.md §4 and §8).

A session is one seat of one game for one labeller. Everything the browser gets goes through
`label_view`, which sends only what that seat could know by the furthest turn reached (the
"frontier"), with the players anonymised and nothing that identifies the game.

Turns are UI turns (1-based) everywhere: turn t is the position before action t, and turn T + 1 is
the final position. Storage, under review/labels/ by default:

    sessions/<session_id>.json   session state: seat, frontier, hints seen (rewritten)
    <game_id>.jsonl              append-only events for that game (kinds: label, retract, hint)
"""
import json
import os
import random
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from bundle import ANON_NAMES

ROOT = Path(__file__).resolve().parents[2]
BUNDLES = ROOT / "review" / "build" / "bundles"
LABELS = ROOT / "review" / "labels"
SERVER = "new.playhanabi.com"
TOOL = "review/0.3"
SUITS = "RYGBPT"

_lock = threading.Lock()
_bundles = {}  # path -> (mtime, bundle)


class LabelError(Exception):
    """A request the session doesn't allow; `status` is the HTTP status to answer with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_bundle(game_id):
    path = BUNDLES / f"{game_id}.json"
    if not path.exists():
        raise LabelError(f"no bundle for game {game_id}", 404)
    mtime = path.stat().st_mtime
    cached = _bundles.get(path)
    if cached is None or cached[0] != mtime:
        cached = (mtime, json.loads(path.read_text()))
        _bundles[path] = cached
    return cached[1]


class Store:
    def __init__(self, root=LABELS):
        self.root = Path(root)
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)

    # ---- Sessions ---------------------------------------------------------------------------------

    def _session_path(self, sid):
        if not sid.isalnum():
            raise LabelError("bad session id", 404)
        return self.root / "sessions" / f"{sid}.json"

    def session(self, sid):
        path = self._session_path(sid)
        if not path.exists():
            raise LabelError("no such session", 404)
        return json.loads(path.read_text())

    def _save(self, s):
        path = self._session_path(s["session_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        os.replace(tmp, path)

    def sessions(self, labeller=None):
        out = [json.loads(p.read_text()) for p in sorted((self.root / "sessions").glob("*.json"))]
        return [s for s in out if labeller is None or s["labeller"] == labeller]

    def start(self, labeller):
        """A new session on a random (game, seat). A labeller gets at most one seat per game (§4.3)."""
        labeller = labeller.strip()
        if not labeller:
            raise LabelError("labeller name is required")
        with _lock:
            done = {s["game_id"] for s in self.sessions(labeller)}
            candidates = []
            for path in sorted(BUNDLES.glob("*.json")):
                game_id = int(path.stem)
                if game_id in done:
                    continue
                b = load_bundle(game_id)
                if any(not c["ok"] and c["kind"] == "error" for c in b["checks"]):
                    continue  # never label a game the engine gets wrong
                candidates += [(game_id, seat) for seat in range(len(b["game"]["export"]["players"]))]
            if not candidates:
                raise LabelError("no games left to label for this labeller", 409)
            game_id, seat = random.choice(candidates)
            s = {"session_id": secrets.token_hex(6), "game_id": game_id, "seat": seat, "labeller": labeller,
                 "chosen_by": "random", "started": _now(), "ended": None, "frontier": 1,
                 "frontier_at": _now(), "hints": [], "tool": TOOL}
            self._save(s)
            return s

    # ---- Events -----------------------------------------------------------------------------------

    def _events_path(self, game_id):
        return self.root / f"{int(game_id)}.jsonl"

    def events(self, s):
        path = self._events_path(s["game_id"])
        if not path.exists():
            return []
        with path.open() as f:
            return [e for e in map(json.loads, f) if e["session_id"] == s["session_id"]]

    def _append(self, s, kind, **fields):
        ev = {"kind": kind, "event_id": secrets.token_hex(8), "server": SERVER, "game_id": s["game_id"],
              "seat": s["seat"], "labeller": s["labeller"], "session_id": s["session_id"], "at": _now(),
              "tool": TOOL, **fields}
        self.root.mkdir(parents=True, exist_ok=True)
        with self._events_path(s["game_id"]).open("a") as f:
            f.write(json.dumps(ev) + "\n")
        return ev

    @staticmethod
    def current_labels(events):
        """The label in force at each turn: later label events replace earlier ones; retract removes one."""
        labels, by_id = {}, {}
        for e in events:
            if e["kind"] == "label":
                labels[e["turn"]] = e
                by_id[e["event_id"]] = e
            elif e["kind"] == "retract":
                old = by_id.get(e["replaces"])
                if old is not None and labels.get(old["turn"]) is old:
                    del labels[old["turn"]]
        return labels

    # ---- Actions ----------------------------------------------------------------------------------

    def advance(self, sid):
        with _lock:
            s = self.session(sid)
            self._advance(s, load_bundle(s["game_id"]))
            return s

    def _advance(self, s, b):
        t = s["frontier"]
        if t > b["turns"]:
            raise LabelError("the game is over", 409)
        if _actor(b, t) == s["seat"] and t not in self.current_labels(self.events(s)):
            raise LabelError("choose a move before going on", 409)
        s["frontier"] = t + 1
        s["frontier_at"] = _now()
        if s["frontier"] > b["turns"]:
            s["ended"] = _now()
        self._save(s)

    def label(self, sid, turn, choice, also_ok=(), ms_to_choice=None, advance=False):
        """Record the labeller's move for `turn`, checked against that turn's legal moves. With `advance`,
        a label at the frontier also reveals the next move (the live game goes on as soon as you move)."""
        with _lock:
            s = self.session(sid)
            b = load_bundle(s["game_id"])
            turn = self._own_turn(s, b, turn)
            obs = b["seat_views"][s["seat"]][turn - 1]
            n = len(b["game"]["export"]["players"])
            if choice is None:
                raise LabelError("choose a move")
            for m in [choice, *also_ok]:
                if _to_legal(m, obs, s["seat"], n) not in obs["legal"]:
                    raise LabelError(f"not a legal move at turn {turn}: {json.dumps(m)}")
            old = self.current_labels(self.events(s)).get(turn)
            hint_used = turn in s["hints"]
            self._append(s, "label", replaces=old["event_id"] if old else None, turn=turn,
                         choice=choice, also_ok=list(also_ok), hint_used=hint_used,
                         # Hindsight: the real move at this turn had already been revealed (the game went on
                         # past it). The first label at the frontier never is; labels after an undo always are.
                         after_reveal=s["frontier"] > turn,
                         ms_to_choice=ms_to_choice)
            if advance and turn == s["frontier"]:
                self._advance(s, b)
            return s

    def retract(self, sid):
        """Undo the latest label still in force. Returns (session, turn it was at)."""
        with _lock:
            s = self.session(sid)
            labels = self.current_labels(self.events(s))
            if not labels:
                raise LabelError("nothing to undo", 409)
            latest = max(labels.values(), key=lambda e: e["at"])
            self._append(s, "retract", replaces=latest["event_id"], turn=latest["turn"])
            return s, latest["turn"]

    def hint(self, sid, turn):
        with _lock:
            s = self.session(sid)
            turn = self._own_turn(s, load_bundle(s["game_id"]), turn)
            if turn not in s["hints"]:
                s["hints"].append(turn)
                self._save(s)
                self._append(s, "hint", turn=turn, before_frontier=turn < s["frontier"])
            return s

    @staticmethod
    def _reached(s, turn):
        turn = int(turn)
        if not 1 <= turn <= s["frontier"]:
            raise LabelError(f"turn {turn} not reached yet", 403)
        return turn

    def _own_turn(self, s, b, turn):
        turn = self._reached(s, turn)
        if turn > b["turns"] or _actor(b, turn) != s["seat"]:
            raise LabelError(f"turn {turn} is not your turn")
        return turn


def _actor(b, turn):
    ex = b["game"]["export"]
    return (turn - 1 + int(ex.get("options", {}).get("startingPlayer", 0))) % len(ex["players"])


def _to_legal(choice, obs, seat, n):
    """A label choice (absolute seats, card IDs; §8) in the DecisionRecord's `legal` format."""
    if not isinstance(choice, dict):
        return None
    if choice.get("type") in ("play", "discard") and set(choice) == {"type", "card"}:
        slot = next((sl["slot"] for sl in obs["hands"][0]["slots"] if sl["card"] == choice["card"]), None)
        return {"type": choice["type"], "slot": slot}
    if choice.get("type") == "clue" and set(choice) == {"type", "to_seat", "kind", "value"}:
        if not isinstance(choice["to_seat"], int):
            return None
        return {"type": "clue", "to": (choice["to_seat"] - seat) % n, "kind": choice["kind"], "value": choice["value"]}
    return None


def actual_move(b, turn):
    """The move actually made at `turn`, in the label choice format."""
    a = b["game"]["export"]["actions"][turn - 1]
    if a["type"] in (0, 1):
        return {"type": "play" if a["type"] == 0 else "discard", "card": a["target"]}
    return {"type": "clue", "to_seat": a["target"], "kind": "color" if a["type"] == 2 else "rank",
            "value": SUITS[a["value"]] if a["type"] == 2 else a["value"]}


def _public_obs(obs):
    # The table needs board, cards, hands and legal; history and unseen are left out to keep it small.
    return {k: v for k, v in obs.items() if k not in ("history", "unseen")}


def label_view(store, s):
    """Everything the browser gets in Label mode: a bundle cut off at the frontier, redacted (§4.3, §6.1)."""
    b = load_bundle(s["game_id"])
    ex = b["game"]["export"]
    n, seat, frontier = len(ex["players"]), s["seat"], s["frontier"]
    views = b["seat_views"][seat][:frontier]
    known = views[-1]["cards"]  # everything this seat has seen by the frontier
    deck = [None if c is None else {"suitIndex": SUITS.index(c[0]), "rank": int(c[1:])} for c in known]
    options = ex.get("options", {})
    redacted = {
        "schema": b["schema"],
        "engine": b["engine"],
        "game": {"export": {
            "id": 0, "players": ANON_NAMES[:n], "deck": deck,
            "actions": [],
            "options": {k: options[k] for k in ("variant", "startingPlayer", "allOrNothing") if k in options}}},
        "turns": frontier - 1,
        "log": b["log_anon"][:frontier],
        "clues": [c for c in b["clues"] if c["turn"] < frontier],
        "positions": [{**p, "hands": []} for p in b["positions"][:frontier]],
        "seat_views": [[_public_obs(o) for o in views] if k == seat else [] for k in range(n)],
        "decisions": [],
        "checks": [],
    }
    events = store.events(s)
    own = [t for t in range(1, min(frontier, b["turns"]) + 1) if _actor(b, t) == seat]
    labels = store.current_labels(events)
    return {
        "session": {k: s[k] for k in ("session_id", "seat", "labeller", "frontier", "ended", "hints")},
        "game_over": frontier > b["turns"],
        "own_turns": own,
        # .get: events written before `after_reveal` replaced `changed_after_reveal` (2026-09-24) lack it.
        "labels": {t: {k: e.get(k, False) for k in ("choice", "also_ok", "hint_used", "after_reveal")}
                   for t, e in labels.items()},
        # The real move at your turns once it's public (turn passed) or asked for with the hint.
        "actual": {t: actual_move(b, t) for t in own if t < frontier or t in s["hints"]},
        "bundle": redacted,
    }


def session_summary(store, s):
    """What the lobby shows for a session. No game ID: the labeller mustn't be able to look the game up."""
    b = load_bundle(s["game_id"])
    ex = b["game"]["export"]
    labels = store.current_labels(store.events(s))
    return {"session_id": s["session_id"], "labeller": s["labeller"], "started": s["started"], "ended": s["ended"],
            "players": len(ex["players"]), "variant": ex.get("options", {}).get("variant", "No Variant"),
            "you": ANON_NAMES[s["seat"]], "turn": s["frontier"], "labels": len(labels)}
