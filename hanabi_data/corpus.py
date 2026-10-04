"""The training corpus: game files -> checked, split trajectory shards (docs/representation.md §8.5, §10).

    python3 -m hanabi_data corpus data/exports --listing data/history/listing.jsonl --out data/corpus

Every game is checked (`check_record`, which compares it with its listing row too), searched by every
label filter (any status, for exploring), and split by seed (`listing.split_of`). A game that passes
goes in as one trajectory per seat. Any other game is left out and the manifest says why.

    <out>/games.jsonl          one row per input file, in input order: status, split, problems, catches
    <out>/report.txt           totals (also printed)
    <out>/{train,valid,test}/  hanabi-trajectory/v0 shard directories (`trajectory.read_shards` reads each)

The output is the same for the same inputs. It's built in `<out>.partial` and renamed when complete,
so `<out>` is never half-written. There's no resume: a build runs in parallel (`--jobs`) and takes
minutes, so after more games arrive, build again into a new directory.

Needs numpy, like `trajectory.py`.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union

from .check import END_NAMES, check_record
from .decision import summarize
from .filters import FILTERS, firings
from .listing import SPLITS, split_of
from .record import game_files, load_game
from .rules import Unsupported
from .trajectory import SCHEMA, ShardWriter, Trajectory, trajectory

OK = "ok"
# Why a file is left out, in report order.
DUPLICATE = "duplicate"      # a game ID already read from an earlier file
UNSUPPORTED = "unsupported"  # a variant or option the engine lacks, a player's-view record, no seed
INVALID = "invalid"          # doesn't convert or replay
FAILED_CHECK = "failed check"
ERROR = "error"              # an unexpected exception: a bug to fix, not a bad game
REASONS = (DUPLICATE, UNSUPPORTED, INVALID, FAILED_CHECK, ERROR)


def build_game(path: Path, rows: Optional[Dict[int, dict]] = None) -> Tuple[dict, List[Trajectory]]:
    """One file's manifest row, and its trajectories if it passes (else none)."""
    row = {"path": str(path), "game_id": None, "status": OK, "split": None, "problems": []}

    def leave_out(status, *problems):
        row.update(status=status, problems=list(problems))
        return row, []

    try:
        doc = json.loads(Path(path).read_text())
        if isinstance(doc, dict):  # the ID, even if the game won't convert
            row["game_id"] = doc.get("id") if "schema" not in doc else (doc.get("source") or {}).get("game_id")
        record = load_game(path, rows)
        row["game_id"] = record["source"]["game_id"]
        if record.get("view") is not None:
            return leave_out(UNSUPPORTED, "a player's-view record; the corpus takes full-information games")
        seed = (record.get("raw") or {}).get("seed")
        if not seed:
            return leave_out(UNSUPPORTED, "no seed, so no split")
        row["split"] = split_of(seed)
        problems = check_record(record)
        if problems:
            return leave_out(INVALID if any(p.startswith("replay:") for p in problems) else FAILED_CHECK, *problems)
        summary = summarize(record)
        trajs = [trajectory(record, seat) for seat in range(len(record["players"]))]
    except Unsupported as e:
        return leave_out(UNSUPPORTED, str(e))
    except ValueError as e:  # InvalidGame, a bad file, unreadable JSON
        return leave_out(INVALID, f"{type(e).__name__}: {e}")
    except Exception as e:  # keep going, so one build shows every such game
        return leave_out(ERROR, f"{type(e).__name__}: {e}")
    row.update({
        "players": len(record["players"]),
        "variant": record["options"]["variant"],
        "listed": record.get("listing") is not None,
        "turns": summary["total_turns"],
        "end_condition": summary["end"]["condition"],
        "final_score": summary["final_score"],
        "won": summary["won"],
        "trajectories": len(trajs),
        "frames": sum(t.n_steps + 1 for t in trajs),
        "catches": [{k: h[k] for k in ("turn", "seat", "filters", "end_run")} for h in firings(record, summary)],
    })
    return row, trajs


_ROWS: Optional[Dict[int, dict]] = None  # each worker's listing, set once by _init


def _init(rows):
    global _ROWS
    _ROWS = rows


def _work(path):
    return build_game(path, _ROWS)


def _results(files: List[Path], rows, jobs: int) -> Iterator[Tuple[dict, List[Trajectory]]]:
    """build_game over every file, in input order."""
    if jobs <= 1 or len(files) <= 1:
        return (build_game(f, rows) for f in files)
    def gen():
        with ProcessPoolExecutor(jobs, initializer=_init, initargs=(rows,)) as pool:
            yield from pool.map(_work, files, chunksize=4)
    return gen()


def build(paths: Iterable[Union[str, Path]], out: Union[str, Path], rows: Optional[Dict[int, dict]] = None,
          jobs: Optional[int] = None, per_shard: int = 1000) -> List[dict]:
    """Build the corpus into `out` (refused if it exists). Returns the manifest rows."""
    out = Path(out)
    tmp = out.with_name(out.name + ".partial")
    if out.exists():
        raise FileExistsError(f"{out} already exists: build into a new directory")
    if tmp.exists():
        raise FileExistsError(f"{tmp} is left over from an interrupted build: delete it first")
    files = game_files(paths)
    tmp.mkdir(parents=True)
    writers = {name: ShardWriter(tmp / name, per_shard) for name, _ in SPLITS}
    manifest, seen = [], set()
    with (tmp / "games.jsonl").open("w") as f:
        for row, trajs in _results(files, rows, jobs or os.cpu_count() or 1):
            gid = row["game_id"]
            if gid is not None and gid in seen:
                row = {"path": row["path"], "game_id": gid, "status": DUPLICATE, "split": None,
                       "problems": [f"game {gid} already read from an earlier file"]}
                trajs = []
            elif gid is not None:
                seen.add(gid)
            for t in trajs:
                writers[row["split"]].add(t)
            manifest.append(row)
            f.write(json.dumps(row) + "\n")
    for w in writers.values():
        w.close()
    (tmp / "report.txt").write_text("\n".join(report(manifest, out)) + "\n")
    tmp.rename(out)
    return manifest


def report(manifest: List[dict], out: Union[str, Path] = "") -> List[str]:
    """Totals for a manifest: what went in, what didn't and why, per split, and the filters' catches."""
    ok = [r for r in manifest if r["status"] == OK]
    left = Counter(r["status"] for r in manifest if r["status"] != OK)
    lines = [f"{SCHEMA} corpus {out}".rstrip(),
             f"{len(manifest):,} files: {len(ok):,} games in the corpus, {sum(left.values()):,} left out"
             + (" (" + " · ".join(f"{k} {left[k]:,}" for k in REASONS if left[k]) + ")" if left else "")]
    for name, _ in SPLITS:
        mine = [r for r in ok if r["split"] == name]
        lines.append(f"  {name}: {len(mine):,} games · {sum(r['trajectories'] for r in mine):,} trajectories · "
                     f"{sum(r['frames'] for r in mine):,} frames")
    if not ok:
        return lines + _left_out(manifest)
    count = lambda key: Counter(r[key] for r in ok)
    players, variants, ends = count("players"), count("variant"), count("end_condition")
    lines += [
        "players: " + " · ".join(f"{n}p {players[n]:,}" for n in sorted(players)),
        "variants: " + " · ".join(f"{v} {c:,}" for v, c in sorted(variants.items())),
        "endings: " + " · ".join(f"{END_NAMES.get(c, c)} {n:,}" for c, n in sorted(ends.items()))
        + f" · won {sum(bool(r['won']) for r in ok):,}",
        f"turns: {sum(r['turns'] for r in ok):,} moves, longest game {max(r['turns'] for r in ok)}",
    ]
    unlisted = sum(not r["listed"] for r in ok)
    if unlisted:
        lines.append(f"games without a listing row (so no datetime): {unlisted:,}")
    lines.append("filter catches (any status):")
    for flt in FILTERS:
        hits = [(r["game_id"], c) for r in ok for c in r["catches"] if flt.name in c["filters"]]
        lines.append(f"  {flt.name} ({flt.status}): {len(hits):,} moves in {len({g for g, _ in hits}):,} games, "
                     f"{sum(c['end_run'] for _, c in hits):,} in a final misplay run")
    return lines + _left_out(manifest)


def _left_out(manifest: List[dict], limit: int = 20) -> List[str]:
    rows = [r for r in manifest if r["status"] not in (OK, DUPLICATE)]
    if not rows:
        return []
    lines = [f"left out (first {min(limit, len(rows))} of {len(rows):,}; all in games.jsonl):"]
    lines += [f"  {r['path']}: {r['status']}: {'; '.join(r['problems'])}" for r in rows[:limit]]
    return lines
