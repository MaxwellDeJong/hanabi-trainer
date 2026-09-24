"""GameRecord constants and file helpers (docs/representation.md §6)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

SCHEMA = "hanabi-game/v0"
SERVER = "new.playhanabi.com"


def is_game_record(doc: dict) -> bool:
    return doc.get("schema") == SCHEMA


def player_view(record: dict, seat: int) -> dict:
    """The record as `seat` would have received it live: that seat's draws hidden. Everything else
    in a GameRecord is public. For testing the player's-view path on exported games."""
    if record.get("view") is not None:
        raise ValueError("already a player's view")
    events = [{**ev, "id": None} if ev["e"] == "draw" and ev["seat"] == seat else ev for ev in record["events"]]
    return {**record, "view": seat, "events": events}


def load_game(path: Union[str, Path]) -> dict:
    """A GameRecord file, or a raw export (converted on the fly)."""
    doc = json.loads(Path(path).read_text())
    if is_game_record(doc):
        return doc
    if "schema" in doc:
        raise ValueError(f"{path}: unknown schema {doc['schema']!r}")
    from .convert_export import from_export
    return from_export(doc)
