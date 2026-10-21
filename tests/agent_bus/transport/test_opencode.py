"""OpenCode sessions are addressed through OpenCode's server, not the file bus."""
import subprocess

import pytest

from agent_bus import store
from agent_bus.adapters import transport
from agent_bus.adapters.transport import opencode
from agent_bus.commands import messages
from agent_bus.protocol import AgentTarget

SESSION_ID = "ses_ea3f31b81ffeofJFgMEV5WYFPO"
SESSIONS = [
    {"id": SESSION_ID, "title": "OpenArtifacts Platform",
     "location": {"directory": "/work/oa"}},
    {"id": "ses_twin1", "title": "Twins"},
    {"id": "ses_twin2", "title": "Twins"},
    {"id": "ses_child", "title": "OpenArtifacts Platform", "parentID": SESSION_ID},
]


@pytest.fixture
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_HOME", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def holder():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


@pytest.fixture
def fake_api(monkeypatch):
    """`opencode api`, answering session.get/list and recording prompts."""
    prompts = []

    def _api(operation, *, data=None, **params):
        if operation == "session.get":
            for s in SESSIONS:
                if s["id"] == params["sessionID"]:
                    return s
            raise opencode.OpenCodeError("Session not found")
        if operation == "session.list":
            return [s for s in SESSIONS if params["search"] in s["title"]]
        if operation == "session.prompt":
            assert data is not None
            prompts.append((params["sessionID"], data))
            return {"id": "msg_1", "delivery": data["delivery"]}
        raise AssertionError(operation)

    monkeypatch.setattr(opencode, "_api", _api)
    monkeypatch.setattr(opencode, "_available", lambda: True)
    monkeypatch.setattr(transport.codex, "resolve", lambda target: None)
    return prompts


@pytest.mark.parametrize("spelling", [
    f"opencode:thread:{SESSION_ID}",  # the id the bus itself emits
    SESSION_ID,                       # a bare session id
    "OpenArtifacts Platform",         # a top-level session's title
])
def test_every_spelling_reaches_the_same_session(fake_api, spelling):
    entry = opencode.resolve(AgentTarget(spelling))
    assert entry is not None
    assert entry["native"] == {"sessionID": SESSION_ID}
    assert entry["id"] == f"opencode:thread:{SESSION_ID}"


def test_the_address_we_emit_is_one_we_accept(fake_api):
    emitted = opencode.resolve(AgentTarget(SESSION_ID))
    assert emitted is not None
    again = opencode.resolve(AgentTarget(emitted["id"]))
    assert again is not None
    assert again["id"] == emitted["id"]


def test_an_ambiguous_title_is_refused_not_guessed(fake_api):
    with pytest.raises(opencode.OpenCodeError, match="address one by id"):
        opencode._find_session("Twins")
    assert opencode.resolve(AgentTarget("Twins")) is None


def test_an_unknown_session_is_not_resolved(fake_api):
    assert opencode.resolve(AgentTarget("ses_nope")) is None
    assert opencode.resolve(AgentTarget("no such title")) is None


def test_send_queues_a_prompt_that_says_who_it_is_from(bus, fake_api):
    result = messages.send(AgentTarget(SESSION_ID), "ship it", from_name=AgentTarget("Manager"),
                           home=bus)
    assert result["to"] == "OpenArtifacts Platform"
    [(session_id, data)] = fake_api
    assert session_id == SESSION_ID
    assert data["delivery"] == "queue"
    assert data["text"].endswith("\n\nship it")
    assert "from Manager" in data["text"]
    assert f"opencode:thread:{SESSION_ID}" in data["text"]


def test_an_mcp_registered_opencode_peer_still_reads_its_file_inbox(bus, holder, fake_api):
    """The regression guard: a bus-space opencode entry is not a session, and
    routing it to this transport would raise instead of delivering."""
    store.register("oc-peer", "opencode", pid=holder.pid, home=bus)
    messages.send(AgentTarget("oc-peer"), "hello", home=bus)
    assert [m["text"] for m in messages.inbox(AgentTarget("oc-peer"), home=bus)] == ["hello"]
    assert fake_api == []


def test_a_server_refusal_is_raised_not_swallowed(bus, monkeypatch):
    def refuse(operation, **_kw):
        raise opencode.OpenCodeError("Session not found")

    monkeypatch.setattr(opencode, "_api", refuse)
    entry = {"id": f"opencode:thread:{SESSION_ID}", "name": "x", "kind": "opencode",
             "native": {"sessionID": SESSION_ID}}
    with pytest.raises(opencode.OpenCodeError):
        opencode.send(entry, "hi")


def test_no_opencode_binary_means_not_mine(monkeypatch):
    monkeypatch.setattr(opencode, "_available", lambda: False)
    assert opencode.resolve(AgentTarget(SESSION_ID)) is None
