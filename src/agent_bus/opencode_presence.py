"""Which OpenCode sessions have a TUI open right now.

An OpenCode TUI plugin writes `<bus home>/opencode-tui/<tui pid>.json`:
`{"pid": int, "sessionIDs": [root ses_ ids], "updatedAt": ISO-8601}`. A session
is open while any file whose pid is alive lists it. This module only reads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import get_home
from .process import is_pid_alive

DIRNAME = "opencode-tui"


def presence_dir(home: str | None = None) -> str:
    return os.path.join(home or get_home(), DIRNAME)


def open_sessions(home: str | None = None) -> set[str]:
    """Session ids listed by any presence file with a live pid. Unreadable
    files are skipped."""
    found: set[str] = set()
    try:
        files = sorted(Path(presence_dir(home)).glob("*.json"))
    except OSError:
        return found
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = data["pid"]
            ids = data["sessionIDs"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(ids, list):
            continue
        if is_pid_alive(pid):
            found.update(i for i in ids if isinstance(i, str))
    return found


def is_open(session_id: str, home: str | None = None) -> bool:
    return session_id in open_sessions(home)
