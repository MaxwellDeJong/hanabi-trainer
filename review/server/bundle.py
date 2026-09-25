"""Build a review bundle (docs/review-tool.md §6.1) for one game and check it against hanab.live's reducer.

    python3 review/server/bundle.py examples/export_78921.json data/exports/export_*.json [--out DIR] [--no-oracle]

Game state comes from the engine (`hanabi_data`, docs/representation.md). The oracle (review/oracle,
see its README) replays the same export through hanab.live's own reducer; every position is compared
and the results go into the bundle's `checks`.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hanabi_data import ENGINE_VERSION, from_export, positions  # noqa: E402
from hanabi_data import summarize as summarize_game  # noqa: E402
from hanabi_data.decision import build  # noqa: E402
from hanabi_data.record import is_game_record  # noqa: E402
from hanabi_data.rules import COPIES, SUIT_LETTERS, End  # noqa: E402

SCHEMA = "hanabi-review-bundle/v0"
SUIT_NAMES = {"R": "Red", "Y": "Yellow", "G": "Green", "B": "Blue", "P": "Purple", "T": "Teal"}
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six"]
ORACLE = ROOT / "review" / "oracle" / "oracle.mjs"
SUFFIXES = (" (clued)", " (blind)", " (critical)")
# Label mode shows players by seat under these names (docs/review-tool.md §4.3).
ANON_NAMES = ["Alice", "Bob", "Cathy", "Donald", "Emily"]


# ---------------------------------------------------------------------------------------------------
# Log lines in the site's wording

def site_log(export, events):
    """One line per event, worded as new.playhanabi.com words it (docs/review-tool.md §2).

    Line 0 is "X goes first"; line k is the k-th action. Differences from hanab.live at c1d970b that
    we have seen on the site: a failed play of a clued card ends in " (clued)" (screenshot 2), where
    c1d970b prints no suffix. Suffixes for blind plays and for failed plays of unclued cards have not
    been seen on the site yet; we follow the discard rules for failed plays (critical > clued > none).
    """
    names, deck = export["players"], export["deck"]
    card = lambda cid: f"{SUIT_NAMES[SUIT_LETTERS[deck[cid]['suitIndex']]]} {deck[cid]['rank']}"
    touched_ever, discarded = set(), []
    lines = [f"{names[0]} goes first"]
    for ev in events[1:]:
        who = names[ev["by"]]
        if ev["e"] == "clue":
            value = SUIT_NAMES[ev["value"]] if ev["kind"] == "color" else str(ev["value"])
            plural = "s" if len(ev["touched"]) != 1 else ""
            lines.append(f"{who} tells {names[ev['to']]} about {NUMBER_WORDS[len(ev['touched'])]} {value}{plural}")
            touched_ever.update(ev["touched"])
            continue
        cid, clued = ev["card"], ev["card"] in touched_ever
        where = f"from slot #{ev['slot']}"
        if ev["e"] == "play" and ev["ok"]:
            lines.append(f"{who} plays {card(cid)} {where}" + ("" if clued else " (blind)"))
        else:
            critical = _is_critical(deck, cid, discarded, events, ev["t"])
            suffix = " (critical)" if critical else " (clued)" if clued else ""
            verb = "discards" if ev["e"] == "discard" else "fails to play"
            lines.append(f"{who} {verb} {card(cid)} {where}{suffix}")
            discarded.append(cid)
    return lines


def _is_critical(deck, cid, discarded_before, events, t):
    """The last copy of a card that is still needed (hanab.live: isCardCritical && isCardNeededForMaxScore)."""
    s, r = deck[cid]["suitIndex"], deck[cid]["rank"]
    same = lambda c: deck[c]["suitIndex"] == s and deck[c]["rank"] == r
    if sum(1 for c in discarded_before if same(c)) != COPIES[r] - 1:
        return False
    played = {e["card"] for e in events[1:] if e["t"] < t and e["e"] == "play" and e["ok"]}
    if any(same(c) for c in played):
        return False
    # Still needed only if no lower rank of the suit is gone for good.
    for lower in range(1, r):
        copies = [c for c in discarded_before if deck[c]["suitIndex"] == s and deck[c]["rank"] == lower]
        if len(copies) == COPIES[lower]:
            return False
    return True


# ---------------------------------------------------------------------------------------------------
# Sound effects

def sound_effects(events, positions_out, end, score, max_score):
    """The site's sound for each position, i.e. for the action that led to it (hanab.live's
    getSoundType.ts at c1d970b): the name of a file in public/sounds, or None for the standard sound,
    turn-us or turn-other, which depends on who is listening (the browser picks it).

    Left out, as on the site with its default settings: the H-group sounds (discarding a clued card,
    double discards, order chop moves) and the variant sounds (moo, oink, quack), which the engine's
    variants don't have.
    """
    out = [None]  # position 0: the deal
    touched, blind, misplays = set(), 0, 0  # cards ever clued; blind plays and misplays in a row
    for ev in events[1:]:
        k, sound = ev["t"], None
        sad = positions_out[k]["max_score"] < positions_out[k - 1]["max_score"]
        if ev["e"] == "clue":
            touched.update(ev["touched"])
            blind = misplays = 0
        elif ev["e"] == "play" and not ev["ok"]:
            misplays, blind = misplays + 1, 0
            sound = "turn-fail2" if misplays == 2 else "turn-fail1"
        else:
            is_blind = ev["e"] == "play" and ev["card"] not in touched
            misplays, blind = 0, blind + 1 if is_blind else 0
            sound = "turn-sad" if sad else f"turn-blind{min(blind, 6)}" if is_blind else None
        out.append(sound)
    if end is not None:
        # The site plays the turn sound and mutes it straight away for this one.
        out[-1] = ("finished-fail" if end["condition"] != End.NORMAL
                   else "finished-perfect" if score == max_score else "finished-success")
    return out


# ---------------------------------------------------------------------------------------------------
# Bundle

def build_bundle(game_doc, oracle=None):
    """`game_doc` is a GameRecord from an export, a raw export, or {"export": ..., "listing": ...}."""
    if is_game_record(game_doc):
        record = game_doc
        game_doc = {"export": record["raw"], **({"listing": record["listing"]} if record.get("listing") else {})}
    else:
        game_doc = game_doc if "export" in game_doc else {"export": game_doc}
        record = from_export(game_doc["export"], listing=game_doc.get("listing"))
    export = game_doc["export"]
    n, summary = len(export["players"]), summarize_game(record)
    T = summary["total_turns"]

    decisions, seat_views, positions_out = [], [[] for _ in range(n)], []
    for engine, action in positions(record):
        if action is not None:
            decisions.append(build(record, engine, engine.active, action, summary))
        for s in range(n):
            seat_views[s].append(build(record, engine, s, action, summary)["obs"])
        # Seen by seat 0, so relative seats are absolute; card IDs are shown for every slot.
        positions_out.append({"turn": engine.turn + 1, "board": seat_views[0][-1]["board"],
                              "max_score": engine.max_score, "hands": [list(h) for h in engine.hands]})
    events = engine.history

    bundle = {
        "schema": SCHEMA,
        "engine": f"hanabi_data {ENGINE_VERSION}",
        "game": game_doc,
        "turns": T,
        "log": site_log(export, events),
        "log_anon": site_log({**export, "players": ANON_NAMES[:n]}, events),
        "clues": [{"turn": e["t"], "giver": e["by"], "target": e["to"], "kind": e["kind"], "value": e["value"],
                   "touched": e["touched"], "missed": e["missed"]} for e in events if e["e"] == "clue"],
        "positions": positions_out,
        "sounds": sound_effects(events, positions_out, engine.end, engine.score, engine.rules.max_score),
        "seat_views": seat_views,
        "decisions": decisions,
    }
    bundle["checks"] = invariant_checks(bundle) + (oracle_checks(bundle, oracle) if oracle else [])
    return bundle


# ---------------------------------------------------------------------------------------------------
# Checks

def _check(checks, turn, name, ok, detail=None, kind="error"):
    checks.append({"turn": turn, "check": name, "ok": bool(ok), "kind": kind,
                   **({"detail": detail} if detail and not ok else {})})


def invariant_checks(bundle):
    checks = []
    export = bundle["game"]["export"]
    total = len(export["deck"])
    for pos in bundle["positions"]:
        b, t = pos["board"], pos["turn"]
        in_hands = sum(len(h) for h in pos["hands"])
        count = b["deck"] + in_hands + b["score"] + len(b["discards"])
        _check(checks, t, "cards_conserved", count == total, f"{count} != {total}")
    for s, views in enumerate(bundle["seat_views"]):
        for i, obs in enumerate(views):
            t = i + 1
            own = obs["hands"][0]["slots"]
            _check(checks, t, "own_cards_hidden", all(obs["cards"][sl["card"]] is None and sl["id"] is None for sl in own),
                   f"seat {s}")
            hidden = sum(sum(v) for v in obs["unseen"].values())
            expected = obs["board"]["deck"] + len(own)
            _check(checks, t, "unseen_matches", hidden == expected, f"seat {s}: {hidden} != {expected}")
            if i + 1 < len(views):
                nxt = views[i + 1]["history"]
                _check(checks, t, "history_extends", nxt[:len(obs["history"])] == obs["history"], f"seat {s}")
    for i, d in enumerate(bundle["decisions"]):
        label = {k: v for k, v in d["label"].items() if k != "card"}
        _check(checks, i + 1, "label_legal", label in d["obs"]["legal"], json.dumps(d["label"]))
    return checks


def run_oracle(path):
    out = subprocess.run(["node", str(ORACLE), str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def oracle_checks(bundle, oracle):
    """Compare every position with hanab.live's reducer. Log wording differences are kind "wording"."""
    checks = []
    export = bundle["game"]["export"]
    suit_of = lambda cid: export["deck"][cid]["suitIndex"]
    theirs_all = oracle["positions"]
    _check(checks, None, "oracle_positions", len(theirs_all) == len(bundle["positions"]),
           f"{len(theirs_all)} != {len(bundle['positions'])}")
    for ours, theirs in zip(bundle["positions"], theirs_all):
        b, t = ours["board"], ours["turn"]
        for field in ("score", "clues", "strikes", "deck", "pace"):
            _check(checks, t, f"oracle_{field}", b[field] == theirs[field], f"ours {b[field]} vs {theirs[field]}")
        _check(checks, t, "oracle_max_score", ours["max_score"] == theirs["max_score"],
               f"ours {ours['max_score']} vs {theirs['max_score']}")
        stacks = list(b["stacks"].values())
        _check(checks, t, "oracle_stacks", stacks == theirs["stacks"], f"ours {stacks} vs {theirs['stacks']}")
        piles = [[c for c in b["discards"] if suit_of(c) == s] for s in range(len(stacks))]
        _check(checks, t, "oracle_discards", piles == theirs["discard_piles"], f"ours {piles} vs {theirs['discard_piles']}")
        _check(checks, t, "oracle_hands", ours["hands"] == theirs["hands"], f"ours {ours['hands']} vs {theirs['hands']}")
        so_far = [c for c in bundle["clues"] if c["turn"] < t]
        value = lambda c: SUIT_LETTERS.index(c["value"]) if isinstance(c["value"], str) else c["value"]
        key = lambda c: (c["turn"], c["giver"], c["target"], c["kind"], value(c), sorted(c["touched"]), sorted(c["missed"]))
        mine, theirs_c = [key(c) for c in so_far], [key(c) for c in theirs["clue_log"]]
        _check(checks, t, "oracle_clues", mine == theirs_c, f"ours {mine} vs {theirs_c}")
    # Log lines: the facts must match exactly; suffix differences are a known version difference.
    ours_log, theirs_log = bundle["log"], theirs_all[-1]["log"]
    _check(checks, None, "oracle_log_length", len(ours_log) == len(theirs_log), f"{len(ours_log)} != {len(theirs_log)}")
    strip = lambda line: next((line[: -len(s)] for s in SUFFIXES if line.endswith(s)), line)
    for k, (a, b) in enumerate(zip(ours_log, theirs_log)):
        turn = k or None  # line k is the action taken at UI turn k; line 0 is "X goes first"
        _check(checks, turn, "oracle_log", strip(a) == strip(b), f"ours {a!r} vs {b!r}")
        if strip(a) == strip(b):
            _check(checks, turn, "oracle_log_wording", a == b, f"site {a!r} vs c1d970b {b!r}", kind="wording")
    return checks


def summarize(bundle):
    """The check errors, for the console; None if there are none. Wording differences are left out (they
    stay in the bundle's checks, under Inspect's Checks button)."""
    errors = [c for c in bundle["checks"] if not c["ok"] and c["kind"] == "error"]
    if not errors:
        return None
    lines = [f"game {bundle['game']['export']['id']}: {len(errors)} check errors"]
    lines += [f"  turn {c['turn']}: {c['check']} {c.get('detail', '')}" for c in errors]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("games", nargs="+", type=Path, help="GameRecord or raw export JSON files")
    ap.add_argument("--out", type=Path, default=ROOT / "review" / "build" / "bundles")
    ap.add_argument("--no-oracle", action="store_true", help="skip the comparison with hanab.live's reducer")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    built, failed = set(), set()
    for path in args.games:
        game_doc = json.loads(path.read_text())
        bundle = build_bundle(game_doc, None if args.no_oracle else run_oracle(path))
        game_id = bundle["game"]["export"]["id"]
        (args.out / f"{game_id}.json").write_text(json.dumps(bundle, separators=(",", ":")))
        built.add(game_id)
        text = summarize(bundle)
        if text is not None:
            print(text)
            failed.add(game_id)
    print(f"{len(built)} bundle{'s' if len(built) != 1 else ''} built, " + (f"{len(failed)} with check errors" if failed else "all checks passed"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
