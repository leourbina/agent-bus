"""One MCP server, many OpenCode sessions: identity is decided per tool call.

Every OpenCode TUI shares one background service, and that service runs a
single agent-bus MCP server for all of them. So the process is nobody in
particular; the caller is whichever session OpenCode names in
`params._meta["ai.opencode/sessionID"]` on that one `tools/call`.
"""
import json
import os
import subprocess

import pytest

from agent_bus import mcp_server, opencode_presence, store
from agent_bus.adapters.lifecycle import identify_mcp_client
from agent_bus.adapters.transport import opencode
from agent_bus.commands import agents
from agent_bus.mcp_server import handle_rpc
from agent_bus.protocol import AgentTarget

OPENCODE = {"name": "opencode", "version": "2.0.32"}
SESSIONS = {
    "ses_a": {"id": "ses_a", "title": "A"},
    "ses_b": {"id": "ses_b", "title": "B"},
    "ses_c": {"id": "ses_c", "title": "C"},
    "ses_child": {"id": "ses_child", "title": "sub", "parentID": "ses_a"},
}


@pytest.fixture
def bus(tmp_path, monkeypatch):
    home = str(tmp_path / "bus")
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    # Module-level handshake state: restored after the test.
    monkeypatch.setattr(mcp_server, "_CLIENT_KIND_HINT", None)
    monkeypatch.setattr(opencode, "_ROOTS", {})

    def _api(operation, *, data=None, **params):
        if operation == "session.get":
            found = SESSIONS.get(params["sessionID"])
            if found is None:
                raise opencode.OpenCodeError("Session not found")
            return found
        raise AssertionError(f"unexpected opencode api call: {operation}")

    monkeypatch.setattr(opencode, "_api", _api)
    monkeypatch.setattr(opencode_presence, "is_open", lambda sid, home=None: True)
    handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": OPENCODE}})
    return home


@pytest.fixture
def peer(bus):
    proc = subprocess.Popen(["sleep", "60"])
    try:
        store.register("peer", "other", pid=proc.pid, home=bus)
        yield "peer"
    finally:
        proc.kill()
        proc.wait()


_ids = iter(range(100, 10_000))


def _call(tool, args=None, session=None):
    params = {"name": tool, "arguments": args or {}}
    if session is not None:
        params["_meta"] = {"ai.opencode/sessionID": session}
    reply = handle_rpc({"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call",
                        "params": params})
    assert reply is not None
    return reply


def _result(reply):
    assert "error" not in reply, reply
    return json.loads(reply["result"]["content"][0]["text"])


def _peer_inbox(home):
    return store.get_inbox(target=AgentTarget("peer"), home=home)


def test_identify_mcp_client_recognises_opencode():
    assert identify_mcp_client({"name": "opencode", "version": "2.0.32"}) == ("opencode", None)


def test_register_files_the_calling_session_not_the_process(bus):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    roster = store.load_roster(bus)
    [entry] = [e for e in roster if e.id == "opencode:tui:ses_a"]
    assert entry.name == "A"
    assert entry.kind == "opencode"
    assert not [e for e in roster if e.pid == os.getpid()]


def test_two_sessions_register_separately(bus):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    _result(_call("register", {"name": "B"}, session="ses_b"))
    ids = {e.id for e in store.load_roster(bus)}
    assert {"opencode:tui:ses_a", "opencode:tui:ses_b"} <= ids
    assert _result(_call("self", session="ses_a"))["name"] == "A"
    assert _result(_call("self", session="ses_b"))["name"] == "B"


def test_send_is_labelled_with_the_calling_session(bus, peer):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    _result(_call("send_message", {"to": peer, "text": "hi"}, session="ses_a"))
    [msg] = _peer_inbox(bus)
    assert msg["from_"].name == "A"


def test_unregistered_session_sends_as_its_address(bus, peer):
    _result(_call("send_message", {"to": peer, "text": "hi"}, session="ses_c"))
    [msg] = _peer_inbox(bus)
    assert msg["from_"].name == "opencode:tui:ses_c"


def test_from_name_in_arguments_is_still_ignored(bus, peer):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    _result(_call("send_message", {"to": peer, "text": "hi", "from_name": "Mallory"},
                  session="ses_a"))
    [msg] = _peer_inbox(bus)
    assert msg["from_"].name == "A"


def test_child_session_call_acts_as_root(bus):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    assert _result(_call("self", session="ses_child"))["name"] == "A"
    assert "opencode:tui:ses_child" not in {e.id for e in store.load_roster(bus)}


def test_unregistered_session_self_reports_its_address(bus):
    me = _result(_call("self", session="ses_c"))
    assert me == {"registered": False, "address": "opencode:tui:ses_c"}


def test_session_inbox_is_empty_and_ack_is_noop(bus):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    assert _result(_call("get_inbox", session="ses_a")) == []
    acked = _result(_call("ack_message", {"message_id": "m1"}, session="ses_a"))
    assert acked == {"acked": False, "reason": "no inbox: messages arrive in the conversation"}
    read = _call("read_message", {"message_id": "m1"}, session="ses_a")
    assert read["error"]["message"] == (
        "messages reach this session in its conversation; there is no inbox to read")


def test_session_set_status_records_on_its_entry(bus):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    _result(_call("set_status", {"status": "busy"}, session="ses_a"))
    [entry] = [e for e in store.load_roster(bus) if e.id == "opencode:tui:ses_a"]
    assert entry.status == "busy"


def test_session_tool_call_refreshes_updated_at(bus):
    _result(_call("register", {"name": "A"}, session="ses_a"))
    [entry] = [e for e in store.load_roster(bus) if e.id == "opencode:tui:ses_a"]
    entry.updatedAt = "2020-01-01T00:00:00Z"
    store.save_roster_entry(entry, bus)
    _result(_call("list_agents", session="ses_a"))
    [entry] = [e for e in store.load_roster(bus) if e.id == "opencode:tui:ses_a"]
    assert entry.updatedAt != "2020-01-01T00:00:00Z"


def test_unregistered_session_tool_call_files_nothing(bus):
    _result(_call("list_agents", session="ses_c"))
    assert "opencode:tui:ses_c" not in {e.id for e in store.load_roster(bus)}


def test_opencode_call_without_meta_behaves_as_before(bus, monkeypatch):
    monkeypatch.setattr(agents, "self_info", lambda home=None: {"via": "self_info"})
    assert _result(_call("self")) == {"via": "self_info"}


def test_meta_is_ignored_from_a_client_that_is_not_opencode(bus, monkeypatch):
    handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "codex-mcp-client"}}})
    monkeypatch.setattr(agents, "self_info", lambda home=None: {"via": "self_info"})
    assert _result(_call("self", session="ses_a")) == {"via": "self_info"}


def test_root_session_falls_back_to_the_id_when_opencode_refuses(bus):
    assert opencode.root_session("ses_missing") == "ses_missing"


def test_root_session_is_cached(bus, monkeypatch):
    calls = []
    real = opencode._api

    def counting(operation, **kw):
        calls.append(kw.get("sessionID"))
        return real(operation, **kw)

    monkeypatch.setattr(opencode, "_api", counting)
    assert opencode.root_session("ses_child") == "ses_a"
    assert opencode.root_session("ses_child") == "ses_a"
    assert calls == ["ses_child", "ses_a"]
