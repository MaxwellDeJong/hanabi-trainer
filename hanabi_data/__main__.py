"""Command line.

    uv run python -m hanabi_data convert-export examples/export_78921.json > game.json
    uv run python -m hanabi_data convert-live capture.txt [--players a,b] [--options '{"variant": "6 Suits"}']
    uv run python -m hanabi_data decision examples/export_78921.json 4 --pretty   # UI turn 4
    uv run python -m hanabi_data decisions game.json > decisions.jsonl
    uv run python -m hanabi_data check game.json [...]
    uv run python -m hanabi_data check data/exports --listing data/history/listing.jsonl --summary   # pilot report
    uv run python -m hanabi_data filters data/exports/*.json [...]           # moves the filters catch
    uv run python -m hanabi_data trajectory examples/export_78921.json --seat 1 --turn 4   # printout
    uv run python -m hanabi_data trajectories data/exports/*.json --out data/trajectories/hanabi-trajectory-v0
    uv run python -m hanabi_data corpus data/exports --listing data/history/listing.jsonl --out data/corpus
    uv run python -m hanabi_data listing data/history/*.html --out data/history/listing.jsonl

Anything that takes a game accepts a GameRecord or a raw export, and `--listing FILE` (a listing .jsonl
or a saved history page) to fill in each game's /history row.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import check_record, game_facts, summary as check_summary
from .convert_export import from_export
from .convert_live import from_live, parse_capture
from .decision import decision_record, decisions, summarize
from .filters import BY_NAME, FILTERS, firings
from .jsonfmt import compact, pretty
from .listing import load_listing, merge, parse_history, summary, write_listing
from .record import game_files, load_game
from .rules import InvalidGame, Unsupported


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="uv run python -m hanabi_data", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    games = argparse.ArgumentParser(add_help=False)
    games.add_argument("--listing", type=Path, help="listing .jsonl or saved history page: fills in /history rows")
    p = sub.add_parser("convert-export", help="export JSON -> GameRecord", parents=[games])
    p.add_argument("export", type=Path)
    p = sub.add_parser("convert-live", help="captured websocket messages -> GameRecord")
    p.add_argument("capture", type=Path, nargs="+", help="capture files, in order")
    p.add_argument("--players", help="comma-separated, if the capture has no init message")
    p.add_argument("--options", default="{}", help="site-format options JSON, if no init message")
    p = sub.add_parser("decision", help="one decision record", parents=[games])
    p.add_argument("game", type=Path)
    p.add_argument("turn", type=int, help="UI turn (1-based)")
    p.add_argument("--seat", type=int, help="whose view (default: the player to act)")
    p.add_argument("--pretty", action="store_true")
    p = sub.add_parser("decisions", help="every decision record, one JSON per line", parents=[games])
    p.add_argument("game", type=Path)
    p = sub.add_parser("trajectory", help="print one seat's trajectory, frame by frame", parents=[games])
    p.add_argument("game", type=Path)
    p.add_argument("--seat", type=int, help="whose view (default: the record's view; required for a full record)")
    p.add_argument("--turn", type=int, help="only this UI turn's frame and step (1-based)")
    p = sub.add_parser("trajectories", help="every seat's trajectory of every game -> .npz shards", parents=[games])
    p.add_argument("games", type=Path, nargs="+", help="duplicate game IDs are read once")
    p.add_argument("--out", type=Path, required=True, help="a new directory (refused if it has shards)")
    p.add_argument("--per-shard", type=int, default=1000, help="trajectories per shard")
    p = sub.add_parser("corpus", help="check, split and convert every game -> trajectory shards per split",
                       parents=[games])
    p.add_argument("games", type=Path, nargs="+", help="game files or directories of them")
    p.add_argument("--out", type=Path, required=True, help="a new directory")
    p.add_argument("--jobs", type=int, help="worker processes (default: one per CPU)")
    p.add_argument("--per-shard", type=int, default=1000, help="trajectories per shard")
    p = sub.add_parser("check", help="validate GameRecords or exports", parents=[games])
    p.add_argument("games", type=Path, nargs="+", help="game files or directories of them")
    p.add_argument("--summary", action="store_true",
                   help="list only the games with problems, then a report: options, endings, problems by kind, "
                        "and the same by year (the pilot report)")
    p = sub.add_parser("filters", help="every move the label filters catch, and totals per filter", parents=[games])
    p.add_argument("games", type=Path, nargs="+", help="duplicate game IDs are read once")
    p = sub.add_parser("listing", help="saved /history pages -> listing rows, and a summary")
    p.add_argument("pages", type=Path, nargs="+", help="saved history pages (.html)")
    p.add_argument("--out", type=Path, help="write the merged rows here (.jsonl)")
    args = ap.parse_args(argv)

    try:
        rows = load_listing(args.listing) if getattr(args, "listing", None) else None
        load = lambda path: load_game(path, rows)
        if args.command == "convert-export":
            export = json.loads(args.export.read_text())
            print(compact(from_export(export, listing=(rows or {}).get(export.get("id")))))
        elif args.command == "listing":
            merged = merge(parse_history(path.read_text()) for path in args.pages)
            if args.out:
                write_listing(merged, args.out)
            print("\n".join(summary(merged) + ([f"-> {args.out}"] if args.out else [])))
        elif args.command == "convert-live":
            messages = [m for path in args.capture for m in parse_capture(path.read_text())]
            players = args.players.split(",") if args.players else None
            print(compact(from_live(messages, players=players, options=json.loads(args.options))))
        elif args.command == "decision":
            record = decision_record(load(args.game), args.turn, viewer=args.seat)
            print(pretty(record) if args.pretty else compact(record))
        elif args.command == "decisions":
            for record in decisions(load(args.game)):
                print(compact(record))
        elif args.command == "trajectory":
            from .trajectory import render, trajectory  # numpy is needed only here
            traj = trajectory(load(args.game), args.seat)
            turns = [args.turn - 1] if args.turn else range(traj.n_steps + 1)
            if any(not 0 <= t <= traj.n_steps for t in turns):
                raise ValueError(f"turn {args.turn} is outside 1..{traj.n_steps + 1}")
            print("\n\n".join(render(traj, t) for t in turns))
        elif args.command == "trajectories":
            return write_trajectories(args.games, args.out, args.per_shard, load)
        elif args.command == "corpus":
            return build_corpus(args.games, args.out, rows, args.jobs, args.per_shard)
        elif args.command == "check" and args.summary:
            facts = [game_facts(path, rows) for path in game_files(args.games)]
            for f in facts:
                if f["problems"]:
                    print(f"{f['path']}: {'; '.join(f['problems'])}")
            print("\n".join(([""] if any(f["problems"] for f in facts) else []) + check_summary(facts)))
            return 1 if any(f["problems"] for f in facts) else 0
        elif args.command == "check":
            failed = 0
            for path in game_files(args.games):
                try:
                    problems = check_record(load(path))
                except (InvalidGame, Unsupported) as e:  # a raw export that doesn't convert
                    problems = [f"{type(e).__name__}: {e}"]
                failed += bool(problems)
                print(f"{path}: {'ok' if not problems else '; '.join(problems)}")
            return 1 if failed else 0
        elif args.command == "filters":
            report_filters(args.games, load)
    except (InvalidGame, Unsupported, ValueError, FileExistsError) as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


def report_filters(paths, load=load_game) -> None:
    """One line per caught move (with the review tool's Inspect link), then totals per filter."""
    seen, moves, hits = set(), 0, []
    for path in paths:
        record = load(path)
        gid = record["source"]["game_id"]
        if gid in seen:
            continue
        seen.add(gid)
        summary = summarize(record)
        moves += summary["total_turns"]
        for hit in firings(record, summary):
            hits.append(hit)
            print(f"{hit['game_id']:>6} t{hit['turn']:<3} {','.join(hit['filters'])}: seat {hit['seat']} "
                  f"{hit['move']}s {hit['card']} (knows {hit['know']}), stacks {hit['stacks']}, "
                  f"clues {hit['clues']}, strikes {hit['strikes']}, {hit['turns_to_end']} turns to end "
                  f"(condition {hit['end_condition']}){', in the final misplay run' if hit['end_run'] else ''}"
                  f"  #/game/{hit['game_id']}/{hit['turn']}?pov={hit['seat']}")
    print(f"\n{len(seen)} games, {moves} moves")
    for f in FILTERS:
        mine = [h for h in hits if f.name in h["filters"]]
        print(f"{f.name} ({f.status}): {len(mine)} moves in {len({h['game_id'] for h in mine})} games, "
              f"{sum(h['end_run'] for h in mine)} in a final misplay run")


def build_corpus(paths, out: Path, rows, jobs, per_shard: int) -> int:
    """`corpus.build`, with its report printed. Fails if any game was left out for a reason other than
    being a duplicate."""
    import time
    from .corpus import DUPLICATE, OK, build, report  # numpy is needed here too

    start = time.monotonic()
    manifest = build(paths, out, rows, jobs, per_shard)
    print("\n".join(report(manifest, out)))
    print(f"built in {time.monotonic() - start:.1f} s")
    return 1 if any(r["status"] not in (OK, DUPLICATE) for r in manifest) else 0


def write_trajectories(paths, out: Path, per_shard: int, load=load_game) -> int:
    """Every seat of every full-information game (the view's seat of a player's-view record). A game
    that doesn't convert is reported and skipped."""
    from .trajectory import trajectory, write_shards

    seen, failed, frames = set(), 0, 0

    def trajs():
        nonlocal failed, frames
        for path in paths:
            try:
                record = load(path)
                gid = record["source"]["game_id"]
                if gid is not None and gid in seen:
                    continue
                seen.add(gid)
                seats = range(len(record["players"])) if record.get("view") is None else [record["view"]]
                built = [trajectory(record, s) for s in seats]
            except (InvalidGame, Unsupported, ValueError) as e:
                failed += 1
                print(f"{path}: skipped: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            for traj in built:
                frames += traj.n_steps + 1
                yield traj

    count = write_shards(trajs(), out, per_shard)
    print(f"{count} trajectories ({frames} frames) from {len(seen)} games -> {out}"
          + (f"; {failed} skipped" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
