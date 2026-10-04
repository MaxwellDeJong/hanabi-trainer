"""GameRecord constants and file helpers (docs/representation.md §6)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

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


def load_game(path: Union[str, Path], listing: Optional[Dict[int, dict]] = None) -> dict:
    """A GameRecord file, or a raw export (converted on the fly). `listing` (rows by game ID, from
    `listing.load_listing`) fills in the record's `listing` if it has none."""
    from .listing import attach
    doc = json.loads(Path(path).read_text())
    if is_game_record(doc):
        return attach(doc, listing)
    if "schema" in doc:
        raise ValueError(f"{path}: unknown schema {doc['schema']!r}")
    from .convert_export import from_export
    return attach(from_export(doc), listing)


def game_files(paths: Iterable[Union[str, Path]]) -> List[Path]:
    """Files as given, directories as their `*.json`. Sorted by the game ID in the file name
    (`export_78921.json`), so a build or check goes oldest first; ties by path."""
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += p.glob("*.json")
        elif p.exists():
            files.append(p)
        else:
            raise ValueError(f"{p}: no such file or directory")

    def key(f: Path):
        m = re.search(r"\d+", f.stem)
        return (int(m.group()) if m else -1, str(f))
    return sorted(set(files), key=key)
