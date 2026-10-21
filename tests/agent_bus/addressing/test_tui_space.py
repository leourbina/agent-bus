"""`tui`: live exactly while a live TUI presence file lists the session."""
import json
import subprocess

import pytest

from agent_bus.adapters import addressing
from agent_bus.opencode_presence import presence_dir


def _entry(sid):
    return {"id": f"opencode:tui:{sid}", "kind": "opencode", "native": {"sessionID": sid}}


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def spawn():
    procs = []

    def _spawn():
        p = subprocess.Popen(["sleep", "60"])
        procs.append(p)
        return p

    yield _spawn
    for p in procs:
        p.kill()
        p.wait()


def _write(bus, pid, ids, name=None):
    d = bus / "opencode-tui"
    d.mkdir(exist_ok=True)
    (d / f"{name or pid}.json").write_text(
        json.dumps({"pid": pid, "sessionIDs": ids, "updatedAt": "2026-10-21T00:00:00Z"}))


def test_presence_dir_is_under_bus_home(bus):
    assert presence_dir() == str(bus / "opencode-tui")


def test_open_session_is_live(bus, spawn):
    _write(bus, spawn().pid, ["ses_a"])
    assert addressing.is_live(_entry("ses_a")) is True


def test_session_not_in_any_file_is_not_live(bus, spawn):
    _write(bus, spawn().pid, ["ses_a"])
    assert addressing.is_live(_entry("ses_b")) is False


def test_dead_pid_presence_file_is_ignored(bus, spawn):
    p = spawn()
    p.kill()
    p.wait()
    _write(bus, p.pid, ["ses_a"])
    assert addressing.is_live(_entry("ses_a")) is False


def test_unreadable_presence_file_is_skipped(bus, spawn):
    (bus / "opencode-tui").mkdir()
    (bus / "opencode-tui" / "bad.json").write_text("{not json")
    _write(bus, spawn().pid, ["ses_a"])
    assert addressing.is_live(_entry("ses_a")) is True


def test_session_open_in_two_tuis_survives_one_closing(bus, spawn):
    one, two = spawn(), spawn()
    _write(bus, one.pid, ["ses_a"])
    _write(bus, two.pid, ["ses_a"])
    one.kill()
    one.wait()
    assert addressing.is_live(_entry("ses_a")) is True
    two.kill()
    two.wait()
    assert addressing.is_live(_entry("ses_a")) is False


def test_address_value_is_used_without_native(bus, spawn):
    _write(bus, spawn().pid, ["ses_a"])
    assert addressing.is_live({"id": "opencode:tui:ses_a", "kind": "opencode"}) is True


def test_tui_space_has_no_mailbox():
    assert addressing.has_mailbox(_entry("ses_a")) is False
