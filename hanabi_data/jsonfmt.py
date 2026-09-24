"""JSON with short containers kept on one line (the layout used in docs/representation.md)."""
from __future__ import annotations

import json


def pretty(x, indent: int = 0, width: int = 104) -> str:
    pad = "  " * indent
    one = json.dumps(x, separators=(", ", ": "))
    if len(one) + len(pad) <= width or not isinstance(x, (dict, list)):
        return one
    if isinstance(x, dict):
        items = [f"{pad}  {json.dumps(str(k))}: {pretty(v, indent + 1, width)}" for k, v in x.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    items = [f"{pad}  {pretty(v, indent + 1, width)}" for v in x]
    return "[\n" + ",\n".join(items) + f"\n{pad}]"


def compact(x) -> str:
    return json.dumps(x, separators=(",", ":"))
