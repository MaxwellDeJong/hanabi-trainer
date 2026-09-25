"""Local server for the review tool: the bundle API plus the built web app.

    python3 review/server/serve.py [--port 8765]

Serves review/web/dist (build it with `npm run build` in review/) and:
    GET  /api/games                         one summary per bundle in review/build/bundles
    GET  /api/games/<id>/inspect            the full bundle (Inspect mode; local only)
    GET  /api/admin                         coverage and per-labeller contributions, with real names (admin only)

Label mode (docs/review-tool.md §4, §7). Keyed by session. Every call returns the session's label view
(labels.label_view), or 410 once the session has expired (unsubmitted 24 hours after it started):
    GET  /api/sessions?labeller=<name>      the labeller's sessions (lobby)
    GET  /api/board?labeller=<name>         every open game, with each seat's status (lobby)
    POST /api/sessions        {labeller, game_id?, seat?}
                                            start a session on that (game, seat), or a random open one
    GET  /api/sessions/<sid>                the label view
    POST /api/sessions/<sid>/advance        reveal the next move (refused on your turn until you label)
    POST /api/sessions/<sid>/labels         {turn, choice, also_ok, ms_to_choice, advance}
    POST /api/sessions/<sid>/retract        undo the latest label (adds "undone_turn" to the view)
    POST /api/sessions/<sid>/hint           {turn}: reveal the actual move at one of your turns
    POST /api/sessions/<sid>/submit         hand in a finished session; it is read-only from then on
"""
import argparse
import json
import re
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import labels

ROOT = Path(__file__).resolve().parents[2]
BUNDLES = ROOT / "review" / "build" / "bundles"
WEB = ROOT / "review" / "web" / "dist"


def summary(path):
    b = json.loads(path.read_text())
    ex = b["game"]["export"]
    final = b["positions"][-1]["board"]
    failed = [c for c in b["checks"] if not c["ok"]]
    return {
        "id": ex["id"],
        "players": ex["players"],
        "variant": ex.get("options", {}).get("variant", "No Variant"),
        "turns": b["turns"],
        "final_score": final["score"],
        "errors": sum(1 for c in failed if c["kind"] == "error"),
        "wording": sum(1 for c in failed if c["kind"] == "wording"),
    }


class Handler(SimpleHTTPRequestHandler):
    store: labels.Store

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path.startswith("/api/sessions") or path == "/api/board":
            return self.session_api("GET", path, parse_qs(query))
        if path == "/api/admin":
            return self.send_json(labels.admin_report(self.store))
        if path == "/api/games":
            games = [summary(p) for p in sorted(BUNDLES.glob("*.json"))]
            return self.send_json(sorted(games, key=lambda g: -g["id"]))
        m = re.fullmatch(r"/api/games/(\d+)/inspect", path)
        if m:
            f = BUNDLES / f"{int(m.group(1))}.json"
            if not f.exists():
                return self.send_error(HTTPStatus.NOT_FOUND, "no bundle for that game")
            return self.send_bytes(f.read_bytes(), "application/json")
        if path.startswith("/api/"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        if not (WEB / "index.html").exists():
            return self.send_bytes(b"review/web/dist is missing: run `npm run build` in review/", "text/plain",
                                   HTTPStatus.SERVICE_UNAVAILABLE)
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/sessions"):
            return self.send_error(HTTPStatus.NOT_FOUND)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"error": "bad JSON"}, HTTPStatus.BAD_REQUEST)
        return self.session_api("POST", path, body)

    def session_api(self, method, path, args):
        store = self.store
        try:
            if path == "/api/sessions":
                if method == "GET":
                    who = args.get("labeller", [None])[0]
                    rows = [labels.session_summary(store, s) for s in store.sessions(who)]
                    return self.send_json(sorted(rows, key=lambda r: r["started"], reverse=True))
                s = store.start(str(args.get("labeller", "")), args.get("game_id"), args.get("seat"))
                return self.send_json(labels.label_view(store, s))
            if path == "/api/board":
                return self.send_json(labels.board(store, args.get("labeller", [""])[0].strip()))
            m = re.fullmatch(r"/api/sessions/(\w+)(?:/(\w+))?", path)
            if m is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            sid, action = m.groups()
            extra = {}
            if method == "GET" and action is None:
                s = store.session(sid)
            elif method == "POST" and action == "advance":
                s = store.advance(sid)
            elif method == "POST" and action == "labels":
                s = store.label(sid, args["turn"], args.get("choice"), args.get("also_ok", []), args.get("ms_to_choice"),
                                bool(args.get("advance")))
            elif method == "POST" and action == "retract":
                s, extra["undone_turn"] = store.retract(sid)
            elif method == "POST" and action == "hint":
                s = store.hint(sid, args["turn"])
            elif method == "POST" and action == "submit":
                s = store.submit(sid)
            else:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_json({**labels.label_view(store, s), **extra})
        except labels.LabelError as e:
            return self.send_json({"error": str(e)}, e.status)
        except (KeyError, TypeError, ValueError) as e:
            return self.send_json({"error": f"bad request: {e!r}"}, HTTPStatus.BAD_REQUEST)

    def send_json(self, obj, status=HTTPStatus.OK):
        self.send_bytes(json.dumps(obj).encode(), "application/json", status)

    def send_bytes(self, data, content_type, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/assets/"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--labels", type=Path, default=labels.LABELS, help="where sessions and labels are stored")
    args = ap.parse_args()
    Handler.store = labels.Store(args.labels, BUNDLES)
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(WEB)))
    print(f"Review tool: http://{args.host}:{args.port}/  ({len(list(BUNDLES.glob('*.json')))} bundles; labels in {args.labels})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
