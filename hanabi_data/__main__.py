"""Command line.

    python3 -m hanabi_data convert-export examples/export_78921.json > game.json
    python3 -m hanabi_data convert-live capture.txt [--players a,b] [--options '{"variant": "6 Suits"}']
    python3 -m hanabi_data decision examples/export_78921.json 4 --pretty   # UI turn 4
    python3 -m hanabi_data decisions game.json > decisions.jsonl
    python3 -m hanabi_data check game.json [...]
    python3 -m hanabi_data filters data/exports/*.json [...]           # moves the filters catch

Anything that takes a game accepts a GameRecord or a raw export.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import check_record
from .convert_export import from_export
from .convert_live import from_live, parse_capture
from .decision import decision_record, decisions, summarize
from .filters import BY_NAME, FILTERS, firings
from .jsonfmt import compact, pretty
from .record import load_game
from .rules import InvalidGame, Unsupported


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m hanabi_data", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("convert-export", help="export JSON -> GameRecord")
    p.add_argument("export", type=Path)
    p.add_argument("--listing", type=Path, help="the game's /history row, as JSON")
    p = sub.add_parser("convert-live", help="captured websocket messages -> GameRecord")
    p.add_argument("capture", type=Path, nargs="+", help="capture files, in order")
    p.add_argument("--players", help="comma-separated, if the capture has no init message")
    p.add_argument("--options", default="{}", help="site-format options JSON, if no init message")
    p = sub.add_parser("decision", help="one decision record")
    p.add_argument("game", type=Path)
    p.add_argument("turn", type=int, help="UI turn (1-based)")
    p.add_argument("--seat", type=int, help="whose view (default: the player to act)")
    p.add_argument("--pretty", action="store_true")
    p = sub.add_parser("decisions", help="every decision record, one JSON per line")
    p.add_argument("game", type=Path)
    p = sub.add_parser("check", help="validate GameRecords or exports")
    p.add_argument("games", type=Path, nargs="+")
    p = sub.add_parser("filters", help="every move the label filters catch, and totals per filter")
    p.add_argument("games", type=Path, nargs="+", help="duplicate game IDs are read once")
    args = ap.parse_args(argv)

    try:
        if args.command == "convert-export":
            listing = json.loads(args.listing.read_text()) if args.listing else None
            print(compact(from_export(json.loads(args.export.read_text()), listing=listing)))
        elif args.command == "convert-live":
            messages = [m for path in args.capture for m in parse_capture(path.read_text())]
            players = args.players.split(",") if args.players else None
            print(compact(from_live(messages, players=players, options=json.loads(args.options))))
        elif args.command == "decision":
            record = decision_record(load_game(args.game), args.turn, viewer=args.seat)
            print(pretty(record) if args.pretty else compact(record))
        elif args.command == "decisions":
            for record in decisions(load_game(args.game)):
                print(compact(record))
        elif args.command == "check":
            failed = 0
            for path in args.games:
                try:
                    problems = check_record(load_game(path))
                except (InvalidGame, Unsupported) as e:  # a raw export that doesn't convert
                    problems = [f"{type(e).__name__}: {e}"]
                failed += bool(problems)
                print(f"{path}: {'ok' if not problems else '; '.join(problems)}")
            return 1 if failed else 0
        elif args.command == "filters":
            report_filters(args.games)
    except (InvalidGame, Unsupported, ValueError) as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


def report_filters(paths) -> None:
    """One line per caught move (with the review tool's Inspect link), then totals per filter."""
    seen, moves, hits = set(), 0, []
    for path in paths:
        record = load_game(path)
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


if __name__ == "__main__":
    sys.exit(main())
