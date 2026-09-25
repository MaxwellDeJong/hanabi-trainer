"""The downloader, with the network replaced by fakes (no request is ever sent)."""
import gzip
import io
import json
import urllib.error

import pytest

from hanabi_data.download import DownloadError, download, export_path, fetch_export


def export_body(game_id):
    return json.dumps({"id": game_id, "players": ["a", "b"], "deck": [], "actions": []}).encode()


class FakeFetch:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, game_id, contact=None):
        self.calls.append(game_id)
        if game_id == self.fail_on:
            raise DownloadError(f"{game_id}: HTTP 503 Service Unavailable")
        return export_body(game_id)


def run(tmp_path, ids, fetch, **kw):
    sleeps = []
    fetched = download(ids, tmp_path, fetch=fetch, sleep=sleeps.append, log=lambda _: None, **kw)
    return fetched, sleeps


def test_fetches_missing_skips_cached_and_waits_between_requests(tmp_path):
    export_path(tmp_path, 2).write_bytes(export_body(2))
    fetch = FakeFetch()
    fetched, sleeps = run(tmp_path, [1, 2, 3, 1], fetch, delay=5.0)
    assert fetch.calls == fetched == [1, 3]
    assert len(sleeps) == 1 and 5.0 <= sleeps[0] <= 7.5
    assert json.loads(export_path(tmp_path, 3).read_bytes())["id"] == 3
    assert run(tmp_path, [1, 2, 3], FakeFetch())[0] == []  # a second run sends nothing


def test_stops_at_first_error_and_keeps_what_was_saved(tmp_path):
    fetch = FakeFetch(fail_on=2)
    with pytest.raises(DownloadError, match="503"):
        run(tmp_path, [1, 2, 3], fetch)
    assert fetch.calls == [1, 2]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export_1.json"]


def test_cap_dry_run_and_minimum_delay_send_nothing(tmp_path):
    fetch = FakeFetch()
    with pytest.raises(DownloadError, match="cap"):
        run(tmp_path, range(1, 22), fetch)
    with pytest.raises(DownloadError, match="minimum"):
        run(tmp_path, [1], fetch, delay=0.5)
    assert run(tmp_path, [1, 2], fetch, dry_run=True)[0] == []
    assert fetch.calls == [] and not any(tmp_path.iterdir())


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


def test_fetch_sends_one_identified_request_and_handles_gzip():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(gzip.compress(export_body(78738)), {"Content-Encoding": "gzip"})

    assert json.loads(fetch_export(78738, contact="me", opener=opener))["id"] == 78738
    assert seen[0].full_url == "https://new.playhanabi.com/export/78738"
    assert "contact: me" in seen[0].get_header("User-agent")


@pytest.mark.parametrize("body, match", [
    (b"<html>502 Bad Gateway</html>", "not JSON"),
    (export_body(1), "not this game's export"),
    (json.dumps({"id": 5, "players": []}).encode(), "no deck, actions"),
])
def test_fetch_rejects_replies_that_are_not_the_export(body, match):
    with pytest.raises(DownloadError, match=match):
        fetch_export(5, opener=lambda request, timeout: FakeResponse(body))


def test_fetch_turns_http_errors_into_download_errors():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    with pytest.raises(DownloadError, match="HTTP 429"):
        fetch_export(5, opener=opener)
