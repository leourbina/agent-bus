"""Which OpenCode sessions have a TUI open right now.

An OpenCode TUI plugin writes `<bus home>/opencode-tui/<tui pid>.json`:
`{"pid": int, "sessionIDs": [root ses_ ids], "updatedAt": ISO-8601}`. A session
is open while any file whose pid is alive lists it. This module only reads.

The plugin removes its file on a clean exit only, so a TUI killed by a signal
leaves one behind. Once that pid is reused, the file would wake the session it
lists; a file written before its pid's process started is that leftover, and
counts for nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import get_home
from .process import is_pid_alive, proc_start_epoch

DIRNAME = "opencode-tui"

# Start times and mtimes are compared at second granularity (`ps` lstart),
# and the plugin writes moments after its process starts.
START_TOLERANCE_SECONDS = 2.0


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
        if is_pid_alive(pid) and not _written_before_start(path, pid):
            found.update(i for i in ids if isinstance(i, str))
    return found


def _written_before_start(path: Path, pid: int) -> bool:
    """Was this file last written before `pid`'s current process started?
    False when either time cannot be read: "cannot tell" is not stale."""
    started = proc_start_epoch(pid)
    if started is None:
        return False
    try:
        written = path.stat().st_mtime
    except OSError:
        return False
    return started > written + START_TOLERANCE_SECONDS


def is_open(session_id: str, home: str | None = None) -> bool:
    return session_id in open_sessions(home)
