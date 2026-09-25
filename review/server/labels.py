"""Label mode: sessions and label events (docs/review-tool.md §4 and §8).

A session is one seat of one game for one labeller. Everything the browser gets goes through
`label_view`, which sends only what that seat could know by the furthest turn reached (the
"frontier"), with the players anonymised. The game ID is shown: labellers are trusted not to look it up.

A session is *active* from its start until the labeller *submits* it at the end of the game (every one of
their turns labelled); a submitted session is read-only. A session not submitted within 24 hours of its
start *expires*: its label events are dropped and it no longer counts anywhere. A (game, seat) with an
active or submitted session is *claimed*: it is never handed out again, picked or random, so two
labellers don't label the same seat (§4.4).

Turns are UI turns (1-based) everywhere: turn t is the position before action t, and turn T + 1 is
the final position. Storage, under review/labels/ by default:

    sessions/<session_id>.json   session state: seat, frontier, hints seen, submitted, expired (rewritten)
    <game_id>.jsonl              append-only events for that game (kinds: label, retract, hint, submit)
"""
import json
import os
import random
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bundle import ANON_NAMES

ROOT = Path(__file__).resolve().parents[2]
BUNDLES = ROOT / "review" / "build" / "bundles"
LABELS = ROOT / "review" / "labels"
SERVER = "new.playhanabi.com"
TOOL = "review/0.3"
SUITS = "RYGBPT"
EXPIRE_AFTER = timedelta(hours=24)

_lock = threading.RLock()
_bundles = {}  # path -> (mtime, bundle)


class LabelError(Exception):
    """A request the session doesn't allow; `status` is the HTTP status to answer with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return _iso(datetime.now(timezone.utc))


def _iso(t):
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def load_bundle(game_id, bundles=BUNDLES):
    path = Path(bundles) / f"{int(game_id)}.json"
    if not path.exists():
        raise LabelError(f"no bundle for game {game_id}", 404)
    mtime = path.stat().st_mtime
    cached = _bundles.get(path)
    if cached is None or cached[0] != mtime:
        cached = (mtime, json.loads(path.read_text()))
        _bundles[path] = cached
    return cached[1]


class Store:
    def __init__(self, root=LABELS, bundles=BUNDLES, expire_after=EXPIRE_AFTER):
        self.root = Path(root)
        self.bundles = Path(bundles)
        self.expire_after = expire_after
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)

    # ---- Games ------------------------------------------------------------------------------------

    def bundle(self, game_id):
        return load_bundle(game_id, self.bundles)

    def game_ids(self):
        return sorted(int(p.stem) for p in self.bundles.glob("*.json"))

    def labelable(self):
        """(game_id, bundle) for every game open for labelling: never one the engine gets wrong."""
        out = []
        for game_id in self.game_ids():
            b = self.bundle(game_id)
            if check_errors(b) == 0:
                out.append((game_id, b))
        return out

    # ---- Sessions ---------------------------------------------------------------------------------

    def _session_path(self, sid):
        if not sid.isalnum():
            raise LabelError("bad session id", 404)
        return self.root / "sessions" / f"{sid}.json"

    def session(self, sid):
        path = self._session_path(sid)
        if not path.exists():
            raise LabelError("no such session", 404)
        s = json.loads(path.read_text())
        if self._expire(s):
            raise LabelError("this session expired 24 hours after it started: its moves were dropped and the seat "
                             "is open again", 410)
        return s

    def _save(self, s):
        path = self._session_path(s["session_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        os.replace(tmp, path)

    def sessions(self, labeller=None):
        """Every session still in force (active or submitted): expired ones are left out."""
        out = [json.loads(p.read_text()) for p in sorted((self.root / "sessions").glob("*.json"))]
        return [s for s in out if not self._expire(s) and (labeller is None or s["labeller"] == labeller)]

    def expires(self, s):
        return _iso(_parse(s["started"]) + self.expire_after)

    def _expire(self, s):
        """Whether the session has expired. The first time it's found unsubmitted past its deadline, its
        events are dropped and it's marked `expired`, which frees its seat."""
        if s.get("expired"):
            return True
        if s.get("submitted") or self.expires(s) > _now():
            return False
        with _lock:
            path = self._events_path(s["game_id"])
            if path.exists():
                kept = [e for e in self.game_events(s["game_id"]) if e["session_id"] != s["session_id"]]
                tmp = path.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(e) + "\n" for e in kept))
                os.replace(tmp, path)
            s["expired"] = _now()
            self._save(s)
        return True

    def editable(self, sid):
        """The session, if it may still change: a submitted session is read-only."""
        s = self.session(sid)
        if s.get("submitted"):
            raise LabelError("this session has been submitted", 409)
        return s

    def claims(self, sessions=None):
        """(game_id, seat) -> the sessions on it (active or submitted), any labeller."""
        out = {}
        for s in self.sessions() if sessions is None else sessions:
            out.setdefault((s["game_id"], s["seat"]), []).append(s)
        return out

    def start(self, labeller, game_id=None, seat=None):
        """A new session: the given (game, seat) or, without a game, a random unclaimed one.
        A claimed seat is never handed out again (§4.4)."""
        labeller = labeller.strip()
        if not labeller:
            raise LabelError("labeler name is required")
        with _lock:
            claimed = self.claims()
            if game_id is None:
                candidates = [(game_id, k) for game_id, b in self.labelable()
                              for k in range(_players(b)) if (game_id, k) not in claimed]
                if not candidates:
                    raise LabelError("no open seats left: every seat is being labeled or submitted", 409)
                game_id, seat = random.choice(candidates)
                chosen_by = "random"
            else:
                if not isinstance(game_id, int) or isinstance(game_id, bool):
                    raise LabelError("no such game", 404)
                b = self.bundle(game_id)
                if check_errors(b):
                    raise LabelError("this game is not open for labeling", 409)
                if not isinstance(seat, int) or not 0 <= seat < _players(b):
                    raise LabelError("no such seat")
                if (game_id, seat) in claimed:
                    raise LabelError("someone else has already taken this seat", 409)
                chosen_by = "picked"
            s = {"session_id": secrets.token_hex(6), "game_id": game_id, "seat": seat, "labeller": labeller,
                 "chosen_by": chosen_by, "started": _now(), "ended": None, "submitted": None, "frontier": 1,
                 "frontier_at": _now(), "hints": [], "expired": None, "tool": TOOL}
            self._save(s)
            return s

    # ---- Events -----------------------------------------------------------------------------------

    def _events_path(self, game_id):
        return self.root / f"{int(game_id)}.jsonl"

    def game_events(self, game_id):
        path = self._events_path(game_id)
        if not path.exists():
            return []
        with path.open() as f:
            return [json.loads(line) for line in f if line.strip()]

    def events(self, s):
        return [e for e in self.game_events(s["game_id"]) if e["session_id"] == s["session_id"]]

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
            s = self.editable(sid)
            self._advance(s, self.bundle(s["game_id"]))
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
            s = self.editable(sid)
            b = self.bundle(s["game_id"])
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
            s = self.editable(sid)
            labels = self.current_labels(self.events(s))
            if not labels:
                raise LabelError("nothing to undo", 409)
            latest = max(labels.values(), key=lambda e: e["at"])
            self._append(s, "retract", replaces=latest["event_id"], turn=latest["turn"])
            return s, latest["turn"]

    def hint(self, sid, turn):
        with _lock:
            s = self.editable(sid)
            turn = self._own_turn(s, self.bundle(s["game_id"]), turn)
            if turn not in s["hints"]:
                s["hints"].append(turn)
                self._save(s)
                self._append(s, "hint", turn=turn, before_frontier=turn < s["frontier"])
            return s

    def submit(self, sid):
        """Hand in a finished session: the game played to the end and a move chosen at every own turn.
        The session is read-only from then on."""
        with _lock:
            s = self.editable(sid)
            b = self.bundle(s["game_id"])
            if s["frontier"] <= b["turns"]:
                raise LabelError("play the game to the end before submitting", 409)
            labels = self.current_labels(self.events(s))
            missing = [t for t in own_turns(b, s["seat"]) if t not in labels]
            if missing:
                raise LabelError(f"choose a move at turn {', '.join(map(str, missing))} before submitting", 409)
            s["submitted"] = _now()
            self._save(s)
            self._append(s, "submit", labels=len(labels))
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


def check_errors(b):
    return sum(1 for c in b["checks"] if not c["ok"] and c["kind"] == "error")


def _players(b):
    return len(b["game"]["export"]["players"])


def own_turns(b, seat, upto=None):
    """The seat's turns, up to `upto` (default: the whole game)."""
    last = b["turns"] if upto is None else min(upto, b["turns"])
    return [t for t in range(1, last + 1) if _actor(b, t) == seat]


def status(s):
    return "submitted" if s.get("submitted") else "active"


def seat_status(sessions):
    """A (game, seat)'s status from the sessions on it: submitted > active > open."""
    kinds = {status(s) for s in sessions}
    return "submitted" if "submitted" in kinds else "active" if kinds else "open"


def last_active(s, events):
    """The session's latest activity (ISO timestamps in one format compare as strings)."""
    return max([s["started"], s["frontier_at"], s.get("submitted") or ""] + [e["at"] for e in events])


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
    b = store.bundle(s["game_id"])
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
            "id": s["game_id"], "players": ANON_NAMES[:n], "deck": deck,
            "actions": [],
            "options": {k: options[k] for k in ("variant", "startingPlayer", "allOrNothing") if k in options}}},
        "turns": frontier - 1,
        "log": b["log_anon"][:frontier],
        "clues": [c for c in b["clues"] if c["turn"] < frontier],
        "positions": [{**p, "hands": []} for p in b["positions"][:frontier]],
        # .get: bundles built before sounds were added (2026-09-25) lack them; the browser then plays the standard one.
        "sounds": b.get("sounds", [])[:frontier],
        "seat_views": [[_public_obs(o) for o in views] if k == seat else [] for k in range(n)],
        "decisions": [],
        "checks": [],
    }
    events = store.events(s)
    own = own_turns(b, seat, frontier)
    labels = store.current_labels(events)
    return {
        "session": {**{k: s[k] for k in ("session_id", "seat", "labeller", "frontier", "ended", "hints")},
                    "submitted": s.get("submitted"), "game_id": s["game_id"]},
        "game_over": frontier > b["turns"],
        "own_turns": own,
        # .get: events written before `after_reveal` replaced `changed_after_reveal` (2026-09-24) lack it.
        "labels": {t: {k: e.get(k, False) for k in ("choice", "also_ok", "hint_used", "after_reveal")}
                   for t, e in labels.items()},
        # The real move at your turns once it's public (turn passed) or asked for with the hint.
        "actual": {t: actual_move(b, t) for t in own if t < frontier or t in s["hints"]},
        "bundle": redacted,
    }


def session_summary(store, s, events=None):
    """What the lobby shows for a session."""
    b = store.bundle(s["game_id"])
    ex = b["game"]["export"]
    events = store.events(s) if events is None else events
    labels = store.current_labels(events)
    own = own_turns(b, s["seat"])
    return {"session_id": s["session_id"], "game_id": s["game_id"], "labeller": s["labeller"],
            "status": status(s), "started": s["started"], "expires": store.expires(s), "submitted": s.get("submitted"),
            "last_active": last_active(s, events), "game_over": s["frontier"] > b["turns"],
            "players": len(ex["players"]), "variant": ex.get("options", {}).get("variant", "No Variant"),
            "you": ANON_NAMES[s["seat"]], "turn": s["frontier"], "labels": len(labels), "own_turns": len(own)}


def board(store, labeller):
    """Every game open for labelling and who is on each seat, for the lobby (§4.4), newest game first."""
    sessions = store.sessions()
    claims = store.claims(sessions)
    rows = []
    for game_id, b in store.labelable():
        seats = []
        for k in range(_players(b)):
            on = sorted(claims.get((game_id, k), []), key=lambda s: s["started"])
            yours = next((s for s in on if s["labeller"] == labeller), None)
            seats.append({"name": ANON_NAMES[k], "status": seat_status(on), "labellers": [s["labeller"] for s in on],
                          "session_id": yours["session_id"] if yours else None,
                          "available": not on})
        rows.append({"game_id": game_id, "players": len(seats),
                     "variant": b["game"]["export"].get("options", {}).get("variant", "No Variant"), "seats": seats})
    return sorted(rows, key=lambda r: -r["game_id"])


def admin_report(store):
    """Coverage and contributions at a glance, with real player names (admin only)."""
    sessions = store.sessions()
    claims = store.claims(sessions)
    events = {}  # session_id -> its events
    for game_id in {s["game_id"] for s in sessions}:
        for e in store.game_events(game_id):
            events.setdefault(e["session_id"], []).append(e)
    rows = {}  # session_id -> admin session row
    for s in sessions:
        ev = events.get(s["session_id"], [])
        labels = store.current_labels(ev)
        b = store.bundle(s["game_id"])
        rows[s["session_id"]] = {
            **session_summary(store, s, ev), "seat": s["seat"],
            "chosen_by": s.get("chosen_by"), "turns": b["turns"], "hints": len(s["hints"]),
            "hint_labels": sum(1 for e in labels.values() if e.get("hint_used")),
            "after_reveal": sum(1 for e in labels.values() if e.get("after_reveal")),
            "ms": [e["ms_to_choice"] for e in labels.values() if isinstance(e.get("ms_to_choice"), (int, float))]}

    games, totals = [], {"games": 0, "labelable_games": 0, "seats": 0, "open": 0, "active": 0, "submitted": 0,
                         "own_turns": 0, "own_turns_submitted": 0}
    for game_id in store.game_ids():
        b = store.bundle(game_id)
        ex = b["game"]["export"]
        errors = check_errors(b)
        totals["games"] += 1
        seats = []
        for k in range(_players(b)):
            on = sorted(claims.get((game_id, k), []), key=lambda s: s["started"])
            st, n_own = seat_status(on), len(own_turns(b, k))
            seats.append({"seat": k, "player": ex["players"][k], "anon": ANON_NAMES[k], "status": st,
                          "own_turns": n_own,
                          "sessions": [{key: rows[s["session_id"]][key] for key in
                                        ("session_id", "labeller", "status", "turn", "labels", "last_active")}
                                       for s in on]})
            if not errors:
                totals["seats"] += 1
                totals[st] += 1
                totals["own_turns"] += n_own
                totals["own_turns_submitted"] += n_own if st == "submitted" else 0
        totals["labelable_games"] += not errors
        final = b["positions"][-1]["board"]
        options = ex.get("options", {})
        games.append({"id": game_id, "players": ex["players"], "variant": options.get("variant", "No Variant"),
                      "turns": b["turns"], "suits": len(final["stacks"]),
                      # Cards played (the stacks' sum) in every mode, not the site's score, which All or
                      # Nothing makes 0 for any game short of the maximum.
                      "score": sum(final["stacks"].values()), "max_score": 5 * len(final["stacks"]),
                      "bombs": final["strikes"], "errors": errors, "seats": seats})

    people = {}
    for r in rows.values():
        p = people.setdefault(r["labeller"], {"labeller": r["labeller"], "active": 0, "submitted": 0, "labels": 0,
                                              "labels_submitted": 0, "hint_labels": 0, "after_reveal": 0, "ms": [],
                                              "first": r["started"], "last_active": r["last_active"]})
        p[r["status"]] += 1
        p["labels"] += r["labels"]
        p["labels_submitted"] += r["labels"] if r["status"] == "submitted" else 0
        p["hint_labels"] += r["hint_labels"]
        p["after_reveal"] += r["after_reveal"]
        p["ms"] += r["ms"]
        p["first"] = min(p["first"], r["started"])
        p["last_active"] = max(p["last_active"], r["last_active"])
    labellers = []
    for p in people.values():
        p["median_ms"] = _median(p.pop("ms"))
        labellers.append(p)
    for r in rows.values():
        r["median_ms"] = _median(r.pop("ms"))

    totals.update(labellers=len(labellers), labels=sum(r["labels"] for r in rows.values()),
                  sessions_active=sum(1 for r in rows.values() if r["status"] == "active"),
                  sessions_submitted=sum(1 for r in rows.values() if r["status"] == "submitted"))
    return {"generated": _now(), "totals": totals,
            "labellers": sorted(labellers, key=lambda p: (-p["labels"], p["labeller"])),
            "sessions": sorted(rows.values(), key=lambda r: r["last_active"], reverse=True),
            "games": games}


def _median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2
