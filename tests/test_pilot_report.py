"""The pilot report (`check --summary`, hanabi_data/check.py): every kind of game it has to describe, and
the counts by year."""
from __future__ import annotations

import json
import shutil

from hanabi_data.__main__ import main
from hanabi_data.check import game_facts, problem_kind, summary

from helpers import EXAMPLES, ROOT, load_export

DATA = ROOT / "tests" / "data"


def row(game_id, year, **kw):
    return {"game_id": game_id, "datetime": f"{year}-06-01T12:00:00Z", **kw}


def test_problem_kinds():
    assert problem_kind("final score 3, the history page says 0") == "history page differs: final score"
    assert problem_kind("players ['a', 'b'], the history page says ['a', 'c']") == "history page differs: players"
    assert problem_kind("replay: end condition 8 on turn 34 is not explained by the rules") == \
        problem_kind("replay: end condition 8 on turn 51 is not explained by the rules")


def test_facts_for_each_kind_of_game(tmp_path):
    rows = {78921: row(78921, 2026), 78822: row(78822, 2022, score=99)}
    good = game_facts(EXAMPLES / "export_78921.json", rows)
    assert good["problems"] == [] and good["year"] == "2026" and good["variant"] == "6 Suits"
    assert good["players"] == 2 and good["options"]["allOrNothing"] is True and good["end"] is not None
    mismatch = game_facts(EXAMPLES / "export_78822.json", rows)
    assert [problem_kind(p) for p in mismatch["problems"]] == ["history page differs: final score"]
    assert mismatch["turns"] is not None  # a failed check still describes the game

    brown = load_export("78921") | {"id": 5, "options": {"variant": "Brown (6 Suits)"}}
    (tmp_path / "export_5.json").write_text(json.dumps(brown))
    unsupported = game_facts(tmp_path / "export_5.json")
    assert unsupported["variant"] == "Brown (6 Suits)" and unsupported["players"] == 2
    assert unsupported["problems"][0].startswith("Unsupported: ") and unsupported["year"] is None

    (tmp_path / "export_6.json").write_text("not json")
    assert game_facts(tmp_path / "export_6.json")["problems"][0].startswith("JSONDecodeError")


def test_deck_out_and_play_past_it():
    aon = game_facts(DATA / "synthetic_aon_deck.json")  # All or Nothing: no final round
    assert aon["problems"] == [] and aon["deck_out"] is not None and aon["after_deck"] > aon["players"]
    final = game_facts(DATA / "synthetic_final_round_full_4p.json")  # one more turn each, then the end
    assert final["problems"] == [] and final["after_deck"] == final["players"] == 4


def test_summary_counts_and_years():
    facts = [
        {**game_facts(EXAMPLES / "export_78921.json"), "year": "2026"},
        {**game_facts(EXAMPLES / "export_78921.json"), "year": "2026"},  # a duplicate file
        {**game_facts(DATA / "synthetic_aon_deck.json"), "year": "2022",
         "options": {"allOrNothing": True, "newRule": True}},
        {**game_facts(EXAMPLES / "export_78822.json"), "problems": ["final score 3, the history page says 0"]},
    ]
    lines = summary(facts)
    assert lines[0] == "3 games, 2 pass every check, 1 with problems (1 duplicate files skipped)"
    assert "options the engine doesn't read (check none of them changes the rules): newRule 1" in lines
    assert "no history-page row: 1 games (e.g. 78822)" in lines
    assert "  1 × history page differs: final score (e.g. 78822)" in lines
    years = lines[lines.index("by year (history page):") + 1:]
    assert [y.split(":")[0].strip() for y in years] == ["2022", "2026", "unlisted"]
    assert years[0].startswith("  2022: 1 games · 0 with problems") and "(1 played on past a round)" in years[0]
    assert "1 with problems" in years[2]


def test_cli(tmp_path, capsys):
    games = tmp_path / "exports"
    games.mkdir()
    for g in ("78921", "78822", "78922"):
        shutil.copy(EXAMPLES / f"export_{g}.json", games)
    (games / "download_log.jsonl").write_text("{}\n")  # not a game: ignored
    listing = tmp_path / "listing.jsonl"
    listing.write_text(json.dumps(row(78822, 2026, score=99)) + "\n")
    assert main(["check", str(games), "--listing", str(listing), "--summary"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith("export_78822.json: final score 0, the history page says 99")
    assert out[2] == "3 games, 2 pass every check, 1 with problems"
    assert main(["check", str(games / "export_78921.json"), "--summary"]) == 0
    assert capsys.readouterr().out.startswith("1 games, 1 pass every check, 0 with problems")
