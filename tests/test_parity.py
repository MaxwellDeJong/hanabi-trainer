"""The engine reproduces the prototype (prototype/replay.py), which was checked against screenshots
and hanab.live's reducer, at every position of every example game, from every seat."""
import sys

import pytest

from hanabi_data import decision_record, from_export
from helpers import EXAMPLES, GAMES, load_export, norm

sys.path.insert(0, str(EXAMPLES.parent))
import replay as prototype  # noqa: E402

COMPARED = ("schema", "key", "obs", "label", "private")


@pytest.mark.parametrize("game_id", GAMES)
def test_every_position_and_seat_matches_prototype(game_id):
    export = load_export(game_id)
    record = from_export(export)
    n, T = len(export["players"]), len(export["actions"])
    for t in range(T + 1):
        for viewer in [None] + list(range(n)):
            ours = norm(decision_record(record, t + 1, viewer=viewer))
            theirs = norm(prototype.decision_record(export, t, viewer=viewer))
            if not export.get("options", {}).get("allOrNothing"):
                theirs["obs"]["rules"]["all_or_nothing"] = False  # the prototype hardcoded true
            for field in COMPARED:
                assert ours[field] == theirs[field], (t + 1, viewer, field)
