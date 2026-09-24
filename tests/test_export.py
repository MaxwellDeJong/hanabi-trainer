"""Export -> GameRecord: endings, metadata, rejected games, the checks of docs/representation.md §9."""
import copy

import pytest

from hanabi_data import End, InvalidGame, Unsupported, check_record, decisions, from_export, summarize
from helpers import (GAMES, color_clue, discard, greedy_actions, load_export, make_export, play, rank_clue,
                     sorted_deck)


@pytest.mark.parametrize("game_id, condition", [("78921", End.STRIKEOUT), ("78822", End.ALL_OR_NOTHING_FAIL),
                                                ("78922", End.STRIKEOUT)])
def test_example_endings(game_id, condition):
    record = from_export(load_export(game_id))
    assert record["events"][-1] == {"e": "end", "condition": condition, "seat": None}
    s = summarize(record)
    assert (s["final_score"], s["won"]) == (0, False)
    assert check_record(record) == []


def test_record_fields():
    export = load_export("78921")
    record = from_export(export, listing={"score": 0, "datetime": "2026-09-23T20:00:00Z"}, fetched_at="x")
    assert record["options"] == {"variant": "6 Suits", "suits": 6, "hand_size": 5, "all_or_nothing": True,
                                 "speedrun": False, "starting_player": 0}
    assert record["source"] == {"kind": "export", "server": "new.playhanabi.com", "game_id": 78921,
                                "table_id": None, "fetched_at": "x"}
    assert record["view"] is None and record["raw"] is export
    draws = [ev for ev in record["events"] if ev["e"] == "draw"]
    assert [d["card"] for d in draws] == list(range(len(draws)))
    assert all(ev["touched"] == sorted(ev["touched"]) for ev in record["events"] if ev["e"] == "clue")
    assert check_record(record) == []


def test_deliberate_strikeout_metadata():
    """78921 ends with misplays on turns 8, 10 and 11; only the last two form the run to the end (§10)."""
    ds = {d["key"]["turn"]: d["meta"] for d in decisions(from_export(load_export("78921")))}
    assert ds[8]["label_effect"] == ["misplay"] and not ds[8]["misplay_run_to_end"]
    assert ds[9]["label_effect"] == [] and not ds[9]["misplay_run_to_end"]
    assert ds[10]["label_effect"] == ["misplay"] and ds[10]["misplay_run_to_end"]
    assert ds[11]["label_effect"] == ["misplay", "ended_game"] and ds[11]["misplay_run_to_end"]
    assert {m["end_misplay_run"] for m in ds.values()} == {2}
    assert [ds[t]["turns_to_end"] for t in (1, 11)] == [10, 0]
    assert ds[1]["seed"] == "p2v1s4001" and ds[1]["raw_action"] == {"type": 3, "target": 1, "value": 1}
    assert ds[1]["players"] == ["pour1out4bga", "harikari.live"] and ds[2]["actor"] == "harikari.live"


def test_lost_critical():
    """78822's last move misplays the last B2, making Blue unfinishable: All or Nothing fail."""
    last = list(decisions(from_export(load_export("78822"))))[-1]["meta"]
    assert last["label_effect"] == ["misplay", "lost_critical", "ended_game"]
    assert last["end_condition"] == End.ALL_OR_NOTHING_FAIL


def test_truncated_export_is_reported():
    export = load_export("78921")
    export["actions"] = export["actions"][:-1]
    record = from_export(export)
    assert record["events"][-1]["e"] != "end"
    assert check_record(record) == ["truncated: the actions stop before the game ends"]
    meta = list(decisions(record))[0]["meta"]
    assert meta["end_condition"] is None and meta["final_score"] is None and meta["turns_to_end"] is None


@pytest.mark.parametrize("action, condition, seat", [({"type": 4, "target": 1, "value": End.TIMEOUT}, End.TIMEOUT, 1),
                                                     ({"type": 4, "target": 0, "value": End.TERMINATED_BY_PLAYER},
                                                      End.TERMINATED_BY_PLAYER, 0),
                                                     ({"type": 5, "target": -1, "value": 10}, End.TERMINATED_BY_VOTE, None)])
def test_end_actions(action, condition, seat):
    export = load_export("78921")
    export["actions"] = export["actions"][:5] + [action]
    record = from_export(export)
    assert record["events"][-1] == {"e": "end", "condition": condition, "seat": seat}
    assert summarize(record)["final_score"] == 0
    assert check_record(record) == []


def test_listing_score_is_checked():
    export = load_export("78921")
    assert check_record(from_export(export, listing={"score": 0})) == []
    assert check_record(from_export(export, listing={"score": 30})) == ["final score 0, the history page says 30"]


def test_tampered_events_are_reported():
    record = from_export(load_export("78921"))
    i = next(i for i, ev in enumerate(record["events"]) if ev["e"] == "clue")
    record["events"][i] = {**record["events"][i], "touched": [record["events"][i]["touched"][0]]}  # drop a touch
    problems = check_record(record)
    assert problems and problems[0].startswith("replay:")


def test_actions_after_the_end_are_rejected():
    export = load_export("78921")
    export["actions"].append(rank_clue(0, 1))
    with pytest.raises(InvalidGame, match="after the game ended"):
        from_export(export)


@pytest.mark.parametrize("actions, message", [
    ([discard(0)], "discard at 8 clues"),
    ([play(7)], "not in seat 0's hand"),
    ([rank_clue(0, 1)], "clue from seat 0 to seat 0"),
    ([color_clue(1, "T")], "colour clue value"),
    ([color_clue(1, "R")], "non-empty"),                   # seat 1 holds only Yellow
    ([rank_clue(1, 1)] * 9, "no clue tokens"),             # the 9th clue in a row
    ([{"type": 7, "target": 0, "value": 0}], "unknown type"),
])
def test_illegal_moves_are_rejected(actions, message):
    deck = sorted_deck(5)  # seat 0: R1-R5, seat 1: Y1-Y5
    # Seats alternate; seat 1's rank clues go to seat 0.
    actions = [a if i % 2 == 0 or a["type"] != 3 else {**a, "target": 0} for i, a in enumerate(actions)]
    with pytest.raises(InvalidGame, match=message):
        from_export(make_export(deck, actions))


def test_unsupported_games_are_rejected():
    deck = sorted_deck(5)
    for options in ({"variant": "Rainbow (6 Suits)"}, {"cardCycle": True}, {"detrimentalCharacters": True}):
        with pytest.raises(Unsupported):
            from_export(make_export(deck, [], options=options))
    with pytest.raises(InvalidGame, match="deck"):
        from_export(make_export(deck[:-1] + ["R1"], []))
    with pytest.raises(InvalidGame, match="deck"):
        from_export(make_export(deck, [], options={"variant": "6 Suits"}))


def test_options_that_change_the_setup():
    deck = sorted_deck(5)
    actions = greedy_actions(deck, 3, {"startingPlayer": 1, "oneExtraCard": True})
    record = from_export(make_export(deck, actions, 3, {"startingPlayer": 1, "oneExtraCard": True}))
    assert record["options"]["hand_size"] == 6 and record["options"]["starting_player"] == 1
    first = next(decisions(record))
    assert first["meta"]["actor"] == "p1" and len(first["obs"]["hands"][0]["slots"]) == 6
    assert summarize(record)["won"]
    record = from_export(make_export(deck, [], 4, {"oneLessCard": True}))
    assert record["options"]["hand_size"] == 3


def test_every_decision_label_is_legal():
    for game_id in GAMES:
        for d in decisions(from_export(load_export(game_id))):
            label = {k: v for k, v in d["label"].items() if k != "card"}
            assert label in d["obs"]["legal"]


def test_record_is_not_mutated_by_replay():
    record = from_export(load_export("78822"))
    before = copy.deepcopy(record)
    list(decisions(record))
    check_record(record)
    assert record == before
