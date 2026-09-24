"""Endings no example game reaches, on synthetic games (tests/data/synthetic_*.json).

The synthetic games were found by a random full-information search; each one's per-turn state was
also checked against hanab.live's reducer with `review/server/bundle.py` (0 errors). The reducer
doesn't decide endings, so the assertions here check them against game.go CheckEnd by hand.
"""
import json

import pytest

from hanabi_data import End, check_record, decisions, from_export, positions, summarize
from hanabi_data.record import player_view
from helpers import ROOT, greedy_actions, make_export, sorted_deck

DATA = ROOT / "tests" / "data"


def synthetic(name):
    return from_export(json.loads((DATA / f"synthetic_{name}.json").read_text()))


def last_draw_action(record):
    """0-based index of the action that drew the deck's last card."""
    t, deck_size = 0, 10 * record["options"]["suits"]
    for ev in record["events"]:
        if ev["e"] in ("play", "discard", "clue"):
            t += 1
        if ev["e"] == "draw" and ev["card"] == deck_size - 1:
            return t - 1


@pytest.mark.parametrize("players", [2, 3, 4, 5])
@pytest.mark.parametrize("options", [{"allOrNothing": True}, {"variant": "6 Suits", "allOrNothing": True}, {}])
def test_win(players, options):
    deck = sorted_deck(6 if options.get("variant") else 5)
    record = from_export(make_export(deck, greedy_actions(deck, players, options), players, options))
    s = summarize(record)
    assert s["end"] == {"condition": End.NORMAL, "seat": None}
    assert s["won"] and s["final_score"] == 5 * record["options"]["suits"]
    assert check_record(record) == []


def test_all_or_nothing_has_no_final_round():
    record = synthetic("aon_deck")
    n, last = len(record["players"]), last_draw_action(record)
    s = summarize(record)
    assert s["total_turns"] > last + n + 1  # play went on past the point a final round would end
    assert s["won"]
    sizes = [[len(h) for h in engine.hands] for engine, _ in positions(record)]
    assert sizes[-1] == [0, 0, 0] and min(map(sum, sizes)) == 0  # hands shrink to nothing


def test_all_or_nothing_softlock():
    record = synthetic("softlock")
    *_, (engine, _) = positions(record)
    assert record["events"][-1] == {"e": "end", "condition": End.ALL_OR_NOTHING_SOFTLOCK, "seat": 0}
    assert engine.hands[0] == [] and engine.clues == 0
    assert summarize(record)["final_score"] == 0


@pytest.mark.parametrize("name", ["final_round_full_2p", "final_round_full_4p"])
def test_final_round_without_all_or_nothing(name):
    """After the last draw every player, including the one who drew it, gets one more turn."""
    record = synthetic(name)
    n = len(record["players"])
    s = summarize(record)
    assert s["total_turns"] == last_draw_action(record) + n + 1
    assert s["end"]["condition"] == End.NORMAL
    *_, (engine, _) = positions(record)
    assert s["final_score"] == engine.score < engine.rules.max_score and not s["won"]  # score kept


def test_final_round_ends_early_when_nothing_can_be_played():
    """game.go: once the players who have had their last turn hold every needed card, the game ends."""
    record = synthetic("final_round")
    n = len(record["players"])
    s = summarize(record)
    assert s["total_turns"] == last_draw_action(record) + n  # one turn short of the full round
    assert s["end"]["condition"] == End.NORMAL and s["final_score"] == 11


GAMES = ["aon_deck", "softlock", "final_round", "final_round_full_2p", "final_round_full_4p"]


@pytest.mark.parametrize("name", GAMES)
def test_player_views_replay_and_match(name):
    """Every seat's view (own draws hidden, as in a live stream) replays, accepts the same ending,
    and gives that seat exactly the observations the full record gives it."""
    record = synthetic(name)
    full = {d["key"]["turn"]: d for d in decisions(record)}
    for seat in range(len(record["players"])):
        view = player_view(record, seat)
        assert summarize(view)["end"] == summarize(record)["end"]
        ours = list(decisions(view))
        assert ours and all(d["meta"]["actor"] == f"p{seat}" for d in ours)
        for d in ours:
            assert d["obs"] == full[d["key"]["turn"]]["obs"]
            assert d["label"] == full[d["key"]["turn"]]["label"]
            # Unknown own cards: no `private`. (An empty hand hides nothing.)
            assert d["private"] == (None if d["obs"]["hands"][0]["slots"] else {"own_hand": []})
