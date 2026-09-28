"""The editor's own tiny bit of persisted state (window size, last tab) -- lives in this
folder, never mixed into the mintech/*.json source files it edits."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

STATE_FILE = Path(__file__).resolve().parent / "state.json"
DEFAULTS: Dict[str, Any] = {"geometry": "1280x860", "last_tab": 0}


def load() -> Dict[str, Any]:
    if STATE_FILE.is_file():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return {**DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


def save(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
