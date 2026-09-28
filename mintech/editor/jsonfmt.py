"""JSON read/write with a "compact if short, else expanded" pretty-printer.

Plain json.dump(indent=2) would expand every object onto its own line and lose the
hand-tuned compactness of files like materials.json (one material per line). This
prints any object/array that fits on one line within LINE_LIMIT compactly, and only
expands what doesn't fit -- close to (not byte-identical to) the existing hand style.
Key order is preserved (Python dicts keep insertion order, and so does json.loads).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LINE_LIMIT = 110


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _fits(value: Any, indent: int) -> bool:
    if not isinstance(value, (dict, list)):
        return True
    text = _compact(value)
    return "\n" not in text and indent + len(text) <= LINE_LIMIT


def _format(value: Any, indent: int) -> str:
    if isinstance(value, dict):
        return _format_dict(value, indent)
    if isinstance(value, list):
        return _format_list(value, indent)
    return json.dumps(value, ensure_ascii=False)


def _format_dict(d: dict, indent: int) -> str:
    if not d:
        return "{}"
    if _fits(d, indent):
        return _compact(d)
    pad, cpad = " " * (indent + 2), " " * indent
    items = [f"{pad}{json.dumps(str(k), ensure_ascii=False)}: {_format(v, indent + 2)}" for k, v in d.items()]
    return "{\n" + ",\n".join(items) + "\n" + cpad + "}"


def _format_list(items: list, indent: int) -> str:
    if not items:
        return "[]"
    if _fits(items, indent):
        return _compact(items)
    pad, cpad = " " * (indent + 2), " " * indent
    rows = [f"{pad}{_format(v, indent + 2)}" for v in items]
    return "[\n" + ",\n".join(rows) + "\n" + cpad + "]"


def dumps(value: Any) -> str:
    return _format(value, 0)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    text = dumps(value) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
