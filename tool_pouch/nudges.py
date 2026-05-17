"""One-time nudges shown in the CLI.

Used to surface upgrade hooks (e.g. "your local store has 5,000 traces —
try `tool-pouch sync` to push them to the cloud") without hammering the user
on every command.

State is persisted in ~/.tool_pouch/nudges.json:

    {"nudges": {"trace_count_5k": "2026-05-09T10:00:00Z"}}

`show_once(key, msg)` writes to stderr the first time it runs for a
given key, then never again.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _state_path() -> Path:
    override = os.environ.get("TOOL_POUCH_NUDGES_PATH")
    if override:
        return Path(override)
    return Path.home() / ".tool-pouch" / "nudges.json"


def _load() -> dict:
    path = _state_path()
    if not path.exists():
        return {"nudges": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"nudges": {}}


def _save(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2))
    except OSError:
        # Best-effort — never block the CLI on a bad nudge file write.
        pass


def has_shown(key: str) -> bool:
    return key in _load().get("nudges", {})


def mark_shown(key: str) -> None:
    state = _load()
    state.setdefault("nudges", {})[key] = datetime.now(timezone.utc).isoformat()
    _save(state)


def show_once(key: str, message: str, stream=None) -> bool:
    """Print `message` to stderr once for `key`. Returns True on first show."""
    if has_shown(key):
        return False
    out = stream if stream is not None else sys.stderr
    out.write(message + "\n")
    out.flush()
    mark_shown(key)
    return True


def reset(key: Optional[str] = None) -> None:
    """Test helper: clear one nudge or all of them."""
    state = _load()
    if key is None:
        state["nudges"] = {}
    else:
        state.get("nudges", {}).pop(key, None)
    _save(state)
