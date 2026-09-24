"""Captured websocket messages -> GameRecord (docs/representation.md §3.4, §6).

A capture is the sequence of messages one client received, as (command, data) pairs:
- `init`: player names, our seat, options, table ID, and the seed (which is dropped, see §3.4)
- `gameActionList`: every event so far; sent on joining and on every reload, so it replaces what
  came before
- `gameAction`: one more event

`parse_capture` reads the text form used for `prototype/examples/live_*.txt`: one message per line,
`<command> <json>`.

The record's `view` follows from the data: the seat whose draws are hidden, or None if nothing is
hidden (a finished game, reloaded, sends everything).
"""
from __future__ import annotations

import json
from typing import Iterable, List, Optional, Tuple

from .engine import positions
from .record import SCHEMA, SERVER
from .rules import SUIT_LETTERS, InvalidGame, Rules, identity_str

IGNORED = {"playerTimes"}


class ConversionError(ValueError):
    """The messages don't form a valid game stream."""


def parse_capture(text: str) -> List[Tuple[str, dict]]:
    messages = []
    for line in text.splitlines():
        if line.strip():
            command, _, body = line.strip().partition(" ")
            messages.append((command, json.loads(body)))
    return messages


def from_live(messages: Iterable[Tuple[str, dict]], *, players: Optional[List[str]] = None,
              options: Optional[dict] = None, table_id: Optional[int] = None,
              fetched_at: Optional[str] = None, server: str = SERVER) -> dict:
    """`players` and `options` come from the `init` message when there is one; pass them otherwise.
    `options` is in the site's format (see `Rules.from_site_options`)."""
    messages = list(messages)
    init, stream = None, []
    for command, data in messages:
        if command == "init":
            init = data
        elif command == "gameActionList":
            stream = list(data["list"])
            table_id = data.get("tableID", table_id)
        elif command == "gameAction":
            stream.append(data["action"])
            table_id = data.get("tableID", table_id)
    game_id = None
    if init is not None:
        players, options = init["playerNames"], init.get("options", {})
        table_id = init.get("tableID", table_id)
        game_id = init["databaseID"] if init.get("databaseID", -1) > 0 else None
    if players is None:
        raise ConversionError("no `init` message: pass players= and options=")
    rules = Rules.from_site_options(options or {}, len(players))
    events, hidden_seats = _events(stream, rules)
    if len(hidden_seats) > 1:
        raise ConversionError(f"draws are hidden for seats {sorted(hidden_seats)}; a player sees all but their own")
    view = next(iter(hidden_seats)) if hidden_seats else None
    if init is not None and view is not None and view != init.get("ourPlayerIndex"):
        raise ConversionError(f"hidden draws are seat {view}'s, but ourPlayerIndex is {init.get('ourPlayerIndex')}")

    return {
        "schema": SCHEMA,
        "source": {"kind": "live", "server": server, "game_id": game_id, "table_id": table_id,
                   "fetched_at": fetched_at},
        "view": view,
        "players": list(players),
        "options": rules.to_record(),
        "events": events,
        "listing": None,
        "raw": {"messages": [[c, _without_seed(c, d)] for c, d in messages]},
    }


def _without_seed(command: str, data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "seed"} if command == "init" else data


def _identity(a: dict, rules: Rules) -> Optional[str]:
    s, r = a.get("suitIndex"), a.get("rank")
    if s == -1 and r == -1:
        return None
    if not (isinstance(s, int) and 0 <= s < rules.suits and r in (1, 2, 3, 4, 5)):
        raise ConversionError(f"bad card {a}")
    return identity_str((s, r))


def _events(stream: List[dict], rules: Rules) -> Tuple[List[dict], set]:
    events, hidden, strike = [], set(), None
    for a in stream:
        kind = a.get("type")
        if strike is not None and not (kind == "discard" and a.get("failed")):
            raise ConversionError(f"strike {strike} not followed by a failed discard")
        if kind == "draw":
            ident = _identity(a, rules)
            if ident is None:
                hidden.add(a["playerIndex"])
            events.append({"e": "draw", "seat": a["playerIndex"], "card": a["order"], "id": ident})
        elif kind == "clue":
            clue = a["clue"]
            if clue["type"] == 0:
                if not 0 <= clue["value"] < rules.suits:
                    raise ConversionError(f"bad colour clue {a}")
                kind_, value = "color", SUIT_LETTERS[clue["value"]]
            else:
                kind_, value = "rank", clue["value"]
            events.append({"e": "clue", "by": a["giver"], "to": a["target"], "kind": kind_, "value": value,
                           "touched": sorted(a["list"])})
        elif kind == "play":
            events.append({"e": "play", "by": a["playerIndex"], "card": a["order"],
                           "id": _identity(a, rules), "ok": True})
        elif kind == "strike":
            strike = a
        elif kind == "discard":
            if a.get("failed"):
                if strike is None or strike.get("order") != a["order"]:
                    raise ConversionError(f"failed discard {a} without a matching strike")
                events.append({"e": "play", "by": a["playerIndex"], "card": a["order"],
                               "id": _identity(a, rules), "ok": False})
                strike = None
            else:
                events.append({"e": "discard", "by": a["playerIndex"], "card": a["order"],
                               "id": _identity(a, rules)})
        elif kind == "gameOver":
            seat = a.get("playerIndex", -1)
            events.append({"e": "end", "condition": a["endCondition"], "seat": seat if seat >= 0 else None})
        elif kind in ("status", "turn") or kind in IGNORED:
            continue
        else:
            raise ConversionError(f"unknown event type {kind!r}: {a}")
    if strike is not None:
        raise ConversionError(f"stream ends after strike {strike}")
    return events, hidden


def check_stream(record: dict) -> List[str]:
    """Compare the engine's replay of a live record with what the server said along the way:
    `status` (clues, score, reachable max), `turn` (whose turn), and the `turn`/`num` fields of clues
    and strikes. Returns a list of problems (empty if everything matches)."""
    stream = []
    for command, data in record["raw"]["messages"]:
        if command == "gameActionList":
            stream = list(data["list"])
        elif command == "gameAction":
            stream.append(data["action"])

    # State after k actions, for every k (k = 0 is right after the deal).
    states = []
    try:
        for engine, _ in positions(record):
            states.append({"clues": engine.clues, "score": engine.score, "max": engine.max_score,
                           "strikes": engine.strikes, "active": -1 if engine.over else engine.active})
    except InvalidGame as e:
        return [f"replay: {e}"]

    problems, done = [], 0  # done = actions seen so far in the stream
    for a in stream:
        kind = a.get("type")
        # A misplay is `strike` + failed `discard`; count it once, at the strike.
        if kind in ("clue", "play", "strike") or (kind == "discard" and not a.get("failed")):
            if kind in ("clue", "strike") and a.get("turn") != done:
                problems.append(f"{kind} {a} has turn {a.get('turn')}, expected {done}")
            if kind == "strike" and a.get("num") != states[done]["strikes"] + 1:
                problems.append(f"strike {a} has num {a.get('num')}, expected {states[done]['strikes'] + 1}")
            done += 1
        elif kind == "status":
            s = states[done]
            got, want = (a["clues"], a["score"], a["maxScore"]), (s["clues"], s["score"], s["max"])
            if got != want:
                problems.append(f"status after {done} actions: server (clues, score, max) {got}, engine {want}")
        elif kind == "turn":
            s = states[done]
            if (a["num"], a["currentPlayerIndex"]) != (done, s["active"]):
                problems.append(f"turn message {a}: engine has turn {done}, seat {s['active']}")
    return problems
