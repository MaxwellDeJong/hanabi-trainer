"""Live websocket captures (table 43267 = game 78922, seen from seat 0) -> GameRecord."""
import copy

import pytest

from hanabi_data import check_record, decisions, from_export, from_live, parse_capture
from hanabi_data.convert_live import ConversionError, check_stream
from helpers import EXAMPLES, load_export, norm

CAPTURES = ("deal", "turn3", "turn10", "final")


def capture(name):
    return parse_capture((EXAMPLES / f"live_43267_player0_{name}.txt").read_text())


def live(name, **kw):
    export = load_export("78922")
    return from_live(capture(name), players=export["players"], options=export.get("options", {}), **kw)


@pytest.fixture(scope="module")
def export_record():
    return from_export(load_export("78922"))


@pytest.mark.parametrize("name, view, events", [("deal", 0, 10), ("turn3", 0, 12), ("turn10", 0, 23),
                                                ("final", None, 26)])
def test_view_and_checks(name, view, events):
    record = live(name)
    assert record["view"] == view and len(record["events"]) == events
    assert record["source"]["kind"] == "live" and record["source"]["table_id"] == 43267
    assert check_record(record) == []


def test_unhidden_final_capture_is_the_export(export_record):
    assert live("final")["events"] == export_record["events"]


@pytest.mark.parametrize("name, turns", [("deal", [1]), ("turn3", [1, 3]), ("turn10", [1, 3, 5, 7, 9])])
def test_players_view_matches_export(export_record, name, turns):
    """§6/§9: seat 0's decision records from the stream equal those from the export, apart from
    `private` and `meta`. The last one is the live decision (seat 0 to act, no label yet) where the
    capture allows it."""
    full = {d["key"]["turn"]: d for d in decisions(export_record)}
    ours = list(decisions(live(name)))
    assert [d["key"]["turn"] for d in ours] == turns
    for d in ours:
        assert norm(d["obs"]) == norm(full[d["key"]["turn"]]["obs"])
        assert d["private"] is None and d["key"]["table_id"] == 43267 and d["key"]["game_id"] is None
    current = {"deal": 1, "turn3": 3}.get(name)
    for d in ours:
        if d["key"]["turn"] == current:
            assert d["label"] is None and d["obs"]["legal"] and d["meta"]["end_condition"] is None
        else:
            assert d["label"] == full[d["key"]["turn"]]["label"]


def test_status_mismatch_is_reported():
    record = live("turn10")
    raw = copy.deepcopy(record["raw"])
    status = next(a for a in raw["messages"][0][1]["list"] if a["type"] == "status")
    status["clues"] = 3
    problems = check_stream({**record, "raw": raw})
    assert problems == ["status after 1 actions: server (clues, score, max) (3, 0, 25), engine (7, 0, 25)"]


def test_init_message():
    init = {"tableID": 43267, "playerNames": ["harikari.live", "d3m0n"], "ourPlayerIndex": 0,
            "databaseID": -1, "seed": "p2v0s713", "options": {"numPlayers": 2, "variantName": "No Variant",
                                                              "allOrNothing": False, "startingPlayer": 0}}
    record = from_live([("init", init)] + capture("turn3"))
    assert record["players"] == init["playerNames"] and record["view"] == 0
    assert "seed" not in record["raw"]["messages"][0][1]
    assert "p2v0s713" not in str(record)
    with pytest.raises(ConversionError, match="ourPlayerIndex"):
        from_live([("init", {**init, "ourPlayerIndex": 1})] + capture("turn3"))


def test_later_messages_extend_the_list():
    """A reload sends the whole list again; single `gameAction`s add to it."""
    full = capture("turn10")[0][1]["list"]
    head, tail = full[:20], full[20:]
    messages = [("gameActionList", {"tableID": 43267, "list": head})]
    messages += [("gameAction", {"tableID": 43267, "action": a}) for a in tail]
    assert from_live(messages, players=["a", "b"])["events"] == live("turn10")["events"]
    reload = [("gameActionList", {"tableID": 43267, "list": head})] + capture("turn10")
    assert from_live(reload, players=["a", "b"])["events"] == live("turn10")["events"]


def test_bad_streams_are_rejected():
    lst = capture("turn10")[0][1]["list"]
    strike = next(i for i, a in enumerate(lst) if a["type"] == "strike")
    with pytest.raises(ConversionError, match="strike"):
        from_live([("gameActionList", {"list": lst[:strike + 1]})], players=["a", "b"])
    hidden_both = [dict(a, suitIndex=-1, rank=-1) if a["type"] == "draw" else a for a in lst]
    with pytest.raises(ConversionError, match="hidden for seats"):
        from_live([("gameActionList", {"list": hidden_both})], players=["a", "b"])
    with pytest.raises(ConversionError, match="unknown event"):
        from_live([("gameActionList", {"list": lst + [{"type": "cardIdentity"}]})], players=["a", "b"])
    with pytest.raises(ConversionError, match="init"):
        from_live(capture("turn10"))
