"""The `/history/<player>` page -> listing rows (docs/representation.md §3.1, §6), and the train/test split
by seed (§3.5, Q12).

A listing row is what the history page shows for one game:

    {"game_id": 78922, "num_players": 2, "score": 0, "variant": "No Variant",
     "datetime": "2026-09-24T00:35:32Z", "players": ["d3m0n", "harikari.live"], "seed": "p2v0s713",
     "seed_games": 8}

`datetime` is when the game ended (UTC; the page has no start time). `players` are sorted by name, not in
seat order. `seed_games` is the page's "Other Scores" number: how many games on the server used this seed,
this one included (it is never below 1).

Pages are saved by hand (one fetch per player, see docs/progress.md) and parsed offline:

    python3 -m hanabi_data listing data/history/*.html --out data/history/listing.jsonl
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from .rules import VARIANTS

PLAYERS = range(2, 6)  # player counts in scope; the group has no 6-player games

# Split by seed: same-seed games share a deck (§3.5). A seed's bucket (0-99) comes from a salted hash, so
# it never changes as games are added. Changing SPLIT_SALT reshuffles every seed: don't.
SPLIT_SALT = "hanabi-split-v1"
SPLITS = (("test", 5), ("valid", 5), ("train", 90))  # (name, buckets), in bucket order

_ROW = re.compile(r'<tr\s+id="history-row-\d+".*?</tr>', re.S)
_CELL = re.compile(r"<td>(.*?)</td>", re.S)
_DATETIME = re.compile(r"(\d{4}-\d\d-\d\d) \u2014 (\d\d:\d\d:\d\d) UTC")
_SEED = re.compile(r'href="/seed/([^"]+)"')


def parse_history(page: str) -> List[dict]:
    """Every row of a saved history page, in page order (newest first). Raises ValueError on a row it
    can't read, rather than skipping it."""
    blocks, expected = _ROW.findall(page), page.count('id="history-row-')
    if len(blocks) != expected:
        raise ValueError(f"found {expected} history rows but could read only {len(blocks)}")
    return [_parse_row(b) for b in blocks]


def _parse_row(block: str) -> dict:
    cells = _CELL.findall(block)
    if len(cells) != 7:
        raise ValueError(f"history row with {len(cells)} cells, expected 7: {block[:200]!r}")
    text = [html.unescape(re.sub(r"<!--.*?-->|<[^>]+>", "", c, flags=re.S)).strip() for c in cells]
    when, seed = _DATETIME.fullmatch(text[4]), _SEED.search(cells[6])
    if not when or not seed:
        raise ValueError(f"history row with date {text[4]!r} and seed cell {cells[6]!r}")
    try:
        row = {
            "game_id": int(text[0]),
            "num_players": int(text[1]),
            "score": int(text[2]),
            "variant": text[3],
            "datetime": f"{when[1]}T{when[2]}Z",
            "players": sorted(p.strip() for p in text[5].split(",")),
            "seed": html.unescape(seed[1]),
            "seed_games": int(text[6]),
        }
    except ValueError as e:
        raise ValueError(f"history row {text[:3]}: {e}") from None
    if len(row["players"]) != row["num_players"]:
        raise ValueError(f"game {row['game_id']}: {row['num_players']} players but {row['players']} listed")
    return row


def merge(row_lists: Iterable[Iterable[dict]]) -> Dict[int, dict]:
    """Rows from several players' pages, one per game ID (sorted). The same game on two pages must
    agree, except `seed_games`, which grows as the seed is replayed: the larger count is kept."""
    out: Dict[int, dict] = {}
    for rows in row_lists:
        for row in rows:
            gid, old = row["game_id"], out.get(row["game_id"])
            if old is None:
                out[gid] = row
                continue
            if {k: v for k, v in old.items() if k != "seed_games"} != {k: v for k, v in row.items() if k != "seed_games"}:
                raise ValueError(f"game {gid} differs between history pages: {old} vs {row}")
            if row["seed_games"] > old["seed_games"]:
                out[gid] = row
    return dict(sorted(out.items()))


def load_listing(path: Union[str, Path]) -> Dict[int, dict]:
    """Rows by game ID, from a saved history page (`.html`) or a listing file (`.jsonl`, one row per line)."""
    path = Path(path)
    text = path.read_text()
    if path.suffix == ".html":
        return merge([parse_history(text)])
    return merge([[json.loads(line) for line in text.splitlines() if line.strip()]])


def write_listing(rows: Dict[int, dict], path: Union[str, Path]) -> None:
    Path(path).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()))


def in_scope(row: dict) -> bool:
    """A game the dataset wants: a variant the engine supports, 2-5 players."""
    return row["variant"] in VARIANTS and row["num_players"] in PLAYERS


def seed_bucket(seed: str) -> int:
    """0-99, fixed for a seed."""
    return int.from_bytes(hashlib.sha256(f"{SPLIT_SALT}:{seed}".encode()).digest()[:8], "big") % 100


def split_of(seed: str) -> str:
    """"train", "valid" or "test". Every game on the same seed lands in the same split."""
    bucket, edge = seed_bucket(seed), 0
    for name, size in SPLITS:
        edge += size
        if bucket < edge:
            return name
    raise AssertionError("SPLITS must cover 100 buckets")


def attach(record: dict, rows: Optional[Dict[int, dict]]) -> dict:
    """The record with its listing row filled in, if it has none yet and `rows` has the game."""
    if not rows or record.get("listing") is not None:
        return record
    row = rows.get(record["source"].get("game_id"))
    return {**record, "listing": row} if row else record


def summary(rows: Dict[int, dict]) -> List[str]:
    """A short report: totals, what's in scope, how seeds repeat, and the split."""
    scope = [r for r in rows.values() if in_scope(r)]
    lines = [f"{len(rows):,} games (IDs {min(rows)}-{max(rows)}, "
             f"{min(r['datetime'] for r in rows.values())[:10]} to {max(r['datetime'] for r in rows.values())[:10]})",
             f"in scope ({' / '.join(VARIANTS)}, {PLAYERS.start}-{PLAYERS.stop - 1} players): {len(scope):,}"]
    for variant in VARIANTS:
        by_n = Counter(r["num_players"] for r in scope if r["variant"] == variant)
        lines.append(f"  {variant}: " + " · ".join(f"{n}p {by_n[n]:,}" for n in PLAYERS))
    by_year = Counter(r["datetime"][:4] for r in scope)
    lines.append("  by year: " + " · ".join(f"{y} {c:,}" for y, c in sorted(by_year.items())))
    seeds = Counter(r["seed"] for r in scope)
    shared = sum(c for c in seeds.values() if c > 1)
    lines.append(f"seeds in scope: {len(seeds):,} distinct; {shared:,} games share a seed with another listed game; "
                 f"{sum(r['seed_games'] > 1 for r in scope):,} games are on a seed played more than once on the server")
    splits = Counter(split_of(r["seed"]) for r in scope)
    lines.append("split by seed: " + " · ".join(f"{name} {splits[name]:,}" for name, _ in SPLITS))
    return lines
