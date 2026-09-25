"""Sound effects in review bundles (review/server/bundle.py sound_effects), after hanab.live's getSoundType.ts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "review" / "server"))
from bundle import sound_effects  # noqa: E402


def sounds(actions, drops=(), end=None, score=0, max_score=25):
    """`actions`: ("clue", touched) / ("play", card) / ("misplay", card) / ("discard", card), one per turn;
    `drops`: turns after which the max score is lower."""
    events, maxes = [{"t": 0, "e": "deal"}], [max_score]
    for t, (kind, x) in enumerate(actions, 1):
        if kind == "clue":
            events.append({"t": t, "e": "clue", "touched": x})
        else:
            events.append({"t": t, "e": "play" if kind != "discard" else "discard", "card": x, "ok": kind == "play"})
        maxes.append(maxes[-1] - (t in drops))
    return sound_effects(events, [{"max_score": m} for m in maxes], end, score, max_score)


def test_standard_and_blind_plays():
    got = sounds([("clue", [3]), ("play", 3), ("play", 4), ("play", 5), ("clue", [6]), ("play", 7)])
    assert got == [None, None, None, "turn-blind1", "turn-blind2", None, "turn-blind1"]


def test_misplays_in_a_row():
    got = sounds([("misplay", 1), ("misplay", 2), ("play", 3), ("misplay", 4)])
    # A misplay also ends a run of blind plays; a play in between ends a run of misplays.
    assert got == [None, "turn-fail1", "turn-fail2", "turn-blind1", "turn-fail1"]


def test_sad_when_the_max_score_drops():
    got = sounds([("discard", 1), ("play", 2), ("misplay", 3)], drops={1, 2, 3})
    assert got == [None, "turn-sad", "turn-sad", "turn-fail1"]  # a misplay is fail even when it lowers the max


def test_finished():
    acts = [("clue", [1]), ("play", 1)]
    assert sounds(acts, end={"condition": 1}, score=25)[-1] == "finished-perfect"
    assert sounds(acts, end={"condition": 1}, score=24)[-1] == "finished-success"
    assert sounds(acts, end={"condition": 2}, score=24)[-1] == "finished-fail"
    assert sounds(acts)[-1] is None  # not over (a partial record)
