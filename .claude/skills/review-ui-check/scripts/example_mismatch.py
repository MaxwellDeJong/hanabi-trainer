"""Worked example: in Label mode, make moves that differ from the recorded ones and screenshot the result.

    python3 example_mismatch.py <base-url> <scratch-dir> [game_id]

Copy it as a starting point for a new check: it shows session setup, picking a move from the recorded
actions, clicking cards, pressing keys, timing screenshots around an animation, and asserting on DOM
state and console errors.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser, new_session, recorded_actions  # noqa: E402

base, scratch = sys.argv[1], sys.argv[2]
game_id = int(sys.argv[3]) if len(sys.argv) > 3 else 78921

actions, start, n = recorded_actions(base, game_id)
seat = start  # sit in the starting seat, so UI turn 1 is ours
view = new_session(base, game_id, seat)
sid = view["session"]["session_id"]
teammate = (seat + 1) % n


def differing_click(action: dict) -> tuple[int, int, str]:
    """A click (holder, card index, button) whose move is not the recorded `action`."""
    if action["type"] in (0, 1):  # recorded play/discard: give a clue instead
        return teammate, 0, "left"
    return seat, 0, "left"  # recorded clue: play our slot 1 instead


with Browser(scratch) as b:
    b.nav(f"{base}/#/label/{sid}/1")
    b.shot("turn1-before")

    b.click_card(*differing_click(actions[0])[:2], button=differing_click(actions[0])[2])
    time.sleep(0.25)  # the hand pulse peaks ~20% into its 1.6 s
    b.shot("turn1-pulse")
    assert b.js("document.querySelectorAll('.mark.ghost').length") > 0, "no ghost tag after a differing move"
    time.sleep(1.6)
    b.shot("turn1-after")  # pulse gone, ghost tag still there

    # On to our next turn: reveal the other players' moves with Space.
    for _ in range(n - 1):
        b.key(" ")
        time.sleep(0.6)  # card moves take 400 ms
    holder, index, button = differing_click(actions[n])
    b.click_card(holder, index, button=button)
    time.sleep(0.25)
    b.shot("turn2-pulse")

    print("log:\n" + (b.text(".action-log") or ""))
    print("screenshots:", b.shots)
    assert not b.errors, b.errors
print("ok")
