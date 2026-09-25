"""Review server (review/server/serve.py): gzip for clients that accept it, plain bodies for those that don't."""
import gzip
import http.client
import json
import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "review" / "server"))
import serve  # noqa: E402
from bundle import build_bundle  # noqa: E402
from labels import Store  # noqa: E402

GAME = 78822


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    bundles = tmp_path_factory.mktemp("bundles")
    b = build_bundle(json.loads((ROOT / "examples" / f"export_{GAME}.json").read_text()))
    (bundles / f"{GAME}.json").write_text(json.dumps(b))
    web = tmp_path_factory.mktemp("dist")
    (web / "index.html").write_text("<!doctype html><title>Review</title>")
    (web / "assets").mkdir()
    (web / "assets" / "index-abc123.js").write_text("console.log('hanabi');\n" * 200)
    (web / "assets" / "x-abc123.png").write_bytes(b"\x89PNG" + bytes(2000))

    class Handler(serve.Handler):
        store = Store(tmp_path_factory.mktemp("labels"), bundles)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(web)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address
    httpd.shutdown()


def request(server, method, path, accept=None, body=None):
    conn = http.client.HTTPConnection(*server)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if accept is not None:
        headers["Accept-Encoding"] = accept
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    assert int(r.getheader("Content-Length")) == len(data)
    return r, data


def test_label_view_gzipped_only_when_accepted(server):
    start = {"labeller": "a", "game_id": GAME, "seat": 0}
    _, data = request(server, "POST", "/api/sessions", body=start)
    sid = json.loads(data)["session"]["session_id"]

    plain, raw = request(server, "GET", f"/api/sessions/{sid}")
    assert plain.getheader("Content-Encoding") is None
    assert len(raw) >= serve.GZIP_MIN
    view = json.loads(raw)

    zipped, data = request(server, "GET", f"/api/sessions/{sid}", accept="br, gzip, deflate")
    assert zipped.getheader("Content-Encoding") == "gzip"
    assert zipped.getheader("Vary") == "Accept-Encoding"
    assert zipped.getheader("Cache-Control") == "no-store"
    assert len(data) < len(raw)
    assert json.loads(gzip.decompress(data)) == view

    refused, data = request(server, "GET", f"/api/sessions/{sid}", accept="gzip;q=0, identity")
    assert refused.getheader("Content-Encoding") is None
    assert json.loads(data) == view


def test_small_bodies_stay_plain(server):
    r, data = request(server, "GET", "/api/sessions/nosuchsession", accept="gzip")
    assert r.status == 404
    assert r.getheader("Content-Encoding") is None
    assert "error" in json.loads(data)


def test_static_files(server):
    r, data = request(server, "GET", "/assets/index-abc123.js", accept="gzip")
    assert r.getheader("Content-Encoding") == "gzip"
    assert "immutable" in r.getheader("Cache-Control")
    assert gzip.decompress(data).startswith(b"console.log")

    r, data = request(server, "GET", "/", accept="gzip")
    assert r.getheader("Cache-Control") == "no-cache"
    assert b"<title>Review</title>" in data

    # Images are already compressed: served by the stock handler, as before.
    r, data = request(server, "GET", "/assets/x-abc123.png", accept="gzip")
    assert r.status == 200 and r.getheader("Content-Encoding") is None
    assert data.startswith(b"\x89PNG")
