"""Trajectories (docs/representation.md §8): the §8.7 tests (prefix, view, round trip), moves in every
form the repo uses, storage, and the derived fields.

Runs on the example games, plus every export downloaded to data/exports/ (not committed, so those
cases only run where the data is).
"""
from collections import Counter
from functools import lru_cache

import numpy as np
import pytest

from hanabi_data import from_export, from_live, parse_capture
from hanabi_data.decision import views
from hanabi_data.engine import ACTIONS
from hanabi_data.record import load_game, player_view
from hanabi_data.rules import COPIES, SUIT_LETTERS
from hanabi_data.trajectory import (CRITICAL, FRAME_FIELDS, MAX_SEATS, MAX_SLOTS, MISPLAY_RUN_TO_END, NONE,
                                    NOT_INPUT, PLAYABLE, STEP_FIELDS, TRASH, UNKNOWN, decode_frame,
                                    decode_legal, decode_move, encode_move, load_shard, read_shards, render,
                                    status_bits, trajectory, write_shards)
from helpers import EXAMPLES, GAMES, ROOT, load_export, make_export, rank_clue, sorted_deck, discard

PATHS = {g: EXAMPLES / f"export_{g}.json" for g in GAMES}
for _p in sorted((ROOT / "data" / "exports").glob("export_*.json")):
    PATHS.setdefault(_p.stem.split("_", 1)[1], _p)
IDS = sorted(PATHS, key=int)
STEP_KEYS = ("actor", "type", "slot", "target", "value")


@lru_cache(maxsize=None)
def record(gid):
    return load_game(PATHS[gid])


@lru_cache(maxsize=None)
def traj(gid, seat):
    return trajectory(record(gid), seat)


def seats(gid):
    return range(len(record(gid)["players"]))


def actions(rec):
    return [ev for ev in rec["events"] if ev["e"] in ACTIONS]


def truncate(rec, k):
    """The record as it stood after k actions (and the draw that followed), with no ending."""
    out, seen = [], 0
    for ev in rec["events"]:
        if ev["e"] in ACTIONS:
            if seen == k:
                break
            seen += 1
        if ev["e"] != "end":
            out.append(ev)
    return {**rec, "events": out}


def assert_same(a, b, skip=(), effect_mask=0xFF):
    """Two field dicts agree, row for row over the shorter length of `a`."""
    for k in a:
        if k in skip:
            continue
        x, y = a[k], b[k][:len(a[k])]
        if k == "effect":
            x, y = x & effect_mask, y & effect_mask
        assert x.dtype == y.dtype and np.array_equal(x, y), k


# ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("gid", IDS)
def test_shapes_padding_and_no_own_cards(gid):
    rec = record(gid)
    n, hand = len(rec["players"]), rec["options"]["hand_size"]
    start, T = rec["options"]["starting_player"], len(actions(rec))
    for seat in seats(gid):
        tr = traj(gid, seat)
        assert tr.n_steps == T
        for k, shape in FRAME_FIELDS.items():
            assert tr.frames[k].shape == (T + 1,) + shape, k
        for k, shape in STEP_FIELDS.items():
            assert tr.steps[k].shape == (T,) + shape, k
        f = tr.frames
        # Never the viewer's own identities, at any frame (§8.7)
        assert set(np.unique(f["suit"][:, 0])) <= {UNKNOWN, NONE}
        assert set(np.unique(f["rank"][:, 0])) <= {UNKNOWN, NONE}
        assert not f["status"][:, 0][f["suit"][:, 0] == UNKNOWN].any()
        # Padding: absent seats, slots past the hand size, unused suits
        assert (f["suit"][:, n:] == NONE).all() and (f["suit"][:, :, hand:] == NONE).all()
        if rec["options"]["suits"] == 5:
            for k in ("stacks", "discards", "unseen"):
                assert (f[k][:, 5] == NONE).all()
        assert list(f["actor"]) == [(start + t - seat) % n for t in range(T + 1)]
        assert list(tr.steps["actor"]) == [(start + t - seat) % n for t in range(T)]
        assert list(tr.steps["is_label"]) == [int(a["by"] == seat) for a in actions(rec)]
    assert set(NOT_INPUT["frame"]) <= set(FRAME_FIELDS) and set(NOT_INPUT["step"]) <= set(STEP_FIELDS)


@pytest.mark.parametrize("gid", IDS)
def test_round_trip_against_decision_records(gid):
    """§8.7 round trip: every frame and step decodes back to the DecisionRecord it came from, as far as
    the frame holds it. `status` agrees except that trajectory `trash` also covers dead cards."""
    rec = record(gid)
    for seat in seats(gid):
        tr, vs = traj(gid, seat), list(views(rec, seat))
        suits = rec["options"]["suits"]
        for t, v in enumerate(vs):
            obs, d = v["obs"], decode_frame(tr.frames, t, suits)
            board = obs["board"]
            for k in ("score", "clues", "strikes", "deck", "pace", "stacks"):
                assert d["board"][k] == board[k], (t, k)
            assert d["board"]["discards"] == dict(Counter(obs["cards"][c] for c in board["discards"]))
            assert d["unseen"] == obs["unseen"]
            decoded = {h["seat"]: h["slots"] for h in d["hands"]}
            for hand in obs["hands"]:
                got = decoded.get(hand["seat"], [])
                assert [s["card"] for s in got] == [s["card"] for s in hand["slots"]], (t, hand["seat"])
                for mine, theirs in zip(got, hand["slots"]):
                    assert mine["id"] == theirs["id"] and mine["know"] == theirs["know"]
                    assert mine["age"] == t - theirs["drawn_t"]
                    assert mine["touches"] == min(len(theirs["touched_t"]), 7)
                    assert mine["touch_age"] == (t - theirs["touched_t"][-1] if theirs["touched_t"] else None)
                    if theirs["status"] is not None:
                        assert mine["status"] == (["trash"] if dead(theirs["id"], board, obs) else theirs["status"])
            own = (v["private"] or {}).get("own_hand")
            assert d["own_hand"] == (own + [None] * (MAX_SLOTS - len(own)) if own is not None else [None] * MAX_SLOTS)
            if t == tr.n_steps:
                break
            assert sorted(map(repr, decode_legal(tr.steps, t))) == sorted(map(repr, obs["legal"]))
            assert bool(tr.steps["is_label"][t]) == (v["label"] is not None)
            if v["label"] is not None:
                fields = {k: tr.steps[k][t] for k in STEP_KEYS}
                assert decode_move(fields, v) == {k: x for k, x in v["label"].items() if k != "card"}
                m = v["meta"]
                bits = {"misplay": 1, "lost_critical": 2, "ended_game": 4}
                assert tr.steps["effect"][t] == sum(bits[e] for e in m["label_effect"]) \
                    + MISPLAY_RUN_TO_END * bool(m["misplay_run_to_end"])
                assert tr.steps["filters"][t] == 0  # no filter is agreed yet


def dead(ident, board, obs):
    """A lower rank of the card's suit has every copy discarded (checked independently of the adapter)."""
    s, r = ident[0], int(ident[1])
    gone = Counter(obs["cards"][c] for c in board["discards"])
    return any(gone[f"{s}{q}"] >= COPIES[q] for q in range(board["stacks"][s] + 1, r))


@pytest.mark.parametrize("gid", IDS)
def test_prefix(gid):
    """§8.7 prefix: the trajectory of a record cut off after k actions is the first k + 1 frames and k
    steps of the full one, so no frame depends on the future. Only `misplay_run_to_end` may differ: it is
    a target that knows how the game ended."""
    rec = record(gid)
    T = len(actions(rec))
    for seat in seats(gid):
        full = traj(gid, seat)
        for k in sorted({0, 1, T // 3, T // 2, T - 1}):
            part = trajectory(truncate(rec, k), seat)
            assert part.n_steps == k
            assert_same(part.frames, full.frames)
            assert_same(part.steps, full.steps, effect_mask=0xFF & ~MISPLAY_RUN_TO_END)


@pytest.mark.parametrize("gid", IDS)
def test_player_view_gives_the_same_trajectory(gid):
    """§8.7 view: seat s's trajectory from the full record equals the one from `player_view(record, s)`,
    the live path, apart from the private targets."""
    rec = record(gid)
    for seat in seats(gid):
        full, mine = traj(gid, seat), trajectory(player_view(rec, seat))
        assert_same(mine.frames, full.frames, skip=("own_suit", "own_rank"))
        assert_same(mine.steps, full.steps)
        assert (mine.frames["own_suit"] == NONE).all() and (mine.frames["own_rank"] == NONE).all()
        assert {k: v for k, v in mine.meta.items() if k != "has_private"} == \
               {k: v for k, v in full.meta.items() if k != "has_private"}
        assert full.meta["has_private"] and not mine.meta["has_private"]


@pytest.mark.parametrize("name", ["deal", "turn3", "turn10"])
def test_live_capture_is_a_prefix_of_the_export(name):
    """The captured websocket stream of game 78922 (seat 0, mid-game) gives the first frames of the
    export's seat-0 trajectory."""
    export = load_export("78922")
    live = from_live(parse_capture((EXAMPLES / f"live_43267_player0_{name}.txt").read_text()),
                     players=export["players"], options=export.get("options", {}))
    mine, full = trajectory(live), trajectory(from_export(export), 0)
    assert mine.meta["viewer"] == 0 and mine.meta["table_id"] == 43267 and mine.meta["game_id"] is None
    assert_same(mine.frames, full.frames, skip=("own_suit", "own_rank"))
    assert_same(mine.steps, full.steps, effect_mask=0xFF & ~MISPLAY_RUN_TO_END)


@pytest.mark.parametrize("gid", IDS)
def test_moves_in_every_form_agree(gid):
    """A move written as a GameRecord event, a DecisionRecord label and a labeling-tool choice encodes to
    the same fields, which are the step's; decoding gives the label back; legal masks hold every legal move."""
    rec = record(gid)
    for seat in seats(gid):
        tr, vs = traj(gid, seat), list(views(rec, seat))
        for t, ev in enumerate(actions(rec)):
            v = vs[t]
            enc = encode_move(ev, v, seat=seat)
            assert all(enc[k] == (None if tr.steps[k][t] == NONE else tr.steps[k][t]) for k in STEP_KEYS), t
            if ev["by"] != seat:
                assert enc["mask"] is None
                continue
            tool = ({"type": ev["e"], "card": ev["card"]} if ev["e"] != "clue" else
                    {"type": "clue", "to_seat": ev["to"], "kind": ev["kind"], "value": ev["value"]})
            assert encode_move(v["label"], v) == enc == encode_move(tool, v, seat=seat)
            assert decode_move(enc, v) == {k: x for k, x in v["label"].items() if k != "card"}
            for move in v["obs"]["legal"]:
                head, index = encode_move(move, v)["mask"]
                assert tr.steps["legal_" + head][t][index] == 1
            total = sum(int(tr.steps[k][t].sum()) for k in ("legal_play", "legal_discard", "legal_clue"))
            assert total == len(v["obs"]["legal"])


def test_bad_moves_are_refused():
    rec = record("78921")
    v = next(views(rec, 0))
    first = actions(rec)[0]
    with pytest.raises(ValueError, match="needs the view's seat"):
        encode_move(first, v)
    with pytest.raises(ValueError, match="no such card"):
        encode_move({"type": "play", "card": 99}, v)
    with pytest.raises(ValueError, match="bad clue"):
        encode_move({"type": "clue", "to": 1, "kind": "color", "value": 2}, v)
    with pytest.raises(ValueError, match="not a legal move"):
        decode_move({"type": 2, "target": 1, "value": 5}, v)  # teal: not in this hand at the start


def test_shards_round_trip(tmp_path):
    trajs = [traj(g, s) for g in GAMES for s in seats(g)]
    export = load_export("78922")
    trajs.append(trajectory(from_live(parse_capture((EXAMPLES / "live_43267_player0_turn10.txt").read_text()),
                                      players=export["players"], options=export.get("options", {}))))
    assert write_shards(trajs, tmp_path, per_shard=3) == len(trajs)
    assert len(list(tmp_path.glob("shard-*.npz"))) == -(-len(trajs) // 3)
    back = list(read_shards(tmp_path))
    assert len(back) == len(trajs)
    for a, b in zip(trajs, back):
        assert a.meta == b.meta
        assert_same(a.frames, b.frames)
        assert_same(a.steps, b.steps)
    z = load_shard(tmp_path / "shard-00000.npz")
    assert list(z["traj_frame_start"]) == [0, trajs[0].n_steps + 1, trajs[0].n_steps + trajs[1].n_steps + 2]
    with pytest.raises(FileExistsError):
        write_shards(trajs, tmp_path)


def test_render_worked_example():
    """representation.md §9: game 78921, UI turn 4, from harikari.live's seat (absolute seat 1)."""
    text = render(traj("78921", 1), 3)
    assert "harikari.live" in text.splitlines()[0] and "seat 0 to act" in text
    seat1 = text[text.index("seat 1 (pour1out4bga)"):].splitlines()[1:6]
    assert [line.split()[1] for line in seat1] == ["R3", "P2", "T1", "P1", "T1"]
    assert text.splitlines()[-1] == "step 3: seat 0 clues P to seat 1, touching slots 2, 4 · label"
    assert render(traj("78921", 1), 11).endswith("end of the record")


def test_status_bits():
    stacks = [1, 0, 0, 0, 0]
    discards = np.zeros((5, 5), np.uint8)
    assert status_bits(0, 2, stacks, discards) == PLAYABLE
    assert status_bits(0, 1, stacks, discards) == TRASH
    assert status_bits(0, 5, stacks, discards) == CRITICAL
    discards[0, 2] = 1  # one R3 gone: the other is critical
    assert status_bits(0, 3, stacks, discards) == CRITICAL
    discards[0, 1] = 2  # both R2s gone: R3 is dead, so trash and not critical
    assert status_bits(0, 3, stacks, discards) == TRASH
    assert status_bits(0, 5, stacks, discards) == TRASH


def test_dead_card_is_trash_in_a_game():
    """Both R2s discarded while a teammate holds R3: trajectory `trash`, but no flag in the DecisionRecord (Q13)."""
    front = ["R2", "R2", "Y1", "Y2", "Y3", "R3", "G1", "G2", "G3", "G4"]
    rest = sorted_deck(5)
    for c in front:
        rest.remove(c)
    acts = [rank_clue(1, 3), rank_clue(0, 2), discard(0), rank_clue(0, 1), discard(1)]
    rec = from_export(make_export(front + rest, acts))
    tr = trajectory(rec, 0)
    r3 = (1, list(tr.frames["card"][5, 1]).index(5))
    assert tr.frames["status"][4][r3] == 0 and tr.frames["status"][5][r3] == TRASH
    slot = next(s for s in list(views(rec, 0))[5]["obs"]["hands"][1]["slots"] if s["card"] == 5)
    assert slot["id"] == "R3" and slot["status"] == []
    assert SUIT_LETTERS[tr.frames["suit"][5][r3]] == "R"
