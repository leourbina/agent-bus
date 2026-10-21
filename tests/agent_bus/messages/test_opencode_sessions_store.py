"""Session-keyed roster entries: one per OpenCode TUI session.

Sticky, not reserved, names: a closed session keeps its entry (and so its
name) for `TUI_RETENTION_SECONDS`, but the name is free for anyone else to
take, and taking it drops the closed session's entry.
"""
import datetime
import json
import os
import subprocess

import pytest

from agent_bus import opencode_presence, store
from agent_bus.protocol import AgentTarget

SID = "ses_a"
EID = f"opencode:tui:{SID}"


@pytest.fixture
def bus(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    return home


@pytest.fixture
def open_sessions(monkeypatch):
    """The set of session ids presence reports open; mutate it per test."""
    opened: set[str] = set()
    monkeypatch.setattr(opencode_presence, "is_open", lambda sid, home=None: sid in opened)
    return opened


@pytest.fixture
def live_pid():
    proc = subprocess.Popen(["sleep", "60"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def _session(home, name="Platform", sid=SID):
    return store.register_session(
        name, "opencode", f"opencode:tui:{sid}", {"sessionID": sid}, cwd="/w", home=home)


def _ids(home):
    return {e.id for e in store.load_roster(home)}


def test_register_session_creates_entry_for_that_session(bus, open_sessions):
    open_sessions.add(SID)
    e = _session(bus)
    assert e.id == EID
    assert e.kind == "opencode"
    assert e.pid is None
    assert e.native == {"sessionID": SID}
    assert e.name == "Platform"
    assert EID in _ids(bus)


def test_reregistering_the_same_session_renames_it(bus, open_sessions):
    open_sessions.add(SID)
    _session(bus, "Platform")
    e = _session(bus, "Infra")
    assert e.name == "Infra"
    assert [x.id for x in store.load_roster(bus)] == [EID]


def test_session_register_collision_gets_suffix(bus, open_sessions, live_pid):
    open_sessions.add(SID)
    store.register("Platform", "claude", pid=live_pid, home=bus)
    assert _session(bus).name == "Platform-2"


def test_closed_session_entry_survives_prune(bus, open_sessions):
    open_sessions.add(SID)
    _session(bus)
    open_sessions.discard(SID)
    store.prune_dead_roster(bus)
    assert EID in _ids(bus)


def test_closed_session_entry_pruned_after_retention(bus, open_sessions):
    open_sessions.add(SID)
    _session(bus)
    open_sessions.discard(SID)
    path = store._roster_path(EID, bus)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)
    d["updatedAt"] = old.isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)
    store.prune_dead_roster(bus)
    assert EID not in _ids(bus)


def test_retention_is_seven_days():
    assert store.TUI_RETENTION_SECONDS == 7 * 24 * 3600


def test_reopened_session_keeps_name_when_free(bus, open_sessions):
    open_sessions.add(SID)
    _session(bus)
    open_sessions.discard(SID)
    store.prune_dead_roster(bus)
    open_sessions.add(SID)
    found = store.find_entry(AgentTarget("Platform"), home=bus)
    assert found is not None
    assert found.id == EID


def test_taking_a_closed_sessions_name_releases_it(bus, open_sessions, live_pid):
    open_sessions.add(SID)
    _session(bus)
    open_sessions.discard(SID)
    claude = store.register("Platform", "claude", pid=live_pid, home=bus)
    assert claude.name == "Platform"
    assert EID not in _ids(bus)
    open_sessions.add(SID)
    found = store.find_entry(AgentTarget("Platform"), home=bus)
    assert found is not None
    assert found.id == claude.id
    assert EID not in _ids(bus)


def test_a_session_taking_a_closed_sessions_name_releases_it(bus, open_sessions):
    open_sessions.add(SID)
    _session(bus)
    open_sessions.discard(SID)
    open_sessions.add("ses_b")
    b = _session(bus, sid="ses_b")
    assert b.name == "Platform"
    assert _ids(bus) == {"opencode:tui:ses_b"}


def test_bus_register_never_adopts_a_closed_session_entry(bus, open_sessions, live_pid):
    """Same name and kind as a closed session: the takeover branch must not
    hand that session's id to a pid-backed registrant."""
    open_sessions.add(SID)
    _session(bus)
    open_sessions.discard(SID)
    e = store.register("Platform", "opencode", pid=live_pid, home=bus)
    assert e.id != EID
    assert e.name == "Platform"
    assert EID not in _ids(bus)


def test_live_session_name_is_not_released(bus, open_sessions, live_pid):
    open_sessions.add(SID)
    _session(bus)
    e = store.register("Platform", "claude", pid=live_pid, home=bus)
    assert e.name == "Platform-2"
    assert EID in _ids(bus)


def test_codex_thread_pruning_unchanged(bus, open_sessions):
    store.ensure_dirs(bus)
    path = os.path.join(store.roster_dir(bus), "codex_thread_x.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "id": "codex:thread:x", "name": "x", "kind": "codex", "pid": None,
            "cwd": None, "status": "idle", "inbox": "", "native": {},
            "registeredAt": "2020-01-01T00:00:00+00:00",
            "updatedAt": "2020-01-01T00:00:00+00:00",
        }, f)
    store.prune_dead_roster(bus)
    assert "codex:thread:x" in _ids(bus)
    assert any(e.id == "codex:thread:x" for e in store.get_live_roster(bus))
