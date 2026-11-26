"""The human-label file (B8) and the agreement report (hanabi_data/human_labels.py).

Labels are written as the review tool writes them (review-tool.md §8), for the example games only.
"""
import json

import numpy as np
import pytest

from hanabi_data.decision import views
from hanabi_data.human_labels import (MIN_GAMES, agreement_report, agrees, cluster_interval, cohen_kappa, collect,
                                      current_labels, label_rows, shrink, to_arrays, write_label_file)
from hanabi_data.record import load_game
from hanabi_data.trajectory import encode_move, trajectory
from helpers import EXAMPLES

GAME = 78921
PATH = EXAMPLES / f"export_{GAME}.json"
_n = iter(range(10 ** 6))


def ev(kind, turn, seat, session="s1", labeller="max", **fields):
    return {"kind": kind, "event_id": f"e{next(_n)}", "server": "new.playhanabi.com", "game_id": GAME,
            "turn": turn, "seat": seat, "labeller": labeller, "session_id": session, "at": "2026-11-25T00:00:00Z",
            "tool": "review/0.3", **fields}


def tool_choice(event, players):
    """A GameRecord event -> the review tool's `choice` form (absolute seats, card IDs)."""
    if event["e"] in ("play", "discard"):
        return {"type": event["e"], "card": event["card"]}
    return {"type": "clue", "to_seat": event["to"], "kind": event["kind"], "value": event["value"]}


@pytest.fixture(scope="module")
def record():
    return load_game(PATH)


def moves(record):
    """`(UI turn, event)` for every move (the event log also holds draws)."""
    return list(enumerate((e for e in record["events"] if e["e"] in ("play", "discard", "clue")), 1))


def actual_labels(record, seat, session="s1"):
    """A label at each of `seat`'s turns that copies the recorded move."""
    return [ev("label", turn, seat, session, choice=tool_choice(e, record["players"]))
            for turn, e in moves(record) if e["by"] == seat]


def test_copying_the_recorded_moves_agrees_everywhere(record):
    events = actual_labels(record, 0) + actual_labels(record, 1, "s2")
    rows = label_rows(record, events, {})
    assert rows and all(agrees(r) == {"exact": True, "touch": True, "type": True} for r in rows)


def test_rows_line_up_with_trajectory_steps(record):
    """A row's (viewer, step) is the trajectory step whose recorded move and legal masks it uses."""
    for seat in (0, 1):
        traj = trajectory(record, seat)
        for r in label_rows(record, actual_labels(record, seat), {}):
            t = r["step"]
            assert traj.steps["actor"][t] == 0  # the viewer moves at that step
            for k in ("type", "slot", "target", "value"):
                want = r["fields"][k]
                assert traj.steps[k][t] == (255 if want is None else want), (k, t)
            arrays = to_arrays([r])
            for head in ("play", "discard", "clue"):
                ok = arrays["ok_" + head][0]
                assert not (ok & ~traj.steps["legal_" + head][t]).any()  # only legal moves
            assert sum(int(arrays["ok_" + h].sum()) for h in ("play", "discard", "clue")) == 1


def test_also_ok_goes_into_the_masks_and_counts_as_agreement(record):
    seat, (turn, e) = 0, next((t, e) for t, e in moves(record) if e["by"] == 0)
    view = list(views(record, seat))[turn - 1]
    actual = encode_move(view["label"], view)["mask"]
    other = next(m for m in view["obs"]["legal"] if encode_move(m, view)["mask"] != actual)
    as_tool = ({"type": other["type"], "card": view["obs"]["hands"][0]["slots"][other["slot"] - 1]["card"]}
               if other["type"] != "clue" else
               {"type": "clue", "to_seat": (other["to"] + seat) % len(record["players"]), "kind": other["kind"],
                "value": other["value"]})
    row, = label_rows(record, [ev("label", turn, seat, choice=as_tool,
                                  also_ok=[tool_choice(e, record["players"])])], {})
    assert agrees(row)["exact"]
    arrays = to_arrays([row])
    assert sum(int(arrays["ok_" + h].sum()) for h in ("play", "discard", "clue")) == 2


@pytest.mark.parametrize("game", (78921, 78822, 78922))
def test_disagreement_and_touch_level(game):
    """A clue of the other kind that touches the same cards agrees at the touch level only."""
    record = load_game(EXAMPLES / f"export_{game}.json")
    vs = list(views(record, 0))
    for turn, e in moves(record):
        if e["by"] != 0 or e["e"] != "clue":
            continue
        view, n = vs[turn - 1], len(record["players"])
        to = e["to"] % n
        slots = view["obs"]["hands"][to]["slots"]
        touched = {sl["slot"] for sl in slots if sl["card"] in e["touched"]}
        for kind, values in (("color", "RYGBPT"[:view["obs"]["rules"]["suits"]]), ("rank", range(1, 6))):
            if kind == e["kind"]:
                continue
            for v in values:
                key = v if kind == "color" else str(v)
                same = {sl["slot"] for sl in slots if (sl["id"][0] == v if kind == "color" else sl["id"][1] == key)}
                if same == touched:
                    row, = label_rows(record, [ev("label", turn, 0, choice={
                        "type": "clue", "to_seat": e["to"], "kind": kind, "value": v})], {})
                    assert agrees(row) == {"exact": False, "touch": True, "type": False}
                    return
    pytest.skip("no equivalent clue of the other kind in this game")


def test_retract_and_replace():
    a = ev("label", 3, 0, choice={"type": "play", "card": 1})
    b = ev("label", 3, 0, choice={"type": "play", "card": 2}, replaces=a["event_id"])
    c = ev("label", 5, 0, choice={"type": "play", "card": 3})
    r = ev("retract", 5, 0, replaces=c["event_id"])
    other = ev("label", 3, 0, session="s2", choice={"type": "play", "card": 4})
    got = current_labels([a, b, c, r, other])
    assert got == {("s1", 3): b, ("s2", 3): other}


def test_labels_off_turn_or_illegal_are_refused(record):
    turn = next(t for t, e in moves(record) if e["by"] == 0)
    with pytest.raises(ValueError, match="isn't the actor"):
        label_rows(record, [ev("label", turn, 1, choice={"type": "play", "card": 0})], {})
    with pytest.raises(ValueError):
        label_rows(record, [ev("label", turn, 0, choice={"type": "play", "card": 999})], {})


def test_label_file_and_collect(tmp_path, record):
    labels = tmp_path / "labels"
    (labels / "sessions").mkdir(parents=True)
    events = actual_labels(record, 0) + actual_labels(record, 1, "s2")
    (labels / f"{GAME}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (labels / "sessions" / "s1.json").write_text(json.dumps(
        {"session_id": "s1", "game_id": GAME, "seat": 0, "chosen_by": "random", "submitted": "2026-11-25"}))
    rows, problems = collect(labels, [EXAMPLES])
    assert not problems and len(rows) == len(events)
    assert len(collect(labels, [EXAMPLES], submitted_only=True)[0]) == len(actual_labels(record, 0))
    assert {r["chosen_by"] for r in rows} == {"random", None}

    write_label_file(rows, tmp_path / "out")
    arrays = np.load(tmp_path / "out" / "labels.npz")
    lines = (tmp_path / "out" / "labels.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["rows"] == len(rows) == len(arrays["step"]) == len(lines) - 1
    assert [json.loads(l)["step"] for l in lines[1:]] == arrays["step"].tolist()
    assert collect(tmp_path / "nothing", [EXAMPLES]) == ([], [])

    (labels / "99999999.jsonl").write_text(json.dumps(ev("label", 1, 0, choice={"type": "play", "card": 0})) + "\n")
    assert collect(labels, [EXAMPLES])[1] == ["game 99999999: no export found"]


def test_report_runs(record):
    rows = label_rows(record, actual_labels(record, 0) + actual_labels(record, 1, "s2"), {})
    text = "\n".join(agreement_report(rows))
    assert "exact   100%" in text and "By player" in text and "kappa" in text
    assert agreement_report([]) == ["no labels"]


def test_kappa():
    assert cohen_kappa([("a", "a"), ("b", "b")]) == 1
    assert cohen_kappa([("a", "b"), ("b", "a")]) == -1
    assert cohen_kappa([("a", "a")] * 3) is None  # one category: undefined
    assert cohen_kappa([]) is None


def test_shrink():
    # Groups no further apart than chance: everyone gets the pooled rate.
    assert shrink({"a": (50, 100), "b": (52, 100)}) == {"a": 0.51, "b": 0.51}
    # Real spread: big groups keep their rate, a small one moves most of the way to the pool.
    got = shrink({"a": (90, 100), "b": (10, 100), "c": (3, 3)})
    assert abs(got["a"] - 0.9) < 0.02 and abs(got["b"] - 0.1) < 0.02
    assert 0.5 < got["c"] < 1
    assert shrink({}) == {}


def test_cluster_interval():
    assert cluster_interval({g: [True] for g in range(MIN_GAMES - 1)}) == (None, None)
    lo, hi = cluster_interval({g: [g % 2 == 0] * 10 for g in range(20)})
    assert lo < 0.5 < hi
