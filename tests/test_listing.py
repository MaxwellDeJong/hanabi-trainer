"""History page -> listing rows, the listing checks, and the split by seed (hanabi_data/listing.py)."""
import json

import pytest

from hanabi_data import check_record, decisions, from_export
from hanabi_data.__main__ import main
from hanabi_data.listing import (attach, in_scope, load_listing, merge, parse_history, seed_bucket, split_of,
                                 summary, write_listing)
from hanabi_data.record import load_game
from helpers import EXAMPLES, ROOT, load_export

PAGE = ROOT / "tests" / "data" / "history_sample.html"


@pytest.fixture
def rows():
    return parse_history(PAGE.read_text())


def test_parse_rows(rows):
    assert [r["game_id"] for r in rows] == [78922, 78921, 74705, 78822]  # page order
    assert rows[0] == {"game_id": 78922, "num_players": 2, "score": 0, "variant": "No Variant",
                       "datetime": "2026-09-24T00:35:32Z", "players": ["d3m0n", "harikari.live"],
                       "seed": "p2v0s713", "seed_games": 8}
    assert [in_scope(r) for r in rows] == [True, True, False, True]  # 74705 is Brown


def test_rows_match_the_exports(rows):
    """Seed, players (sorted: the page doesn't keep seat order) and score agree with the exports."""
    for row in rows:
        if not in_scope(row):
            continue
        export = load_export(str(row["game_id"]))
        assert (row["seed"], row["players"], row["num_players"]) == (export["seed"], sorted(export["players"]),
                                                                       len(export["players"]))
        assert check_record(from_export(export, listing=row)) == []


def test_unreadable_row_is_refused():
    page = PAGE.read_text().replace("UTC</td>", "</td>", 1)
    with pytest.raises(ValueError, match="history row with date"):
        parse_history(page)
    page = PAGE.read_text().replace("<td>2</td>", "<td>3</td>", 1)  # 78922's player count
    with pytest.raises(ValueError, match="3 players but"):
        parse_history(page)


def test_merge(rows):
    other = [dict(rows[0], seed_games=9), rows[1]]
    merged = merge([rows, other])
    assert list(merged) == [74705, 78822, 78921, 78922]  # sorted by game ID
    assert merged[78922]["seed_games"] == 9  # the newer count
    with pytest.raises(ValueError, match="differs between history pages"):
        merge([rows, [dict(rows[0], score=5)]])


def test_listing_file_round_trip(rows, tmp_path):
    merged = merge([rows])
    write_listing(merged, tmp_path / "listing.jsonl")
    assert load_listing(tmp_path / "listing.jsonl") == merged == load_listing(PAGE)


def test_listing_checks(rows):
    export, row = load_export("78921"), rows[1]
    assert check_record(from_export(export, listing=row)) == []
    for key, value, problem in [("score", 30, "final score 0, the history page says 30"),
                                ("players", ["a", "b"], "players ['harikari.live', 'pour1out4bga'], the history page says ['a', 'b']"),
                                ("num_players", 3, "num_players 2, the history page says 3"),
                                ("variant", "No Variant", "variant 6 Suits, the history page says No Variant"),
                                ("seed", "p2v1s1", "seed p2v1s4001, the history page says p2v1s1"),
                                ("game_id", 1, "game_id 78921, the history page says 1")]:
        assert check_record(from_export(export, listing={**row, key: value})) == [problem]


def test_attach_fills_meta(rows):
    listing = merge([rows])
    record = load_game(EXAMPLES / "export_78921.json", listing)
    assert record["listing"] == listing[78921]
    assert {d["meta"]["datetime"] for d in decisions(record)} == {"2026-09-23T04:31:59Z"}
    assert load_game(EXAMPLES / "export_78921.json")["listing"] is None
    kept = {**record, "listing": {"score": 0}}
    assert attach(kept, listing) is kept  # an existing listing is never replaced


def test_split_by_seed():
    seeds = [f"p{n}v{v}s{k}" for n in range(2, 6) for v in (0, 1) for k in range(1, 2001)]
    assert seed_bucket("p2v1s4001") == seed_bucket("p2v1s4001")
    assert all(0 <= seed_bucket(s) < 100 for s in seeds)
    counts = {name: sum(split_of(s) == name for s in seeds) for name in ("train", "valid", "test")}
    assert sum(counts.values()) == len(seeds)
    assert 0.03 < counts["test"] / len(seeds) < 0.07 and 0.03 < counts["valid"] / len(seeds) < 0.07
    # Frozen: if these move, every game's split has changed.
    assert [split_of(s) for s in ("p2v1s4001", "p4v106s1", "p3v13s17")] == ["train", "test", "valid"]
    assert [seed_bucket(s) for s in ("p2v1s4001", "p4v106s1", "p3v13s17")] == [93, 4, 5]


def test_cli(tmp_path, capsys):
    out = tmp_path / "listing.jsonl"
    assert main(["listing", str(PAGE), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "4 games" in text and "in scope (No Variant / 6 Suits, 2-5 players): 3" in text
    assert main(["check", "--listing", str(out), str(EXAMPLES / "export_78922.json")]) == 0
    assert capsys.readouterr().out.strip().endswith("export_78922.json: ok")
    assert main(["convert-export", "--listing", str(PAGE), str(EXAMPLES / "export_78822.json")]) == 0
    assert json.loads(capsys.readouterr().out)["listing"]["datetime"] == "2026-09-23T03:46:37Z"
    assert summary(load_listing(out))[0].startswith("4 games (IDs 74705-78922")
