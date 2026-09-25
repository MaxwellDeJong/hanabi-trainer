"""Label filters (hanabi_data/filters.py) on small synthetic 2-player games.

Every game deals seat 0 R5 Y5 G1 B1 P1 (cards 0-4, slot 5..1) and seat 1 R1 R2 R3 R4 R4 (cards 5-9).
"""
import dataclasses

from hanabi_data import decisions, from_export
from hanabi_data import filters
from hanabi_data.decision import summarize
from hanabi_data.engine import positions
from hanabi_data.filters import AGREED, fired, firings
from helpers import color_clue, discard, make_export, play, rank_clue, sorted_deck

HANDS = ["R5", "Y5", "G1", "B1", "P1", "R1", "R2", "R3", "R4", "R4"]
R5, Y5 = 0, 1
R1, R2, R3, R4a, R4b = 5, 6, 7, 8, 9


def deck():
    rest = sorted_deck(5)
    for card in HANDS:
        rest.remove(card)
    return HANDS + rest


def game(actions):
    return from_export(make_export(deck(), actions))


def caught(actions):
    """Filter names for the last action."""
    *_, (engine, action) = [p for p in positions(game(actions)) if p[1] is not None]
    return fired(engine, action)


# Turn 1: seat 0 gives seat 1 a clue; turn 2: seat 1 clues seat 0's two 5s.
FIVES_CLUED = [rank_clue(1, 1), rank_clue(0, 5)]
# Then seat 1 plays R1-R4 while seat 0 discards its 1s: the R stack is at 4 after turn 10.
RED_TO_4 = [discard(4), play(R1), discard(3), play(R2), discard(2), play(R3), rank_clue(1, 4), play(R4a)]


def test_play_clued_5_with_no_4_on_the_board():
    assert caught(FIVES_CLUED + [play(R5)]) == ["play_clued_5_no_4"]


def test_play_clued_5_that_could_be_the_suit_at_4():
    # Y5 is not playable, but as far as seat 0 knows it could be R5, and R is at 4.
    assert caught(FIVES_CLUED + RED_TO_4 + [play(Y5)]) == []


def test_play_clued_5_whose_suit_is_known_and_not_at_4():
    # A yellow clue rules out R: now the Y5 play is certainly a misplay.
    actions = FIVES_CLUED + RED_TO_4[:-1] + [color_clue(0, "Y"), play(R4a), play(Y5)]
    assert caught(actions) == ["play_clued_5_no_4"]


def test_unclued_misplay_is_not_caught():
    assert caught([play(R5)]) == []


def test_discard_clued_5():
    assert caught(FIVES_CLUED + [discard(R5)]) == ["discard_clued_5_live"]


def test_discard_clued_5_that_could_be_in_a_dead_suit():
    # Both R4s are discarded, so R can't reach 5, and seat 0's card could be R5.
    actions = FIVES_CLUED + [discard(4), discard(R4a), discard(3), discard(R4b), discard(Y5)]
    assert caught(actions) == []


def test_discard_clued_5_known_to_be_in_a_live_suit():
    actions = FIVES_CLUED + [discard(4), discard(R4a), discard(3), discard(R4b), color_clue(0, "Y"),
                             play(R1), discard(Y5)]
    assert caught(actions) == ["discard_clued_5_live"]


def test_meta_lists_agreed_filters_only(monkeypatch):
    record = game(FIVES_CLUED + [play(R5)])
    assert [d["meta"]["filters"] for d in decisions(record)] == [[], [], []]
    name = "play_clued_5_no_4"
    monkeypatch.setitem(filters.BY_NAME, name, dataclasses.replace(filters.BY_NAME[name], status=AGREED))
    assert [d["meta"]["filters"] for d in decisions(record)] == [[], [], [name]]


def test_firings_report():
    record = game(FIVES_CLUED + [play(R5)])
    [hit] = firings(record, summarize(record))
    assert hit["filters"] == ["play_clued_5_no_4"]
    assert (hit["turn"], hit["seat"], hit["card"], hit["know"]) == (3, 0, "R5", "RYGBP/5")
