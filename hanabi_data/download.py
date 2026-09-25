"""Polite downloader for game exports (docs/representation.md §3.1).

Only for small, hand-picked samples until the server owner agrees to more:
- one request at a time, at least `delay` seconds apart plus random jitter
- every export is cached on disk; an ID already there is never fetched again
- a hard cap on the number of requests per run
- the run stops at the first problem (HTTP error, timeout, or a reply that isn't the requested export)
  instead of retrying

    python3 -m hanabi_data.download 78738 78742 --dry-run
    python3 -m hanabi_data.download 78738 78742
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .record import SERVER

BASE_URL = f"https://{SERVER}/export/"
MIN_DELAY = 1.0
CONTACT = "harikari.live"  # player name on the server


class DownloadError(Exception):
    pass


def export_path(out_dir: Path, game_id: int) -> Path:
    return out_dir / f"export_{game_id}.json"


def user_agent(contact: Optional[str] = None) -> str:
    from . import ENGINE_VERSION
    ua = f"hanabi_data/{ENGINE_VERSION} (small hand-picked sample, one request at a time"
    return ua + (f"; contact: {contact})" if contact else ")")


def check_export(game_id: int, body: bytes) -> None:
    """The reply is the export of `game_id`, not an error page or a different game."""
    try:
        doc = json.loads(body)
    except ValueError:
        raise DownloadError(f"{game_id}: the reply is not JSON: {body[:80]!r}") from None
    if not isinstance(doc, dict) or doc.get("id") != game_id:
        got = doc.get("id") if isinstance(doc, dict) else type(doc).__name__
        raise DownloadError(f"{game_id}: the reply is not this game's export (id {got!r})")
    missing = [k for k in ("players", "deck", "actions") if k not in doc]
    if missing:
        raise DownloadError(f"{game_id}: the export has no {', '.join(missing)}")


def fetch_export(game_id: int, *, contact: Optional[str] = None, timeout: float = 30,
                 opener: Callable = urllib.request.urlopen) -> bytes:
    """One GET of /export/<id>. Returns the body exactly as the server sent it (after gzip)."""
    request = urllib.request.Request(BASE_URL + str(game_id), headers={
        "User-Agent": user_agent(contact), "Accept": "application/json", "Accept-Encoding": "gzip"})
    try:
        with opener(request, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        raise DownloadError(f"{game_id}: HTTP {e.code} {e.reason}") from e
    except (urllib.error.URLError, OSError) as e:  # includes timeouts
        raise DownloadError(f"{game_id}: {e}") from e
    check_export(game_id, body)
    return body


def download(game_ids: Iterable[int], out_dir: Path, *, delay: float = 5.0, max_requests: int = 20,
             contact: Optional[str] = None, dry_run: bool = False, log: Callable[[str], None] = print,
             fetch: Callable = fetch_export, sleep: Callable[[float], None] = time.sleep) -> List[int]:
    """Fetches the exports not yet in `out_dir`. Returns the IDs fetched. Raises DownloadError on the
    first problem; exports saved before it are kept."""
    if delay < MIN_DELAY:
        raise DownloadError(f"delay {delay}s is below the minimum of {MIN_DELAY}s")
    ids = list(dict.fromkeys(game_ids))
    todo = [g for g in ids if not export_path(out_dir, g).exists()]
    for g in ids:
        if g not in todo:
            log(f"{g}: cached ({export_path(out_dir, g)})")
    if len(todo) > max_requests:
        raise DownloadError(f"{len(todo)} exports to fetch, more than the cap of {max_requests} per run")
    if dry_run:
        for g in todo:
            log(f"{g}: would fetch {BASE_URL}{g}")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    fetched = []
    for i, g in enumerate(todo):
        if i:
            sleep(delay + random.uniform(0, delay / 2))
        body = fetch(g, contact=contact)
        path = export_path(out_dir, g)
        part = path.with_name(path.name + ".part")
        part.write_bytes(body)
        os.replace(part, path)  # never leaves a half-written export behind
        log(f"{g}: saved {path} ({len(body):,} bytes)")
        fetched.append(g)
    return fetched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m hanabi_data.download", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", type=int, nargs="+", help="game IDs")
    ap.add_argument("--out", type=Path, default=Path("data/exports"), help="cache folder (default: data/exports)")
    ap.add_argument("--delay", type=float, default=5.0, help="seconds between requests, plus up to 50%% jitter")
    ap.add_argument("--max-requests", type=int, default=20, help="refuse to run if more exports than this are missing")
    ap.add_argument("--contact", default=CONTACT, help=f"sent in the User-Agent (default: {CONTACT})")
    ap.add_argument("--dry-run", action="store_true", help="list what would be fetched, send nothing")
    args = ap.parse_args(argv)
    try:
        download(args.ids, args.out, delay=args.delay, max_requests=args.max_requests,
                 contact=args.contact, dry_run=args.dry_run)
    except DownloadError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
