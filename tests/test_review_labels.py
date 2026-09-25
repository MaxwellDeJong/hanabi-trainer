"""Label store (review/server/labels.py): claims, picked seats, submitting, expiry, the board and the admin report."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "review" / "server"))
import labels  # noqa: E402
from bundle import build_bundle  # noqa: E402
from labels import LabelError, Store  # noqa: E402

GAME = 78822  # 3 players, 9 actions


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    out = tmp_path_factory.mktemp("bundles")
    for gid in (78822, 78921):
        b = build_bundle(json.loads((ROOT / "examples" / f"export_{gid}.json").read_text()))
        (out / f"{gid}.json").write_text(json.dumps(b))
    return out


@pytest.fixture
def store(tmp_path, bundles):
    return Store(tmp_path / "labels", bundles)


def seats(store):
    return sum(len(store.bundle(g)["game"]["export"]["players"]) for g in store.game_ids())


def play_to_end(store, sid):
    """Label every own turn with the real move and reveal every other turn."""
    s = store.session(sid)
    b = store.bundle(s["game_id"])
    while s["frontier"] <= b["turns"]:
        t = s["frontier"]
        if labels._actor(b, t) == s["seat"]:
            s = store.label(sid, t, labels.actual_move(b, t), advance=True)
        else:
            s = store.advance(sid)
    return s


def test_random_start_never_hands_out_a_claimed_seat(store):
    taken = set()
    for _ in range(seats(store)):
        s = store.start("ann")  # one labeller may take every seat of a game
        assert (s["game_id"], s["seat"]) not in taken
        taken.add((s["game_id"], s["seat"]))
    with pytest.raises(LabelError) as e:
        store.start("latecomer")
    assert e.value.status == 409


def test_picked_seat(store):
    s = store.start("ann", GAME, 2)
    assert (s["game_id"], s["seat"], s["chosen_by"]) == (GAME, 2, "picked")
    with pytest.raises(LabelError, match="already taken"):
        store.start("bob", GAME, 2)
    assert store.start("ann", GAME, 0)["seat"] == 0  # another seat of the same game
    with pytest.raises(LabelError, match="no such seat"):
        store.start("bob", GAME, 3)
    for bad in (12345, "78822", True):
        with pytest.raises(LabelError) as e:
            store.start("bob", bad, 0)
        assert e.value.status == 404


def test_picked_seat_refused_once_submitted(store):
    sid = store.start("ann", GAME, 0)["session_id"]
    play_to_end(store, sid)
    store.submit(sid)
    with pytest.raises(LabelError, match="already taken"):
        store.start("bob", GAME, 0)


def test_submit(store):
    sid = store.start("ann", GAME, 0)["session_id"]
    with pytest.raises(LabelError, match="to the end"):
        store.submit(sid)
    s = play_to_end(store, sid)
    assert s["ended"] and not s["submitted"]
    # Undo leaves a turn without a move: submitting is refused until it has one again.
    _, turn = store.retract(sid)
    with pytest.raises(LabelError, match=f"turn {turn}"):
        store.submit(sid)
    store.label(sid, turn, labels.actual_move(store.bundle(GAME), turn))
    s = store.submit(sid)
    assert s["submitted"] and labels.status(s) == "submitted"
    assert store.events(s)[-1]["kind"] == "submit"
    for call in (lambda: store.retract(sid), lambda: store.advance(sid), lambda: store.hint(sid, 1),
                 lambda: store.label(sid, 1, labels.actual_move(store.bundle(GAME), 1)), lambda: store.submit(sid)):
        with pytest.raises(LabelError, match="submitted"):
            call()
    view = labels.label_view(store, s)
    assert view["session"]["submitted"] == s["submitted"] and view["game_over"]


def test_board(store):
    ann = store.start("ann", GAME, 0)["session_id"]
    play_to_end(store, ann)
    store.submit(ann)
    store.start("bob", GAME, 1)
    board = {g["game_id"]: g for g in labels.board(store, "ann")}
    got = [(x["status"], x["labellers"], x["available"], x["session_id"] is not None) for x in board[GAME]["seats"]]
    assert got == [("submitted", ["ann"], False, True), ("active", ["bob"], False, False), ("open", [], True, False)]


def backdate(store, sid, hours):
    """Make the session look `hours` old."""
    path = store.root / "sessions" / f"{sid}.json"
    s = json.loads(path.read_text())
    s["started"] = labels._iso(datetime.now(timezone.utc) - timedelta(hours=hours))
    path.write_text(json.dumps(s))


def test_expiry(store):
    ann = store.start("ann", GAME, 0)["session_id"]
    store.label(ann, 1, labels.actual_move(store.bundle(GAME), 1), advance=True)
    bob = store.start("bob", GAME, 1)["session_id"]
    play_to_end(store, bob)
    bob_events = store.events(store.session(bob))
    backdate(store, ann, 23)
    assert store.session(ann)["frontier"] == 2  # still in force just before 24 hours
    backdate(store, ann, 25)
    with pytest.raises(LabelError, match="expired") as e:
        store.label(ann, 1, labels.actual_move(store.bundle(GAME), 1))
    assert e.value.status == 410
    # Its labels are gone from the game's events, other sessions' stay, and the seat is open again.
    assert bob_events and store.game_events(GAME) == bob_events
    assert [s["session_id"] for s in store.sessions()] == [bob]
    board = {g["game_id"]: g for g in labels.board(store, "cat")}
    assert [x["status"] for x in board[GAME]["seats"]] == ["open", "active", "open"]
    assert store.start("cat", GAME, 0)["seat"] == 0


def test_submitted_sessions_never_expire(store):
    sid = store.start("ann", GAME, 0)["session_id"]
    play_to_end(store, sid)
    store.submit(sid)
    backdate(store, sid, 100)
    assert store.session(sid)["submitted"]
    with pytest.raises(LabelError, match="already taken"):
        store.start("bob", GAME, 0)


def test_admin_report(store):
    ann = store.start("ann", GAME, 0)["session_id"]
    play_to_end(store, ann)
    store.submit(ann)
    store.start("bob", GAME, 1)
    r = labels.admin_report(store)
    t = r["totals"]
    assert (t["games"], t["seats"], t["submitted"], t["active"]) == (2, seats(store), 1, 1)
    assert t["open"] == seats(store) - 2 and t["labellers"] == 2
    assert t["sessions_submitted"] == 1 and t["sessions_active"] == 1
    own = len(labels.own_turns(store.bundle(GAME), 0))
    assert t["own_turns_submitted"] == own and t["labels"] == own
    people = {p["labeller"]: p for p in r["labellers"]}
    assert (people["ann"]["submitted"], people["ann"]["labels_submitted"]) == (1, own)
    assert (people["bob"]["active"], people["bob"]["labels"]) == (1, 0)
    game = next(g for g in r["games"] if g["id"] == GAME)
    assert [x["status"] for x in game["seats"]] == ["submitted", "active", "open"]
    # A lost All or Nothing game: the score is the cards played, not the site's 0.
    assert (game["suits"], game["score"], game["max_score"], game["bombs"]) == (6, 3, 30, 1)


def test_old_sessions_still_load(store):
    """Sessions written before `submitted` existed are active."""
    s = store.start("ann")
    raw = store.session(s["session_id"])
    del raw["submitted"]
    store._save(raw)
    assert labels.session_summary(store, store.session(s["session_id"]))["status"] == "active"
