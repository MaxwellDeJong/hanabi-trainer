"""Checks for a stored GameRecord (docs/representation.md §10). Every check returns a list of
problems; an empty list means the record passed.

`game_facts` and `summary` are the pilot report (`check --summary`): what a sample of downloaded games
looks like, by year, before committing to the full download (§12 Q10).
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Union

from .convert_live import check_stream
from .decision import summarize
from .engine import positions
from .record import SCHEMA, is_game_record, load_game
from .rules import KNOWN_OPTIONS, End, InvalidGame, Unsupported

REQUIRED = ("schema", "source", "view", "players", "options", "events", "listing", "raw")


def check_record(record: dict) -> List[str]:
    missing = [k for k in REQUIRED if k not in record]
    if missing:
        return [f"missing fields {missing}"]
    if record["schema"] != SCHEMA:
        return [f"schema {record['schema']!r}, expected {SCHEMA!r}"]
    try:
        summary = summarize(record)  # replays every event through the engine
    except (InvalidGame, Unsupported) as e:
        return [f"replay: {e}"]
    problems = []
    kind = record["source"]["kind"]
    if kind == "export":
        if summary["end"] is None:
            problems.append("truncated: the actions stop before the game ends")
        from .convert_export import from_export
        if from_export(record["raw"])["events"] != record["events"]:
            problems.append("events differ from a fresh conversion of raw")
        problems += _check_listing(record, summary)
    elif kind == "live":
        problems += check_stream(record)
    else:
        problems.append(f"unknown source kind {kind!r}")
    return problems


def _check_listing(record: dict, summary: dict) -> List[str]:
    """The history page agrees with the export. Only the fields the listing has are compared."""
    listing, problems = record.get("listing") or {}, []
    expected = {
        "game_id": record["source"]["game_id"],
        "score": summary["final_score"],
        "num_players": len(record["players"]),
        "players": sorted(record["players"]),
        "variant": record["options"]["variant"],
        "seed": (record.get("raw") or {}).get("seed"),
    }
    for key, value in expected.items():
        if key in listing and value is not None and listing[key] != value:
            what = "final score" if key == "score" else key
            problems.append(f"{what} {value}, the history page says {listing[key]}")
    return problems


END_NAMES = {v: k.lower() for k, v in vars(End).items() if k.isupper() and isinstance(v, int)}
LISTING_FIELDS = re.compile(r"^(final score|game_id|num_players|players|variant|seed) .*, the history page says ")


def problem_kind(problem: str) -> str:
    """A problem with its game-specific details taken out, so the same failure counts once per kind."""
    m = LISTING_FIELDS.match(problem)
    if m:
        return f"history page differs: {m.group(1)}"
    return re.sub(r"\d+", "#", problem)[:120]


def game_facts(path: Union[str, Path], rows: Optional[Dict[int, dict]] = None) -> dict:
    """One game file for the pilot report: its check problems, and what the export says even if it
    doesn't convert. `deck_out` is the turn after which the deck was empty; `after_deck` how many turns
    were played from then on (more than one round only under All or Nothing)."""
    facts = {"path": str(path), "game_id": None, "year": None, "variant": None, "players": None, "options": {},
             "problems": [], "end": None, "won": None, "turns": None, "deck_out": None, "after_deck": None}
    try:
        doc = json.loads(Path(path).read_text())
        raw = (doc.get("raw") if is_game_record(doc) else doc) or {}
        facts.update(game_id=raw.get("id"), options=raw.get("options") or {},
                     players=len(raw["players"]) if "players" in raw else None)
        facts["variant"] = facts["options"].get("variant", facts["options"].get("variantName", "No Variant"))
        row = (rows or {}).get(facts["game_id"])
        facts["year"] = row["datetime"][:4] if row else None
        record = load_game(path, rows)
        facts["problems"] = check_record(record)
        if any(p.startswith("replay:") for p in facts["problems"]):
            return facts
        info = summarize(record)
        facts.update(turns=info["total_turns"], won=info["won"],
                     end=END_NAMES.get(info["end"]["condition"], info["end"]["condition"]) if info["end"] else None)
        for engine, _ in positions(record):
            if engine.deck_left == 0:
                facts["deck_out"] = engine.turn
                break
        if facts["deck_out"] is not None:
            facts["after_deck"] = info["total_turns"] - facts["deck_out"]
    except Exception as e:  # Unsupported, InvalidGame, bad JSON, or a bug: all go in the report
        facts["problems"].append(f"{type(e).__name__}: {e}")
    return facts


def summary(facts: List[dict]) -> List[str]:
    """The pilot report: what the games are, what failed and how, and the same by year (from the history
    page), so anything that changed over time shows up. A game ID seen in an earlier file is skipped."""
    seen, unique = set(), []
    for f in facts:
        if f["game_id"] is None or f["game_id"] not in seen:
            unique.append(f)
            seen.add(f["game_id"])
    duplicates, facts = len(facts) - len(unique), unique
    n = len(facts)
    bad = [f for f in facts if f["problems"]]
    count = lambda key: Counter(f[key] for f in facts if f[key] is not None)
    show = lambda c, fmt="{}": " · ".join(f"{fmt.format(k)} {v:,}" for k, v in sorted(c.items(), key=str))
    deck_out = [f for f in facts if f["deck_out"] is not None]
    beyond = lambda f: f["deck_out"] is not None and f["after_deck"] > f["players"]
    past = [f for f in deck_out if beyond(f)]
    lines = [f"{n:,} games, {n - len(bad):,} pass every check, {len(bad):,} with problems"
             + (f" ({duplicates:,} duplicate files skipped)" if duplicates else ""),
             f"variants: {show(count('variant'))}",
             f"players: {show(count('players'), '{}p')}",
             f"endings: {show(count('end'))} · won {sum(bool(f['won']) for f in facts):,}",
             f"deck ran out in {len(deck_out):,} games; in {len(past):,} play went on for more than a round after it"
             + (f" (most: {max(f['after_deck'] for f in past)} turns, game "
                f"{max(past, key=lambda f: f['after_deck'])['game_id']})" if past else "")]
    options = Counter(f"{k}={v}" for f in facts for k, v in f["options"].items() if k != "variant")
    lines.append("options: " + (show(options) or "none") + f" · none at all {sum(not f['options'] for f in facts):,}")
    unknown = Counter(k for f in facts for k in f["options"] if k not in KNOWN_OPTIONS)
    if unknown:
        lines.append("options the engine doesn't read (check none of them changes the rules): " + show(unknown))
    unlisted = [f for f in facts if f["year"] is None]
    if unlisted:
        lines.append(f"no history-page row: {len(unlisted):,} games (e.g. {', '.join(str(f['game_id']) for f in unlisted[:5])})")
    if bad:
        kinds = defaultdict(list)
        for f in bad:
            for k in dict.fromkeys(problem_kind(p) for p in f["problems"]):
                kinds[k].append(f["game_id"] if f["game_id"] is not None else f["path"])
        lines.append("problems by kind:")
        lines += [f"  {len(ids):,} × {k} (e.g. {', '.join(map(str, ids[:3]))})"
                  for k, ids in sorted(kinds.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    lines.append("by year (history page):")
    for year in sorted({f["year"] for f in facts}, key=lambda y: y or "~"):
        mine = [f for f in facts if f["year"] == year]
        sets = Counter("+".join(sorted(k for k in f["options"] if k != "variant")) or "no options" for f in mine)
        lines.append(f"  {year or 'unlisted'}: {len(mine):,} games · {sum(bool(f['problems']) for f in mine):,} with "
                     f"problems · won {sum(bool(f['won']) for f in mine):,} · deck out "
                     f"{sum(f['deck_out'] is not None for f in mine):,} ({sum(map(beyond, mine)):,} played on past a "
                     f"round) · {show(sets)}")
    return lines
