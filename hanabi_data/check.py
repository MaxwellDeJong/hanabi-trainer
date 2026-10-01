"""Checks for a stored GameRecord (docs/representation.md §10). Every check returns a list of
problems; an empty list means the record passed."""
from __future__ import annotations

from typing import List

from .convert_live import check_stream
from .decision import summarize
from .record import SCHEMA
from .rules import InvalidGame, Unsupported

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
