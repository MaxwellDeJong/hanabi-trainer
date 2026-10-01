"""The corpus build (hanabi_data/corpus.py): what goes in, what's left out and why, splits, and that a
parallel build writes exactly what a serial one does."""
from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from hanabi_data.__main__ import main
from hanabi_data.corpus import (DUPLICATE, FAILED_CHECK, INVALID, OK, UNSUPPORTED, build, game_files,
                                report)
from hanabi_data.listing import split_of
from hanabi_data.record import load_game
from hanabi_data.trajectory import read_shards, trajectory

from helpers import EXAMPLES, GAMES, ROOT, load_export, make_export, play, sorted_deck

SYNTHETIC = sorted((ROOT / "tests" / "data").glob("synthetic_*.json"))
LISTING = {78822: {"game_id": 78822, "score": 99}}  # disagrees with the export: a failed check


@pytest.fixture
def games(tmp_path):
    """A folder of good games plus one of each kind of file the build must leave out."""
    d, dup = tmp_path / "games", tmp_path / "dup"
    d.mkdir()
    dup.mkdir()
    for g in GAMES:
        shutil.copy(EXAMPLES / f"export_{g}.json", d)
    for p in SYNTHETIC:
        shutil.copy(p, d)
    shutil.copy(EXAMPLES / "export_78921.json", dup)
    write = lambda name, doc: (d / name).write_text(doc if isinstance(doc, str) else json.dumps(doc))
    write("export_5.json", make_export(sorted_deck(6), [], options={"variant": "Rainbow (6 Suits)"}, game_id=5))
    write("export_6.json", {**load_export("78921"), "id": 6, "seed": None})
    write("export_7.json", make_export(sorted_deck(6), [play(40)], options={"variant": "6 Suits"}, game_id=7))
    write("export_8.json", make_export(sorted_deck(6), [], options={"variant": "6 Suits"}, game_id=8))
    write("export_9.json", "not json")
    return [dup, d]


def by_id(manifest):
    """Rows by game ID (by file name when there's none), skipping duplicates."""
    return {r["game_id"] if r["game_id"] is not None else r["path"].rsplit("/", 1)[-1]: r
            for r in manifest if r["status"] != DUPLICATE}


def assert_same(a, b):
    assert a.meta == b.meta
    for k in a.frames:
        assert np.array_equal(a.frames[k], b.frames[k]), k
    for k in a.steps:
        assert np.array_equal(a.steps[k], b.steps[k]), k


def test_statuses(games, tmp_path):
    manifest = build(games, tmp_path / "corpus", rows=LISTING, jobs=1)
    rows = by_id(manifest)
    assert len(manifest) == 3 + len(SYNTHETIC) + 1 + 5
    assert [r["status"] for r in manifest].count(DUPLICATE) == 1
    assert {rows[i]["status"] for i in (78921, 78922)} == {OK}
    assert rows[78822]["status"] == FAILED_CHECK and "history page says 99" in rows[78822]["problems"][0]
    assert rows[5]["status"] == UNSUPPORTED and "Rainbow" in rows[5]["problems"][0]
    assert rows[6]["status"] == UNSUPPORTED and rows[6]["problems"] == ["no seed, so no split"]
    assert rows[7]["status"] == INVALID and "not in seat 0's hand" in rows[7]["problems"][0]
    assert rows[8]["status"] == FAILED_CHECK and "truncated" in rows[8]["problems"][0]
    assert rows["export_9.json"]["status"] == INVALID
    ok = [r for r in manifest if r["status"] == OK]
    assert len(ok) == 2 + len(SYNTHETIC)
    for r in ok:
        assert r["trajectories"] == r["players"] and r["listed"] is False
    assert rows[78921]["catches"] == []
    assert [c["filters"] for c in rows[78922]["catches"]] == [["play_clued_5_no_4"]]  # turn 10 (label-filtering.md)
    assert json.loads((tmp_path / "corpus" / "games.jsonl").read_text().splitlines()[0]) == manifest[0]


def test_shards_hold_every_seat_of_every_good_game_in_its_split(games, tmp_path):
    out = tmp_path / "corpus"
    manifest = build(games, out, jobs=1, per_shard=2)
    assert not (tmp_path / "corpus.partial").exists()
    expected = {"train": [], "valid": [], "test": []}
    for r in manifest:
        if r["status"] == OK:
            record = load_game(r["path"])
            assert r["split"] == split_of(record["raw"]["seed"])
            expected[r["split"]] += [trajectory(record, s) for s in range(len(record["players"]))]
    assert sum(map(len, expected.values())) == sum(r.get("trajectories", 0) for r in manifest)
    for split, trajs in expected.items():
        back = list(read_shards(out / split))
        assert len(back) == len(trajs)
        for a, b in zip(trajs, back):
            assert_same(a, b)
    assert (out / "report.txt").read_text() == "\n".join(report(manifest, out)) + "\n"


def test_parallel_build_is_identical(games, tmp_path):
    one = build(games, tmp_path / "one", jobs=1, per_shard=3)
    many = build(games, tmp_path / "many", jobs=3, per_shard=3)
    assert one == many
    for split in ("train", "valid", "test"):
        assert sorted(p.name for p in (tmp_path / "one" / split).iterdir()) == \
            sorted(p.name for p in (tmp_path / "many" / split).iterdir())
        for a, b in zip(read_shards(tmp_path / "one" / split), read_shards(tmp_path / "many" / split)):
            assert_same(a, b)


def test_refuses_existing_or_interrupted_output(games, tmp_path):
    (tmp_path / "corpus").mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        build(games, tmp_path / "corpus", jobs=1)
    (tmp_path / "next.partial").mkdir()
    with pytest.raises(FileExistsError, match="interrupted"):
        build(games, tmp_path / "next", jobs=1)


def test_game_files_order(tmp_path):
    for name in ("export_10.json", "export_9.json", "notes.txt", "export_100.json"):
        (tmp_path / name).write_text("{}")
    other = tmp_path / "sub"
    other.mkdir()
    (other / "export_50.json").write_text("{}")
    names = [p.name for p in game_files([tmp_path, other / "export_50.json", tmp_path / "export_9.json"])]
    assert names == ["export_9.json", "export_10.json", "export_50.json", "export_100.json"]
    with pytest.raises(ValueError, match="no such file"):
        game_files([tmp_path / "missing"])


def test_cli(games, tmp_path, capsys):
    assert main(["corpus", *map(str, games), "--out", str(tmp_path / "all"), "--jobs", "1"]) == 1
    printed = capsys.readouterr().out
    assert "left out (" in printed and "built in" in printed
    good = [str(EXAMPLES / f"export_{g}.json") for g in GAMES]
    assert main(["corpus", *good, str(games[0]), "--out", str(tmp_path / "good"), "--jobs", "2"]) == 0
    assert "3 games in the corpus, 1 left out (duplicate 1)" in capsys.readouterr().out
