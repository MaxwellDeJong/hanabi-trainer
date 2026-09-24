import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE.parent, HERE):  # the repo root (hanabi_data) and tests/ (helpers)
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
