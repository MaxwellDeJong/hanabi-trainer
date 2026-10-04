"""Polite downloader for game exports (docs/representation.md §3.1).

- one request at a time, oldest game first, never faster than `rate` requests per second (start to
  start), plus up to 10% random jitter
- every export is cached on disk; an ID already there is never fetched again, so a stopped run resumes
  by running the same command again
- the run stops at the first problem (HTTP error, timeout, or a reply that isn't the requested export)
  instead of retrying
- more than SMALL_RUN missing exports (a bulk run) needs a terms file: what the server owner agreed to
  (§3.1). Without one the run refuses to start. A terms file can also set a rate ceiling and a time
  window (UTC) outside which no request is sent
- every request and every stop is appended to `download_log.jsonl` in the output folder

    python3 -m hanabi_data.download 78738 78742 --dry-run
    python3 -m hanabi_data.download 78738 78742
    python3 -m hanabi_data.download --listing data/history/listing.jsonl --dry-run
    python3 -m hanabi_data.download --listing data/history/listing.jsonl --terms data/download_terms.json --sample 100
    python3 -m hanabi_data.download --listing data/history/listing.jsonl --terms data/download_terms.json

A terms file (JSON; only `approved` is required):

    {"approved": "2026-10-05",                  # date the owner agreed (ISO)
     "note": "reply to the message of 2026-09-30",
     "rate": 0.5,                               # requests per second, a ceiling for every run
     "window": "08:00-13:00"}                   # UTC; may wrap past midnight ("22:00-03:00")
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .record import SERVER

BASE_URL = f"https://{SERVER}/export/"
# Sent with every request, word for word as promised to the server owner (message of 2026-09-30). A literal,
# so it doesn't follow ENGINE_VERSION; change it only after telling the owner.
USER_AGENT = "hanabi_data/0.1.0 (bulk export download, one request at a time; contact: harikari.live)"
DEFAULT_RATE = 0.5  # requests per second
MAX_RATE = 1.0  # never faster, whatever the terms say
JITTER = 0.1  # each gap is 1/rate times 1 to 1 + JITTER
SMALL_RUN = 20  # more missing exports than this need a terms file
SAMPLE_SEED = 20261002  # --sample picks the same games every time, so a stopped pilot resumes
LOG_NAME = "download_log.jsonl"
TERMS_KEYS = {"approved", "note", "rate", "window"}

Window = Tuple[dt.time, dt.time]


class DownloadError(Exception):
    pass


def export_path(out_dir: Path, game_id: int) -> Path:
    return out_dir / f"export_{game_id}.json"


def sample(game_ids: Iterable[int], n: int, seed: int = SAMPLE_SEED) -> List[int]:
    """`n` of the games at random (all of them if there are fewer), the same ones for the same IDs and seed."""
    ids = sorted(set(game_ids))
    return sorted(random.Random(seed).sample(ids, min(n, len(ids))))


def parse_window(text: str) -> Window:
    """'08:00-13:00' (UTC) -> (start, end). The end is exclusive; a window may wrap past midnight."""
    try:
        start, end = (dt.time.fromisoformat(part.strip()) for part in text.split("-"))
    except ValueError:
        raise DownloadError(f"window {text!r}: expected HH:MM-HH:MM (UTC)") from None
    if start == end:
        raise DownloadError(f"window {text!r} is empty")
    return start, end


def in_window(window: Optional[Window], now: dt.datetime) -> bool:
    if window is None:
        return True
    start, end, t = window[0], window[1], now.time()
    return start <= t < end if start < end else t >= start or t < end


def load_terms(path: Path, today: Optional[dt.date] = None) -> dict:
    """The terms file, checked: an approval date that isn't in the future, a rate within MAX_RATE, a
    valid window, no unknown keys (a typo would otherwise be ignored)."""
    try:
        terms = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise DownloadError(f"terms {path}: {e}") from None
    if not isinstance(terms, dict):
        raise DownloadError(f"terms {path}: expected a JSON object")
    unknown = set(terms) - TERMS_KEYS
    if unknown:
        raise DownloadError(f"terms {path}: unknown keys {sorted(unknown)}")
    try:
        approved = dt.date.fromisoformat(str(terms.get("approved")))
    except ValueError:
        raise DownloadError(f"terms {path}: 'approved' must be a date (YYYY-MM-DD)") from None
    if approved > (today or dt.datetime.now(dt.timezone.utc).date()):
        raise DownloadError(f"terms {path}: approved {approved} is in the future")
    if "rate" in terms and not (isinstance(terms["rate"], (int, float)) and 0 < terms["rate"] <= MAX_RATE):
        raise DownloadError(f"terms {path}: rate {terms['rate']!r} must be above 0 and at most {MAX_RATE}")
    if "window" in terms:
        parse_window(terms["window"])
    return terms


def check_export(game_id: int, body: bytes, row: Optional[dict] = None) -> None:
    """The reply is the export of `game_id`, not an error page or a different game. With the game's
    listing row, its players and seed must match too."""
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
    if row is not None:
        got = (sorted(doc["players"]), doc.get("seed"))
        if got != (row["players"], row["seed"]):
            raise DownloadError(f"{game_id}: players and seed {got} differ from the history page's "
                                f"{(row['players'], row['seed'])}")


def fetch_export(game_id: int, *, agent: str, base_url: str = BASE_URL, timeout: float = 30,
                 opener: Callable = urllib.request.urlopen) -> bytes:
    """One GET of <base_url><id>. Returns the body exactly as the server sent it (after gzip)."""
    request = urllib.request.Request(base_url + str(game_id), headers={
        "User-Agent": agent, "Accept": "application/json", "Accept-Encoding": "gzip"})
    try:
        with opener(request, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        retry = e.headers.get("Retry-After") if e.headers else None
        raise DownloadError(f"{game_id}: HTTP {e.code} {e.reason}" + (f" (Retry-After: {retry})" if retry else "")) from e
    except (urllib.error.URLError, OSError) as e:  # includes timeouts
        raise DownloadError(f"{game_id}: {e}") from e
    check_export(game_id, body)
    return body


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def download(game_ids: Iterable[int], out_dir: Path, *, rate: float = DEFAULT_RATE, terms: Optional[dict] = None,
             window: Optional[str] = None, max_requests: Optional[int] = None,
             listing: Optional[Dict[int, dict]] = None, dry_run: bool = False,
             base_url: str = BASE_URL, log: Callable[[str], None] = print, fetch: Callable = fetch_export,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             now: Callable[[], dt.datetime] = _utcnow) -> List[int]:
    """Fetches the exports not yet in `out_dir`, oldest (lowest ID) first. Returns the IDs fetched.

    Stops without error when `max_requests` have been sent or the window closes; the next run resumes.
    Raises DownloadError on the first problem (exports saved before it are kept), or before sending
    anything if the run isn't allowed: too fast, a bulk run without terms, outside the window.
    """
    terms = terms or {}
    if not 0 < rate <= MAX_RATE:
        raise DownloadError(f"rate {rate}/s is outside 0 to {MAX_RATE}/s")
    rate = min(rate, terms.get("rate", MAX_RATE))
    windows = [parse_window(w) for w in (terms.get("window"), window) if w]
    bulk = bool(terms)
    ids = sorted(set(game_ids))
    todo = [g for g in ids if not export_path(out_dir, g).exists()]
    planned = todo[:max_requests] if max_requests is not None else todo
    gap = 1 / rate
    open_now = all(in_window(w, now()) for w in windows)
    window_text = " and ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in windows) + " UTC" if windows else "any time"

    log(f"{len(ids):,} games, {len(ids) - len(todo):,} cached, {len(todo):,} to fetch"
        + (f" (this run: the first {len(planned):,})" if len(planned) < len(todo) else "")
        + (f"; IDs {planned[0]}-{planned[-1]}" if planned else ""))
    log(f"{rate:g} request/s, about {_hours(len(planned) * gap * (1 + JITTER / 2))}; window: {window_text}; "
        f"terms: {'approved ' + str(terms['approved']) if bulk else 'none'}")
    log(f"User-Agent: {USER_AGENT}")
    refusal = None
    if len(todo) > SMALL_RUN and not bulk:
        refusal = f"{len(todo):,} exports to fetch: more than {SMALL_RUN} needs a terms file (--terms)"
    elif planned and not open_now:
        refusal = f"outside the window ({window_text})"
    if dry_run:
        for g in planned[:SMALL_RUN]:
            log(f"{g}: would fetch {base_url}{g}")
        if len(planned) > SMALL_RUN:
            log(f"... and {len(planned) - SMALL_RUN:,} more")
        if refusal:
            log(f"a real run would refuse: {refusal}")
        return []
    if refusal:
        raise DownloadError(refusal)
    if not planned:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    run_log = out_dir / LOG_NAME

    def record(**entry):
        with run_log.open("a") as f:
            f.write(json.dumps({"at": now().isoformat(timespec="seconds"), **entry}) + "\n")

    record(event="start", to_fetch=len(planned), rate=rate, window=window_text, user_agent=USER_AGENT,
           terms=terms or None)
    fetched: List[int] = []
    started = last = None
    try:
        for i, g in enumerate(planned):
            if last is not None:
                wait = gap * (1 + random.uniform(0, JITTER)) - (clock() - last)
                if wait > 0:
                    sleep(wait)
            if not all(in_window(w, now()) for w in windows):
                log(f"stopped: the window ({window_text}) closed; {len(fetched):,} fetched this run, "
                    f"next is {g}. Run the same command again to resume")
                record(event="stop", reason="window closed", fetched=len(fetched), next=g)
                return fetched
            last = clock()
            if started is None:
                started = last
            try:
                body = fetch(g, agent=USER_AGENT, base_url=base_url)
                check_export(g, body, (listing or {}).get(g))
            except DownloadError as e:
                record(event="error", game_id=g, error=str(e), fetched=len(fetched))
                raise
            path = export_path(out_dir, g)
            part = path.with_name(path.name + ".part")
            part.write_bytes(body)
            os.replace(part, path)  # never leaves a half-written export behind
            fetched.append(g)
            record(event="saved", game_id=g, bytes=len(body), seconds=round(clock() - last, 3))
            left = len(planned) - i - 1
            pace = (clock() - started) / len(fetched)
            log(f"{g}: saved ({len(body):,} bytes); {len(fetched):,}/{len(planned):,}"
                + (f", about {_hours(left * max(pace, gap))} left" if left else ""))
    except KeyboardInterrupt:
        record(event="stop", reason="interrupted", fetched=len(fetched))
        log(f"interrupted: {len(fetched):,} fetched this run. Run the same command again to resume")
        raise
    record(event="done", fetched=len(fetched))
    return fetched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m hanabi_data.download", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", type=int, nargs="*", help="game IDs (or use --listing)")
    ap.add_argument("--listing", type=Path,
                    help="fetch every in-scope game of this listing (.jsonl or saved history page), and check "
                         "each export's players and seed against it")
    ap.add_argument("--terms", type=Path, help="what the server owner agreed to (JSON); needed for a bulk run")
    ap.add_argument("--out", type=Path, default=Path("data/exports"), help="cache folder (default: data/exports)")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE,
                    help=f"requests per second (default {DEFAULT_RATE}; never above the terms' rate or {MAX_RATE})")
    ap.add_argument("--window", help="only send requests in this UTC window, e.g. 08:00-13:00 (on top of the terms')")
    ap.add_argument("--max-requests", type=int, help="stop after this many requests (a pilot run)")
    ap.add_argument("--sample", type=int, metavar="N",
                    help="only N of the listing's games, picked at random across all years (a pilot run); the "
                         "same N every time, so the run resumes and the full run later skips them")
    ap.add_argument("--dry-run", action="store_true", help="say what would be fetched, send nothing")
    args = ap.parse_args(argv)
    if bool(args.ids) == bool(args.listing):
        ap.error("give game IDs or --listing, not both")
    if args.sample is not None and (not args.listing or args.sample < 1):
        ap.error("--sample needs --listing and a positive number")
    try:
        rows = None
        if args.listing:
            from .listing import in_scope, load_listing
            rows = {g: r for g, r in load_listing(args.listing).items() if in_scope(r)}
            if args.sample is not None:
                rows = {g: rows[g] for g in sample(rows, args.sample)}
        terms = load_terms(args.terms) if args.terms else None
        download(args.ids or rows, args.out, rate=args.rate, terms=terms, window=args.window,
                 max_requests=args.max_requests, listing=rows, dry_run=args.dry_run)
    except DownloadError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
