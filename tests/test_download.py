"""The downloader. Nothing here reaches the real server: fetches are faked, or go to a local HTTP server, and
any other connection fails the test."""
import datetime as dt
import gzip
import http.server
import io
import json
import os
import signal
import socket
import threading
import urllib.error

import pytest

import hanabi_data.download as download_module
from hanabi_data.download import (DownloadError, LOG_NAME, Signalled, download, export_path, fetch_export, in_window,
                                  load_skip, load_terms, main, parse_window, sample, USER_AGENT)
from helpers import EXAMPLES, ROOT

START = dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.timezone.utc)
TERMS = {"approved": "2026-10-04"}


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    real = socket.create_connection

    def local_only(address, *args, **kw):
        if address[0] not in ("127.0.0.1", "localhost"):
            raise AssertionError(f"test tried to connect to {address}")
        return real(address, *args, **kw)

    monkeypatch.setattr(socket, "create_connection", local_only)


def export_body(game_id, players=("a", "b"), seed="p2v1s1"):
    return json.dumps({"id": game_id, "players": list(players), "seed": seed, "deck": [], "actions": []}).encode()


class FakeFetch:
    def __init__(self, fail_on=None, error=None):
        self.calls, self.agents, self.fail_on, self.error = [], [], fail_on, error

    def __call__(self, game_id, agent, base_url):
        self.calls.append(game_id)
        self.agents.append(agent)
        if game_id == self.fail_on:
            raise self.error or DownloadError(f"{game_id}: HTTP 503 Service Unavailable")
        return export_body(game_id)


class FakeTime:
    """A monotonic clock and a UTC wall clock that only move when the downloader sleeps (or `step` is
    added per fetch)."""
    def __init__(self, start=START, step=0.0):
        self.t, self.start, self.step, self.sleeps, self.starts = 0.0, start, step, [], []

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds

    def now(self):
        return self.start + dt.timedelta(seconds=self.t)


def run(tmp_path, ids, fetch, time=None, log=None, **kw):
    time = time or FakeTime()
    lines = []

    def timed_fetch(game_id, **f):
        time.starts.append(time.t)
        body = fetch(game_id, **f)
        time.t += time.step
        return body

    fetched = download(ids, tmp_path, fetch=timed_fetch, sleep=time.sleep, clock=time.clock, now=time.now,
                       log=log or lines.append, **kw)
    return fetched, time, lines


def log_entries(tmp_path):
    return [json.loads(line) for line in (tmp_path / LOG_NAME).read_text().splitlines()]


def test_fetches_oldest_first_skips_cached_and_keeps_the_rate(tmp_path):
    export_path(tmp_path, 2).write_bytes(export_body(2))
    fetch = FakeFetch()
    fetched, time, _ = run(tmp_path, [4, 3, 1, 2, 1], fetch, time=FakeTime(step=0.3))
    assert fetch.calls == fetched == [1, 3, 4]
    gaps = [b - a for a, b in zip(time.starts, time.starts[1:])]
    assert all(2.0 <= g <= 2.2 for g in gaps), gaps  # 0.5 request/s by default, start to start, jitter on top
    assert json.loads(export_path(tmp_path, 3).read_bytes())["id"] == 3
    assert [e["event"] for e in log_entries(tmp_path)] == ["start", "saved", "saved", "saved", "done"]
    assert run(tmp_path, [1, 2, 3, 4], FakeFetch())[0] == []  # a second run sends nothing


def test_slow_replies_add_no_extra_wait(tmp_path):
    _, time, _ = run(tmp_path, [1, 2, 3], FakeFetch(), time=FakeTime(step=5.0))
    assert time.sleeps == []


def test_stops_at_first_error_and_keeps_what_was_saved(tmp_path):
    fetch = FakeFetch(fail_on=2)
    with pytest.raises(DownloadError, match="503"):
        run(tmp_path, [1, 2, 3], fetch)
    assert fetch.calls == [1, 2]
    assert sorted(p.name for p in tmp_path.iterdir()) == [LOG_NAME, "export_1.json"]
    assert log_entries(tmp_path)[-1] == {"at": "2026-10-05T09:00:02+00:00", "event": "error", "game_id": 2,
                                         "error": "2: HTTP 503 Service Unavailable", "seconds": 0.0, "fetched": 1}


def test_interrupt_is_logged_and_the_next_run_resumes(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, [1, 2, 3], FakeFetch(fail_on=2, error=KeyboardInterrupt()))
    assert log_entries(tmp_path)[-1]["event"] == "stop" and log_entries(tmp_path)[-1]["reason"] == "interrupted"
    fetch = FakeFetch()
    assert run(tmp_path, [1, 2, 3], fetch)[0] == fetch.calls == [2, 3]


def test_bulk_run_needs_terms_and_nothing_is_sent_without_them(tmp_path):
    fetch = FakeFetch()
    with pytest.raises(DownloadError, match="more than 20 needs a terms file"):
        run(tmp_path, range(1, 22), fetch)
    _, _, lines = run(tmp_path, range(1, 22), fetch, dry_run=True)
    assert lines[-1] == "a real run would refuse: 21 exports to fetch: more than 20 needs a terms file (--terms)"
    assert "... and 1 more" in lines
    assert fetch.calls == [] and not any(tmp_path.iterdir())
    assert run(tmp_path, range(1, 22), fetch, terms=TERMS)[0] == list(range(1, 22))


def test_user_agent_is_the_one_promised():
    # word for word as in the message to the server owner; don't change one without the other
    assert USER_AGENT == "hanabi_data/0.1.0 (bulk export download, one request at a time; contact: harikari.live)"


def test_every_run_sends_the_promised_user_agent(tmp_path):
    fetch = FakeFetch()
    run(tmp_path / "small", [1, 2], fetch)  # no terms
    run(tmp_path / "bulk", [3], fetch, terms=TERMS)
    assert fetch.agents == [USER_AGENT] * 3
    assert log_entries(tmp_path / "bulk")[0]["terms"] == TERMS


def test_rate_limits(tmp_path):
    with pytest.raises(DownloadError, match="outside 0 to 1.0/s"):
        run(tmp_path, [1], FakeFetch(), rate=2)
    _, time, _ = run(tmp_path, [1, 2], FakeFetch(), rate=1.0, terms={**TERMS, "rate": 0.25})
    assert 4.0 <= time.sleeps[0] <= 4.4  # the terms' rate is a ceiling


def test_window(tmp_path):
    fetch = FakeFetch()
    with pytest.raises(DownloadError, match="outside the window"):
        run(tmp_path, [1], fetch, window="10:00-12:00")
    assert fetch.calls == []
    # Opens at 09:00:00 and closes at 09:00:05: three requests (0, ~2, ~4 s), then a clean stop.
    fetched, _, lines = run(tmp_path, [1, 2, 3, 4, 5], fetch, terms={**TERMS, "window": "08:00-09:00:05"})
    assert fetched == [1, 2, 3]
    assert lines[-1].startswith("stopped: the window (08:00-09:00 UTC) closed; 3 fetched this run, next is 4")
    assert log_entries(tmp_path)[-1]["reason"] == "window closed"
    later = FakeTime(start=START + dt.timedelta(days=1))
    assert run(tmp_path, [1, 2, 3, 4, 5], fetch, time=later, terms={**TERMS, "window": "08:00-09:00:05"})[0] == [4, 5]


def test_window_parsing_and_wrapping():
    at = lambda h, m=0: dt.datetime(2026, 10, 5, h, m, tzinfo=dt.timezone.utc)
    day, night = parse_window("08:00-13:00"), parse_window("22:00-03:00")
    assert [in_window(day, at(h)) for h in (7, 8, 12, 13)] == [False, True, True, False]
    assert [in_window(night, at(h)) for h in (21, 22, 0, 2, 3)] == [False, True, True, True, False]
    assert in_window(None, at(5))
    for bad in ("8-13", "08:00", "10:00-10:00"):
        with pytest.raises(DownloadError):
            parse_window(bad)


def test_pilot_run_stops_after_max_requests_and_resumes(tmp_path):
    fetch = FakeFetch()
    assert run(tmp_path, range(1, 6), fetch, max_requests=2)[0] == [1, 2]
    assert run(tmp_path, range(1, 6), fetch, max_requests=2)[0] == [3, 4]


def test_export_is_checked_against_its_listing_row(tmp_path):
    rows = {1: {"players": ["a", "b"], "seed": "p2v1s1"}, 2: {"players": ["a", "c"], "seed": "p2v1s1"}}
    with pytest.raises(DownloadError, match="differ from the history page"):
        run(tmp_path, [1, 2], FakeFetch(), listing=rows)
    assert sorted(p.name for p in tmp_path.glob("export_*")) == ["export_1.json"]


@pytest.mark.parametrize("terms, match", [
    ({"note": "x"}, "'approved' must be a date"),
    ({"approved": "2026-13-01"}, "'approved' must be a date"),
    ({"approved": "2026-10-06"}, "in the future"),
    ({"approved": "2026-10-04", "rate": 2}, "rate 2"),
    ({"approved": "2026-10-04", "window": "8 to 1"}, "HH:MM-HH:MM"),
    ({"approved": "2026-10-04", "rte": 0.5}, "unknown keys"),
])
def test_bad_terms_are_refused(tmp_path, terms, match):
    path = tmp_path / "terms.json"
    path.write_text(json.dumps(terms))
    with pytest.raises(DownloadError, match=match):
        load_terms(path, today=dt.date(2026, 10, 5))


def test_good_terms(tmp_path):
    terms = {"approved": "2026-10-05", "note": "ok", "rate": 0.5, "window": "08:00-13:00"}
    (tmp_path / "terms.json").write_text(json.dumps(terms))
    assert load_terms(tmp_path / "terms.json", today=dt.date(2026, 10, 5)) == terms


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


def test_fetch_sends_one_identified_request_and_handles_gzip():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(gzip.compress(export_body(78738)), {"Content-Encoding": "gzip"})

    assert json.loads(fetch_export(78738, agent="ua", opener=opener))["id"] == 78738
    assert seen[0].full_url == "https://new.playhanabi.com/export/78738"
    assert seen[0].get_header("User-agent") == "ua"


@pytest.mark.parametrize("body, match", [
    (b"<html>502 Bad Gateway</html>", "not JSON"),
    (export_body(1), "not this game's export"),
    (json.dumps({"id": 5, "players": []}).encode(), "no deck, actions"),
])
def test_fetch_rejects_replies_that_are_not_the_export(body, match):
    with pytest.raises(DownloadError, match=match):
        fetch_export(5, agent="ua", opener=lambda request, timeout: FakeResponse(body))


def test_fetch_turns_http_errors_into_download_errors():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "60"}, None)

    with pytest.raises(DownloadError, match=r"HTTP 429 Too Many Requests \(Retry-After: 60\)"):
        fetch_export(5, agent="ua", opener=opener)


@pytest.fixture
def local_server():
    """Serves examples/export_<id>.json at /export/<id> (gzipped when asked) on 127.0.0.1; 404 otherwise."""
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            if self.path == "/export/503":  # an overloaded server
                body = b"<html>upstream overloaded</html>"
                self.send_response(503)
                self.send_header("Retry-After", "120")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/export/200":  # a maintenance page sent as a success
                body = b"<html>down for maintenance</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            path = EXAMPLES / f"export_{self.path.rsplit('/', 1)[-1]}.json"
            if not self.path.startswith("/export/") or not path.exists():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            if "gzip" in self.headers.get("Accept-Encoding", ""):
                body = gzip.compress(body)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/export/", requests
    server.shutdown()


def test_end_to_end_against_a_local_server(tmp_path, local_server):
    from hanabi_data.listing import load_listing
    base_url, requests = local_server
    rows = load_listing(ROOT / "tests" / "data" / "history_sample.html")
    time = FakeTime()
    fetched = download([78922, 78822, 78921], tmp_path, base_url=base_url, listing=rows, terms=TERMS,
                       sleep=time.sleep, clock=time.clock, now=time.now, log=lambda _: None)
    assert fetched == [78822, 78921, 78922]
    assert [p for p, _ in requests] == ["/export/78822", "/export/78921", "/export/78922"]
    assert {h["User-Agent"] for _, h in requests} == {USER_AGENT}
    for g in fetched:  # saved unchanged
        assert export_path(tmp_path, g).read_bytes() == (EXAMPLES / f"export_{g}.json").read_bytes()
    with pytest.raises(DownloadError, match="HTTP 404"):
        download([1], tmp_path, base_url=base_url, sleep=time.sleep, clock=time.clock, now=time.now,
                 log=lambda _: None)


def test_cli_dry_runs(tmp_path, capsys):
    page = ROOT / "tests" / "data" / "history_sample.html"
    assert main(["--listing", str(page), "--out", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "3 games, 0 cached, 3 to fetch; IDs 78822-78922"  # the Brown game is out of scope
    assert out[1].startswith("0.5 request/s, about 0 min; window: any time; terms: none")
    assert out[3:] == [f"{g}: would fetch https://new.playhanabi.com/export/{g}" for g in (78822, 78921, 78922)]
    terms = tmp_path / "terms.json"
    terms.write_text(json.dumps({"approved": "2026-09-30", "window": "08:00-13:00"}))
    assert main(["--listing", str(page), "--terms", str(terms), "--out", str(tmp_path), "--dry-run"]) == 0
    assert "terms: approved 2026-09-30" in capsys.readouterr().out
    assert not any(p.name != "terms.json" for p in tmp_path.iterdir())
    with pytest.raises(SystemExit):
        main(["1", "--listing", str(page), "--dry-run"])
    with pytest.raises(SystemExit):
        main(["--dry-run"])


def test_sample_is_random_but_the_same_every_time():
    ids = range(1000, 3000)
    picked = sample(ids, 100)
    assert len(picked) == 100 and picked == sorted(picked) and set(picked) <= set(ids)
    assert sample(reversed(ids), 100) == picked  # depends on the IDs, not their order
    assert picked[0] < 1200 and picked[-1] > 2800  # spread over the whole range, not the oldest first
    assert sample(ids, 100, seed=1) != picked
    assert sample([3, 1, 2], 10) == [1, 2, 3]


def test_cli_sample(tmp_path, capsys):
    page = ROOT / "tests" / "data" / "history_sample.html"
    runs = []
    for _ in range(2):
        assert main(["--listing", str(page), "--sample", "2", "--out", str(tmp_path), "--dry-run"]) == 0
        runs.append(capsys.readouterr().out)
    assert runs[0].startswith("2 games, 0 cached, 2 to fetch") and runs[0] == runs[1]
    for bad in (["--sample", "2", "1"], ["--listing", str(page), "--sample", "0"]):
        with pytest.raises(SystemExit):
            main(bad + ["--dry-run"])


def run_local(tmp_path, base_url, ids, **kw):
    time = FakeTime()
    return download(ids, tmp_path, base_url=base_url, sleep=time.sleep, clock=time.clock, now=time.now,
                    log=lambda _: None, **kw)


def test_http_error_logs_status_headers_timing_and_keeps_the_reply(tmp_path, local_server):
    base_url, _ = local_server
    with pytest.raises(DownloadError, match=r"HTTP 503 .*Retry-After: 120"):
        run_local(tmp_path, base_url, [503])
    start, error = log_entries(tmp_path)
    assert start["host"] == socket.gethostname() and start["pid"] == os.getpid()
    assert error["event"] == "error" and error["game_id"] == 503 and error["status"] == 503
    assert error["headers"]["Retry-After"] == "120" and "seconds" in error
    assert error["reply_bytes"] == 32 and error["reply_kept"].startswith("failed/503_")
    assert (tmp_path / error["reply_kept"]).read_bytes() == b"<html>upstream overloaded</html>"


def test_a_reply_that_is_not_the_export_is_kept_whole(tmp_path, local_server):
    base_url, _ = local_server
    with pytest.raises(DownloadError, match="not JSON"):
        run_local(tmp_path, base_url, [200])
    error = log_entries(tmp_path)[-1]
    assert error["status"] == 200 and error["headers"]["Content-Type"] == "text/html"
    assert (tmp_path / error["reply_kept"]).read_bytes() == b"<html>down for maintenance</html>"
    assert not export_path(tmp_path, 200).exists()


def test_an_export_that_differs_from_its_listing_row_is_kept(tmp_path):
    listing = {1: {"players": ["x", "y"], "seed": "p2v1s1"}}
    with pytest.raises(DownloadError, match="differ from the history page"):
        run(tmp_path, [1], FakeFetch(), listing=listing)
    error = log_entries(tmp_path)[-1]
    assert (tmp_path / error["reply_kept"]).read_bytes() == export_body(1)


def test_network_errors_name_their_kind():
    def opener(request, timeout):
        raise urllib.error.URLError(TimeoutError("timed out"))

    with pytest.raises(DownloadError) as caught:
        fetch_export(5, agent="ua", opener=opener)
    assert caught.value.details == {"exception": "TimeoutError"}


def test_an_unexpected_exception_is_logged_with_its_traceback(tmp_path):
    with pytest.raises(RuntimeError):
        run(tmp_path, [1, 2], FakeFetch(fail_on=2, error=RuntimeError("a bug")))
    crash = log_entries(tmp_path)[-1]
    assert crash["event"] == "crash" and crash["game_id"] == 2 and crash["fetched"] == 1
    assert crash["error"] == "RuntimeError: a bug" and "Traceback" in crash["traceback"]


def test_a_full_disk_while_saving_is_logged(tmp_path, monkeypatch):
    def full(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(download_module.Path, "write_bytes", full)
    with pytest.raises(OSError):
        run(tmp_path, [1], FakeFetch())
    crash = log_entries(tmp_path)[-1]
    assert crash["event"] == "crash" and "No space left on device" in crash["error"]


def test_sighup_and_sigterm_stop_the_run_cleanly(tmp_path, monkeypatch):
    real = download_module.download

    def hung_up(ids, out_dir, **kw):
        def fetch(game_id, agent, base_url):
            if game_id == 2:
                os.kill(os.getpid(), signal.SIGHUP)  # the tmux pane or SSH session closes
            return export_body(game_id)
        time = FakeTime()
        return real(ids, out_dir, **kw, fetch=fetch, sleep=time.sleep, clock=time.clock, now=time.now)

    monkeypatch.setattr(download_module, "download", hung_up)
    before = signal.getsignal(signal.SIGHUP)
    assert main(["1", "2", "3", "--out", str(tmp_path)]) == 130
    assert signal.getsignal(signal.SIGHUP) == before  # main puts the old handlers back
    stop = log_entries(tmp_path)[-1]
    assert stop["event"] == "stop" and stop["reason"] == "signal SIGHUP" and stop["game_id"] == 2
    assert sorted(p.name for p in tmp_path.glob("export_*")) == ["export_1.json"]


def test_sigterm_is_logged_as_a_stop(tmp_path):
    with pytest.raises(Signalled):
        run(tmp_path, [1], FakeFetch(fail_on=1, error=Signalled("signal SIGTERM")))
    assert log_entries(tmp_path)[-1]["reason"] == "signal SIGTERM"


def test_a_game_that_always_fails_can_be_skipped_with_a_reason(tmp_path):
    for _ in range(2):  # without a skip, every rerun stops at the same game
        lines = []
        with pytest.raises(DownloadError):
            run(tmp_path, [1, 2, 3], FakeFetch(fail_on=2), log=lines.append)
    assert lines[-1].startswith("2: stopped after 0 this run. If 2 fails again on a rerun") and "--skip" in lines[-1]
    fetch = FakeFetch(fail_on=2)
    fetched, _, lines = run(tmp_path, [1, 2, 3], fetch, skip={2: "HTTP 404: deleted", 9: "not in this run"})
    assert fetched == fetch.calls == [3]
    assert lines[0] == "3 games, 1 cached, 1 skipped, 1 to fetch; IDs 3-3"
    assert "2: skipped (HTTP 404: deleted)" in lines
    start = [e for e in log_entries(tmp_path) if e["event"] == "start"][-1]
    assert start["skipped"] == {"2": "HTTP 404: deleted"}  # only the skips that applied to this run


def test_skip_does_not_count_towards_the_bulk_limit(tmp_path):
    fetch = FakeFetch()
    assert run(tmp_path, range(1, 22), fetch, skip={5: "bad"})[0] == [g for g in range(1, 22) if g != 5]


@pytest.mark.parametrize("doc, match", [
    ([1, 2], "expected a JSON object"),
    ({"abc": "x"}, "not a game ID"),
    ({"12": ""}, "needs a reason"),
    ({"12": None}, "needs a reason"),
])
def test_bad_skip_files_are_refused(tmp_path, doc, match):
    path = tmp_path / "skip.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(DownloadError, match=match):
        load_skip(path)


def test_cli_skip(tmp_path, capsys):
    page = ROOT / "tests" / "data" / "history_sample.html"
    skip = tmp_path / "skip.json"
    skip.write_text(json.dumps({"78921": "HTTP 404 in testing"}))
    assert main(["--listing", str(page), "--skip", str(skip), "--out", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("3 games, 0 cached, 1 skipped, 2 to fetch") and "78921: skipped (HTTP 404 in testing)" in out
    assert "78921: would fetch" not in out
