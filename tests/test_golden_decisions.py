"""The engine's decision records match a frozen copy, at every position of every example game, from
every seat. The copy (tests/data/golden_decisions.jsonl) is the output of the original prototype,
which was checked against screenshots and hanab.live's reducer; `meta` is not compared.

Regenerate it only for an intended change to the decision record, and review the diff:

    python3 tests/test_golden_decisions.py
"""
import json

import pytest

from hanabi_data import decision_record, from_export
from helpers import GAMES, ROOT, load_export, norm

GOLDEN = ROOT / "tests" / "data" / "golden_decisions.jsonl"
COMPARED = ("schema", "key", "obs", "label", "private")


def records(game_id):
    """(viewer, record) for every position and seat of an example game, as stored in the golden file."""
    export = load_export(game_id)
    record = from_export(export)
    for turn in range(1, len(export["actions"]) + 2):
        for viewer in range(len(export["players"])):
            d = norm(decision_record(record, turn, viewer=viewer))
            yield {"viewer": viewer, **{k: d[k] for k in COMPARED}}


def golden(game_id):
    lines = [json.loads(line) for line in GOLDEN.read_text().splitlines()]
    return [g for g in lines if str(g["key"]["game_id"]) == game_id]


@pytest.mark.parametrize("game_id", GAMES)
def test_every_position_and_seat_matches_golden(game_id):
    ours, theirs = list(records(game_id)), golden(game_id)
    assert len(ours) == len(theirs)
    for a, b in zip(ours, theirs):
        for field in COMPARED:
            assert a[field] == b[field], (a["key"]["turn"], a["viewer"], field)


if __name__ == "__main__":
    GOLDEN.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for g in GAMES for r in records(g)))
