"""docs/representation.md §8 is generated, not hand-written: the CLI output, the example file and
the document agree."""
import json
import re
import subprocess
import sys

from helpers import EXAMPLES, ROOT

EXAMPLE = EXAMPLES / "decision_78921_turn4.json"


def test_cli_output_is_the_example_file():
    out = subprocess.run([sys.executable, "-m", "hanabi_data", "decision", str(EXAMPLES / "export_78921.json"),
                          "4", "--pretty"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out == EXAMPLE.read_text()


def test_design_doc_shows_the_example_file():
    doc = (ROOT / "docs" / "representation.md").read_text()
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", doc, re.S)]
    assert json.loads(EXAMPLE.read_text()) in blocks
