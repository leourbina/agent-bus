"""An OpenCode session's own return socket, so it can message Claude peers.

Claude refuses a peer message whose sender names no socket of its own: it dials
`from` back to ack, and a Claude agent replies natively to that address. One
OpenCode service hosts every session, so no pid-keyed listener belongs to any
one of them. Each registered, live session gets a listener of its own, keyed by
its session id, and frames arriving on it are delivered into that session.
"""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time

import pytest

from agent_bus import listener, store, uds
from agent_bus.adapters.transport import opencode
from agent_bus.commands import agents

SID = "ses_listenA1"
ADDRESS = f"opencode:tui:{SID}"


@pytest.fixture
def bus(tmp_path, monkeypatch):
    """A tmp bus home and sessions dir, and a socket dir short enough for AF_UNIX."""
    home = tmp_path / "bus"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    sock_dir = f"/tmp/abs{secrets.token_hex(4)}"
    os.makedirs(sock_dir)
    monkeypatch.setenv("AGENT_BUS_HOME", str(home))
    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("AGENT_BUS_SOCK_DIR", sock_dir)
    monkeypatch.delenv("AGENT_BUS_LISTEN_SOCK", raising=False)
    store.ensure_dirs()
    yield {"home": str(home), "sessions": str(sessions), "socks": sock_dir}
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture
def tui(bus):
    """A live process standing in for a TUI, and a switch for its presence file."""
    proc = subprocess.Popen(["sleep", "120"])
    d = os.path.join(bus["home"], "opencode-tui")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{proc.pid}.json")

    def show(*ids: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": proc.pid, "sessionIDs": list(ids),
                       "updatedAt": "2026-10-21T00:00:00Z"}, f)

    show(SID)
    yield show
    proc.kill()
    proc.wait()


@pytest.fixture
def holders():
    """Stand-in listener processes for a faked spawn: real, live, ours to kill."""
    procs: list[subprocess.Popen] = []
    popen = subprocess.Popen  # the real one: `spawned` patches the module attribute

    def make() -> subprocess.Popen:
        p = popen(["sleep", "120"])
        procs.append(p)
        return p

    yield make
    for p in procs:
        with contextlib.suppress(OSError):
            p.kill()
        p.wait()


@pytest.fixture
def spawned(monkeypatch, holders):
    """Fake the listener spawn; record each command line."""
    calls: list[list[str]] = []
    popen = subprocess.Popen

    def fake_popen(cmd, **kw):
        # One module object, so this also sees every `ps` a liveness check runs.
        if "agent_bus" not in cmd:
            return popen(cmd, **kw)
        calls.append(list(cmd))
        return holders()

    monkeypatch.setattr(listener.subprocess, "Popen", fake_popen)
    return calls


def _register(name: str = "LiveA") -> store.RosterEntry:
    return store.register_session(name, "opencode", ADDRESS, {"sessionID": SID})


def _entry() -> store.RosterEntry | None:
    return next((e for e in store.load_roster() if e.id == ADDRESS), None)


def test_our_socket_prefers_the_sending_sessions_listener(bus, tmp_path, monkeypatch):
    sock = tmp_path / "listen.sock"
    sock.write_text("")
    entry = _register()
    entry.native = {**entry.native, "listenSock": str(sock)}
    store.save_roster_entry(entry)
    monkeypatch.setattr(uds, "ancestor_pids", lambda start=None: [])

    assert uds._our_socket("LiveA") == str(sock)
    assert uds._our_socket(None) is None
    # A sender that is not a session keeps the old resolution.
    assert uds._our_socket("someone-else") is None


def test_register_session_starts_one_listener(bus, tui, spawned):
    agents.register_session("LiveA", SID)
    agents.register_session("LiveA", SID)

    assert len(spawned) == 1
    cmd = spawned[0]
    assert cmd[cmd.index("--session") + 1] == SID
    assert cmd[cmd.index("--name") + 1] == "LiveA"


def test_renaming_a_session_restarts_its_listener(bus, tui, spawned):
    agents.register_session("LiveA", SID)
    first = listener.start_session_listen("LiveA", SID)
    assert first is not None

    agents.register_session("LiveB", SID)

    assert len(spawned) == 2
    assert spawned[1][spawned[1].index("--name") + 1] == "LiveB"
    second = listener.start_session_listen("LiveB", SID)
    assert second is not None and second != first


def test_touch_restarts_a_missing_listener_for_a_live_session(bus, tui, spawned):
    _register()
    assert agents.touch_session(SID) is True
    assert len(spawned) == 1
    agents.touch_session(SID)
    assert len(spawned) == 1


def test_touch_does_not_start_a_listener_for_a_closed_session(bus, tui, spawned):
    _register()
    tui()  # no session open
    agents.touch_session(SID)
    assert spawned == []


def _listen_sock() -> str | None:
    entry = _entry()
    return entry.native.get("listenSock") if entry is not None else None


def _exited(pid: int) -> bool:
    """Reaped, or not ours to reap any more (already reaped elsewhere)."""
    try:
        return os.waitpid(pid, os.WNOHANG)[0] == pid
    except ChildProcessError:
        return True


def _wait_for(pred, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _only_sock(sock_dir: str) -> str | None:
    found = [f for f in os.listdir(sock_dir) if f.endswith(".sock")]
    return os.path.join(sock_dir, found[0]) if found else None


def _token_for(sessions_dir: str, sock_path: str) -> str | None:
    pid = os.path.basename(sock_path).split(".")[0]
    for fn in os.listdir(sessions_dir):
        if fn.startswith(f"{pid}.") and fn.endswith(".key"):
            with open(os.path.join(sessions_dir, fn), encoding="utf-8") as f:
                return json.load(f)["peerToken"]
    return None


def test_session_listener_delivers_inbound_frames_into_the_session(bus, tui, monkeypatch):
    prompts: list[tuple[str, dict | None]] = []

    def fake_api(operation, *, data=None, **params):
        assert operation == "session.prompt", operation
        prompts.append((params["sessionID"], data))
        return {"id": "msg_1"}

    monkeypatch.setattr(opencode, "_api", fake_api)
    _register()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            uds.run_listen(name="LiveA", session_id=SID)
        except BaseException as e:  # surfaced by the assert below
            errors.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        assert _wait_for(lambda: _only_sock(bus["socks"]) is not None)
        sock_path = _only_sock(bus["socks"])
        assert sock_path is not None
        assert _wait_for(lambda: _listen_sock() == sock_path)
        assert _wait_for(lambda: _token_for(bus["sessions"], sock_path) is not None)
        time.sleep(0.1)
        token = _token_for(bus["sessions"], sock_path)

        frame = {
            "msgV": 1, "type": "user", "priority": "next",
            "message": {"role": "user", "content": (
                '<cross-session-message from="uds:/nowhere.sock" from-name="Claude A" '
                'from-mode="prompting">\nhello session\n</cross-session-message>')},
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(sock_path)
            s.sendall((json.dumps({"type": "auth", "token": token}) + "\n").encode())
            s.sendall((json.dumps(frame) + "\n").encode())
            s.shutdown(socket.SHUT_WR)
        assert _wait_for(lambda: bool(prompts))
        [(session_id, data)] = prompts
        assert session_id == SID
        assert data is not None
        assert "from Claude A" in data["text"]
        assert data["text"].endswith("\n\nhello session")
        assert errors == []

        tui()  # the TUI closes the session
        t.join(timeout=10)
        assert not t.is_alive(), "listener outlived its session"
        assert not os.path.exists(sock_path)
        entry = _entry()
        assert entry is not None and "listenSock" not in entry.native
    finally:
        tui()
        t.join(timeout=10)


def test_session_listener_exits_when_the_session_closes(bus, tui):
    _register()
    pid = listener.start_session_listen("LiveA", SID)
    assert pid is not None
    pid_file = os.path.join(bus["home"], "listeners", f"opencode-{SID}.pid")
    sock_path = os.path.join(bus["socks"], f"{pid}.sock")
    try:
        assert _wait_for(lambda: os.path.exists(sock_path), timeout=15), "never bound"
        assert _wait_for(lambda: _listen_sock() == sock_path, timeout=10)
        # The session file it publishes folds into the session's own row.
        assert _wait_for(lambda: os.path.exists(
            os.path.join(bus["sessions"], f"{pid}.json")), timeout=10)
        assert [a["name"] for a in agents.list_agents()] == ["LiveA"]

        tui()
        exited = _wait_for(lambda: _exited(pid), timeout=10)
        assert exited, "listener did not exit within 10s of its session closing"
        assert not os.path.exists(sock_path)
        assert not os.path.exists(pid_file)
    finally:
        if not _exited(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)  # ours: the pid start_session_listen spawned
            _wait_for(lambda: _exited(pid), timeout=5)
