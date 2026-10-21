"""`tui` -- an OpenCode session, live only while a TUI has it open.

Liveness is presence: some live `opencode-tui/<pid>.json` lists the session
(see opencode_presence). Delivery goes through OpenCode's own server, so no
mailbox: a file inbox here would be write-only.
"""

from __future__ import annotations

from typing import Any

from ... import opencode_presence
from ...address import TUI
from ...address import parse as parse_address

SPACE = TUI


def _session_id(entry: Any) -> str:
    get = entry.get if isinstance(entry, dict) else lambda k: getattr(entry, k, None)
    native = get("native") or {}
    sid = native.get("sessionID") if isinstance(native, dict) else None
    if sid:
        return str(sid)
    return parse_address(str(get("id") or ""), kind_hint=get("kind")).value


def is_live(entry: Any) -> bool:
    return opencode_presence.is_open(_session_id(entry))


def has_mailbox(entry: Any) -> bool:
    return False
