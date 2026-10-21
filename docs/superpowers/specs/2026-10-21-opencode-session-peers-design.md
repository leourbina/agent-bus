# OpenCode sessions as full bus peers: design

Status: draft for review. Builds on `feat/opencode-transport` (PR 1), which
already delivers a message into an OpenCode session's context through the
OpenCode service (`adapters/transport/opencode.py`).

## Why

Every agent on this machine should be able to talk to every other one, and an
incoming message should land directly in the agent's context, so nobody polls
an inbox. This document covers OpenCode only; CyberStrike, Pi and omp come
later.

OpenCode v2 TUIs attach to one shared background service (`opencode serve
--service`) that runs every MCP server. As a result every OpenCode session on
the bus today registers as the same process, shares one file inbox and one
identity (`other-58929` on this machine), and replies arrive labelled with the
service, so a follow-up cannot find its way back to the session that sent it.

## Goals

1. **Each OpenCode session is its own agent.** Several sessions in one OpenCode
   service each register their own name, receive only their own messages and
   send as themselves. They never collapse into one shared service identity.
   This is the most important requirement.
2. **Found by registered name.** A session registers at startup (the `register`
   MCP tool, as every agent already does) and other agents address it by that
   name. No special addressing for OpenCode.
3. **Messages land in context.** Delivery is PR 1's transport (`session.prompt`,
   `delivery: "queue"`). The session never needs `get_inbox`.
4. **Replies are attributed to the session.** `send_message` from a registered
   session is labelled with that session's registered name, so the recipient's
   reply reaches the same session.
5. **Reachable only while a TUI has it open.** A registered session can receive
   messages only while some running OpenCode TUI process has opened it. Once
   it is opened in a TUI it stays reachable for as long as that TUI process
   runs, even if the TUI switches to another session (reading of "it should
   be always reachable"). When no live TUI has it, sends are refused like any
   absent agent, so nothing wakes a closed session and spends tokens.
   **Closing frees the name.** While no live TUI has the session, its entry
   is not live, may be pruned like any dead entry, and its name is free for
   another agent to register. A reopened session registers again, like a
   fresh start; the bus does not reclaim the old name for it (my call).
6. **`self` answers the session.** A registered session's `self` returns its
   own registered entry.

## Non-goals

- Headless sessions (`opencode run`, API-driven sessions). Out of scope.
- CyberStrike, Pi, omp, or any other harness.
- Upstreaming. This is our fork; upstream compatibility is nice, not required.

## Anti-goals (failure even if it "works")

- Any path where two sessions in one service can see each other's identity,
  messages or replies.
- Letting an MCP caller assert an arbitrary sender name. Upstream closed this
  on purpose (#156): `_call_send` ignores `from_name` from tool arguments.
  Identity must come from something the harness sets, not the model.
- A closed session being woken by a message.

## Facts established (probed live, opencode 2.0.32)

- **Session id on every tool call.** OpenCode's MCP client sends
  `params._meta["ai.opencode/sessionID"] = "ses_…"` on every `tools/call`.
  `clientInfo.name` is `"opencode"`. The harness sets `_meta`; the model does
  not control it. This is the identity source, at the same trust level the bus
  already gives `clientInfo`.
- **TUI plugins (v2).** v2 no longer reads `tui.jsonc`. TUI plugins are listed
  in `~/.config/opencode/cli.json` under `"plugins"` (paths relative to that
  directory; herdr's is `"./herdr-opencode"`, a directory containing
  `tui.js`), or added with `opencode plugin add`. The module's default export
  is `{ id, setup(api) }`. `setup` runs inside the TUI process
  (`process.pid` is the TUI's pid). `api.ui.router.current()` returns
  `{type:"session", sessionID}` while a session is on screen (observed within
  0.5s of it opening) and `{type:"home"}` otherwise. `process` `exit` fires
  when the TUI closes. Reference: `~/.config/opencode/herdr-tui-session.js`
  (`setup`) and its `cli.json` entry.
- **`opencode run` loads no TUI plugin.** Irrelevant now that headless is out.
- **The OpenCode service has no API for which client has which session open.**
  Hence the plugin.
- **Delivery wakes sessions with no TUI attached.** A queued prompt to an idle,
  unattached session starts a turn within seconds. This is why goal 5 needs a
  gate on the sending side, not just on presence display.

## Design

### 1. Presence: the TUI plugin

Ship `integrations/opencode/` (alongside `integrations/omp/`): a v2 TUI plugin
plus a README with install steps (`opencode plugin add <path>` or a
`cli.json` entry).

The plugin records, for its own TUI process, every top-level session it has
opened during its lifetime: `{pid, sessionIDs: [...]}`. "Top-level" means the
root of a session's parent chain (herdr's `root()` shows how; sub-agent child
sessions are not separate agents). It updates the record whenever the route
lands on a new root session and removes it on exit.

The record is written where the bus can read it without asking OpenCode,
under the bus home (`AGENT_BUS_HOME`), one file per TUI pid, written
atomically. Exact path and shape are the builder's call; mirror how Claude
Code's `~/.claude/sessions/<pid>.json` is consumed by
`adapters/discovery/claude.py`. A stale file whose pid is dead counts for
nothing; the bus never trusts a file without checking the pid is alive (with
the existing pid-reuse guard, `ps -o lstart=` start time, if the plugin
records it).

### 2. Identity: per-call, from `_meta`

In `mcp_server.py`, read `params._meta["ai.opencode/sessionID"]` on
`tools/call` when the client identified as opencode. Thread that session id
into the command for that one call; never store it on the server process
(the MCP server is shared by every session in the service). The commands it
affects:

- **`register(name)`**: creates or updates a roster entry for *that session*:
  kind `opencode`, address `opencode:thread:<sessionID>` (the thread space PR 1
  introduced: always-live by address, no file mailbox), name as given,
  `native.sessionID` set. Name collisions follow the bus's existing rules.
  Registering again from the same session renames it, as for other agents.
- **`send_message`**: `from_name` is the calling session's registered name;
  if the session never registered, its address `opencode:thread:<sessionID>`.
  Still never read from tool arguments (#156).
- **`self`**: the calling session's entry, or a clear "not registered" result.
- **`get_inbox` / `read_message` / `ack_message`**: a session has no file
  mailbox; messages are already in its conversation. Return empty / no-op
  rather than the service's shared inbox.
- **`list_agents`, `set_status`**: `set_status` applies to the calling
  session's entry.

A `tools/call` from opencode without the `_meta` key keeps today's behaviour,
so an older OpenCode keeps working.

**Sub-agents (my call).** A sub-agent runs in a child session, and its tool
calls may carry the child's id rather than the root's (not yet probed). The
bus maps any session id to its root (follow `parentID` via `opencode api
session.get`, cached, since a session's parent never changes) before using it
for identity, so a sub-agent sends, registers and answers `self` as its root
session. Root sessions are the only agents.

Also recognise `clientInfo.name == "opencode"` in `identify_mcp_client`, which
today does not, so the MCP connection is kind `opencode` rather than `other`.

### 3. Reachability gate

Liveness of an `opencode:thread:*` roster entry becomes "some live TUI
presence record lists its session id". This should be a liveness rule the
send path already consults (`_refuse_if_not_live` →
`addressing.is_live`), so `agent-bus list` hides closed sessions and `send`
refuses them with "receiver unavailable", without OpenCode-specific branches in
`commands/messages.py`. How to express that within the address-space model
(the thread space is "always live" for codex) is the builder's call; it must
not change codex behaviour.

PR 1's `resolve_unknown` path (sending to a bare `ses_…` id or a title with no
roster entry) must apply the same gate.

### 4. Retiring the service-level identity

The `other-58929` and `pending-<pid>` entries on this machine come from the
OpenCode service still running agent-bus 0.5.4, the version installed before
this fork. On this branch the MCP server registers itself at startup only
when `AGENT_BUS_NAME` is set, so no new process-level entries appear once the
service runs the fork. The remaining service-level entry exists only because
sessions called `register` and were all filed under the service's pid; after
this change, a `register` from an opencode session that carries `_meta`
always files the session, never the process.

Live verification needs the service to run the fork: `uv tool install` the
branch, then `opencode service restart`. Existing `other-*` / `pending-*`
entries are left to the bus's normal pruning; no migration.

## Left to the builder

- Presence file location, shape, and write mechanics.
- How the plugin learns `AGENT_BUS_HOME` (env, or the default home).
- How the liveness rule is expressed in `adapters/addressing`.
- Test strategy, within the repo's gate (ruff, basedpyright, pytest). Live
  verification against a real TUI is expected before calling it done.
- Whether presence lookups are cached.

## Verification (what done looks like)

1. Two TUIs on the shared service, sessions registered as "A" and "B".
   `agent-bus list` shows both. A Claude agent sends to "A": only A's TUI
   shows it. A replies with `send_message`: the Claude agent sees it from "A",
   replies, and the reply lands in A.
2. `self` in A returns "A"; in B returns "B".
3. Close A's TUI: "A" disappears from `list`; sending to "A" is refused, and
   nothing is injected into A's session (checked with
   `opencode api session.message.list`).
4. While A is closed, another agent can register the name "A". Reopen A's
   session in a TUI and register it again: it becomes reachable under the
   name it registered.
5. Switch B's TUI to a different session: "B" stays reachable.
6. The full gate passes.
