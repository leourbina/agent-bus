# OpenCode Sessions as Full Bus Peers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each OpenCode TUI session registers, sends, and is reached as its own bus agent, reachable only while a TUI has it open, with sticky (not reserved) names.

**Architecture:** A v2 OpenCode TUI plugin writes a presence file per TUI process listing the root sessions it has opened. OpenCode sessions get their own address space, `opencode:tui:<sessionID>`, whose liveness is "some live presence file lists it". The MCP server reads `_meta["ai.opencode/sessionID"]` on every `tools/call`, maps it to the root session, and answers register/send/self/status for that session, never for the shared service process.

**Tech Stack:** Python 3.11 (agent-bus, uv, pytest, ruff 0.16.4, basedpyright), JavaScript ES module (OpenCode v2 TUI plugin), `opencode api` CLI.

**Spec:** `docs/superpowers/specs/2026-10-21-opencode-session-peers-design.md`

## Global Constraints

- Gate before any "done": `uv run ruff check`, `uv run basedpyright src/ tests/`, `uv run python -m pytest tests/ -q` all clean; paste summaries verbatim (AGENTS.md).
- Identity never comes from tool arguments (#156). Only `_meta["ai.opencode/sessionID"]`, set by the harness.
- Codex behaviour unchanged: `opencode` work must not touch the `thread` space's rules.
- A `tools/call` from opencode **without** `_meta["ai.opencode/sessionID"]` behaves exactly as today.
- Session address: `opencode:tui:<rootSessionID>`. Kind: `opencode`.
- Presence file: `<bus home>/opencode-tui/<tui pid>.json`, JSON `{"pid": <int>, "sessionIDs": [<root ses_ ids>], "updatedAt": <ISO-8601>}`, written atomically (temp file + rename). Bus home = `$AGENT_BUS_HOME` else `~/.agent-bus` (`paths.py`).
- Closed-session retention: a non-live `tui` entry is pruned once its `updatedAt` is more than 7 days old (`TUI_RETENTION_SECONDS = 7 * 24 * 3600`), or immediately when its name is taken (Task 2).
- Tests never reach the live OpenCode service or the live bus: `tests/conftest.py` already disables `opencode._available`; presence tests use `tmp_path` homes.
- No em dashes in user-facing copy (README, tool descriptions, error text).

## Review Focus

1. **Two TUIs, same session open in both** — closing one must keep the session live while the other runs. (Task 1: `test_session_open_in_two_tuis_survives_one_closing`.)
2. **Stale presence file from a crashed TUI** (pid dead, file left behind) — must count for nothing. (Task 1: `test_dead_pid_presence_file_is_ignored`.)
3. **Malformed or half-written presence file** — must not break `list`/`send` for everyone else. (Task 1: `test_unreadable_presence_file_is_skipped`.)
4. **A sub-agent's tool call** carrying a child session id — must act as the root session. (Task 3: `test_child_session_call_acts_as_root`.)
5. **Registering a name a live agent already holds, from a session** — gets the bus's usual `-2` suffix, never steals it. (Task 2: `test_session_register_collision_gets_suffix`.)

---

### Task 1: `tui` address space and presence reader; route PR 1's transport through it

**Files:**
- Modify: `src/agent_bus/address.py` (add `TUI = "tui"`, export it)
- Create: `src/agent_bus/opencode_presence.py`
- Create: `src/agent_bus/adapters/addressing/tui.py`
- Modify: `src/agent_bus/adapters/addressing/__init__.py` (add `tui` to `ADAPTERS`, `__all__`)
- Modify: `src/agent_bus/adapters/addressing/thread.py` (revert docstring to Codex-only)
- Modify: `src/agent_bus/adapters/transport/opencode.py` (`SPACE = _address.TUI`; `resolve` returns None unless the session is open)
- Test: `tests/agent_bus/addressing/test_tui_space.py` (new), `tests/agent_bus/transport/test_opencode.py` (update spellings), `tests/agent_bus/addressing/test_addressing.py` (space set)

**Interfaces:**
- Produces:
  - `opencode_presence.presence_dir(home: str | None = None) -> str` — `<bus home>/opencode-tui`
  - `opencode_presence.open_sessions(home: str | None = None) -> set[str]` — union of `sessionIDs` across presence files whose `pid` is alive (`process.is_pid_alive`); unreadable files skipped (emit nothing heavier than the existing roster-unreadable style, or nothing).
  - `opencode_presence.is_open(session_id: str, home: str | None = None) -> bool`
  - `addressing.tui`: `SPACE = TUI`; `is_live(entry) -> bool` = `is_open(native.sessionID or address value)`; `has_mailbox(entry) -> False`
  - `transport.opencode.SPACE == "tui"`; minted ids `opencode:tui:<id>`; `_as_session_id` accepts `opencode:tui:<id>` and bare `ses_…`.

- [ ] **Step 1: Write failing tests** in `tests/agent_bus/addressing/test_tui_space.py`, each writing presence files into `tmp_path` with `AGENT_BUS_HOME` set and a live `sleep 60` holder pid:
  - `test_open_session_is_live` — file `{pid: holder, sessionIDs: ["ses_a"]}` → `addressing.is_live({"id": "opencode:tui:ses_a", "kind": "opencode", "native": {"sessionID": "ses_a"}})` is True.
  - `test_session_not_in_any_file_is_not_live` → False for `ses_b`.
  - `test_dead_pid_presence_file_is_ignored` — pid of an exited process → False.
  - `test_unreadable_presence_file_is_skipped` — one file `"{not json"`, another valid → valid one still counts.
  - `test_session_open_in_two_tuis_survives_one_closing` — two holders list `ses_a`; kill one → still True; kill both → False.
  - `test_tui_space_has_no_mailbox`.
  - In `test_addressing.py`, the space-set assertion becomes `{BUS, SESSION, THREAD, TUI}`.
- [ ] **Step 2: Run** `uv run python -m pytest tests/agent_bus/addressing -q` — Expected: FAIL (module `tui` missing).
- [ ] **Step 3: Implement** `address.TUI`, `opencode_presence.py`, `addressing/tui.py`, register it in `addressing/__init__.py`; restore `thread.py`'s first docstring line to "`thread` -- a Codex thread. The space with no liveness rule.".
- [ ] **Step 4: Update the transport.** In `transport/opencode.py`: `SPACE = _address.TUI`; module docstring's address line to `opencode:tui:<sessionID>` (open only while a TUI has it, per presence). In `resolve`, after building the entry, return None unless `opencode_presence.is_open(session_id)`. In `tests/agent_bus/transport/test_opencode.py`: replace every `opencode:thread:` with `opencode:tui:`; in `fake_api` also monkeypatch `opencode_presence.is_open` to `lambda sid, home=None: True`; add `test_a_closed_session_is_not_resolved` (is_open → False ⇒ `resolve(SESSION_ID)` is None, and `messages.send(SESSION_ID, …)` raises `ValueError` matching `"no such agent"`, with no prompt recorded).
- [ ] **Step 5: Run** `uv run python -m pytest tests/agent_bus/addressing tests/agent_bus/transport -q` — Expected: PASS.
- [ ] **Step 6: Commit** `feat(opencode): tui address space, live only while a TUI has the session open`.

---

### Task 2: Store support for session entries (register, retention, name release)

**Files:**
- Modify: `src/agent_bus/store.py` (`_prune`, `register`, new `register_session`)
- Test: `tests/agent_bus/messages/test_opencode_sessions_store.py` (new)

**Interfaces:**
- Consumes: Task 1 (`addressing.is_live` for `tui` entries; `address.TUI`).
- Produces:
  - `store.register_session(name: str, kind: Kind, entry_id: str, native: dict[str, Any], cwd: str | None = None, home: str | None = None) -> RosterEntry` — upsert keyed by `entry_id` (no pid; `pid=None`). Name collision against *other live* entries (and their live former names) uses the same `name-2`, `name-3` rule as `register`. Re-registering the same `entry_id` renames it (former-name grace as in `register`'s same-pid branch is optional; keep it if it falls out of extracting a shared helper, otherwise plain rename).
  - `store.TUI_RETENTION_SECONDS = 7 * 24 * 3600`.
  - Name release: in both `register` and `register_session`, once `final_name` is chosen, delete every roster entry in the `tui` space that is **not live** and whose `name == final_name` (emit a `RosterEntryRetained`-style event, e.g. new `ev.ClosedSessionNameReleased(entry_id, name)`, if the events module requires typed events).
  - `_prune`: a non-live `tui` entry is kept unless `updatedAt` is older than `TUI_RETENTION_SECONDS`.

- [ ] **Step 1: Write failing tests** (presence via monkeypatched `opencode_presence.is_open`, toggled per test):
  - `test_register_session_creates_entry_for_that_session` — returns entry with `id == "opencode:tui:ses_a"`, `kind == "opencode"`, `pid is None`, `native == {"sessionID": "ses_a"}`, name `"Platform"`.
  - `test_session_register_collision_gets_suffix` — a live bus entry named `"Platform"` exists → session gets `"Platform-2"`.
  - `test_closed_session_entry_survives_prune` — session closed, `prune_dead_roster()` → entry still on disk.
  - `test_closed_session_entry_pruned_after_retention` — `updatedAt` 8 days old → pruned.
  - `test_reopened_session_keeps_name_when_free` — register `"Platform"`, close, prune, reopen → `find_entry("Platform")` resolves to `opencode:tui:ses_a`.
  - `test_taking_a_closed_sessions_name_releases_it` — register session `"Platform"`, close, `store.register("Platform", "claude", pid=holder)` gets exactly `"Platform"` (no suffix) and the session's entry is gone; reopen → `find_entry("Platform")` is the Claude entry, and no entry has id `opencode:tui:ses_a`.
  - `test_codex_thread_pruning_unchanged` — a `codex:thread:x` entry behaves as before (live, never pruned).
- [ ] **Step 2: Run** `uv run python -m pytest tests/agent_bus/messages/test_opencode_sessions_store.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `register_session`, name release, and the retention rule in `store.py`. Prefer extracting the existing name-collision computation in `register` into one helper both functions call, rather than duplicating it.
- [ ] **Step 4: Run** the new file plus `tests/agent_bus/messages tests/agent_bus/presence -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(store): session-keyed entries with sticky, releasable names`.

---

### Task 3: Per-call identity in the MCP server

**Files:**
- Modify: `src/agent_bus/adapters/lifecycle/__init__.py` (`identify_mcp_client`: `"opencode"` → `("opencode", None)`)
- Modify: `src/agent_bus/adapters/transport/opencode.py` (add `root_session`)
- Modify: `src/agent_bus/commands/agents.py` (session variants)
- Modify: `src/agent_bus/mcp_server.py` (`tools/call` reads `_meta`; `_CALLS` take the caller session)
- Test: `tests/agent_bus/mcp/test_opencode_identity.py` (new)

**Interfaces:**
- Consumes: Task 2 `store.register_session`; Task 1 address/space.
- Produces:
  - `transport.opencode.root_session(session_id: str) -> str` — follow `parentID` via `_api("session.get", sessionID=…)` to the root; module-level dict cache; on `OpenCodeError` return the id unchanged.
  - `transport.opencode.session_address(session_id: str) -> str` — `"opencode:tui:<id>"`.
  - `commands.agents.register_session(name: str, session_id: str, home: str | None = None) -> dict[str, Any]` — `store.register_session(name, "opencode", session_address(sid), {"sessionID": sid})`, returns `roster_to_public(...)`.
  - `commands.agents.session_self(session_id: str, home: str | None = None) -> dict[str, Any]` — the entry (public form, `"registered": True`) if it exists, else `{"registered": False, "address": session_address(sid)}`.
  - `commands.agents.set_session_status(status: str, session_id: str, home: str | None = None) -> dict[str, Any]`.
  - `mcp_server`: every `_call_*` becomes `(args: dict[str, Any], session: str | None) -> Any`; `session` is the **root** id from `_meta["ai.opencode/sessionID"]` when `_CLIENT_KIND_HINT == "opencode"`, else None. With `session` set: `register` → `agents.register_session` (no UDS listener); `send_message` → `messages.send(..., from_name=<registered name if the entry exists, else session_address(session)>)`; `self` → `agents.session_self`; `set_status` → `agents.set_session_status`; `get_inbox` → `[]`; `read_message` → raises `ValueError("messages reach this session in its conversation; there is no inbox to read")`; `ack_message` → `{"acked": False, "reason": "no inbox: messages arrive in the conversation"}`. With `session` None: today's behaviour, unchanged. Skip the `get_self()`/`touch_published_session` presence touch when `session` is set.

- [ ] **Step 1: Write failing tests** driving `mcp_server.handle_rpc` with an `initialize` (`clientInfo.name = "opencode"`) then `tools/call` messages carrying `_meta: {"ai.opencode/sessionID": …}`; monkeypatch `opencode._api` (for `session.get` parent lookups and `session.prompt`), `opencode._available`, and `opencode_presence.is_open`:
  - `test_identify_mcp_client_recognises_opencode` → `("opencode", None)`.
  - `test_register_files_the_calling_session_not_the_process` — register `"A"` from `ses_a` → roster has `opencode:tui:ses_a` named `"A"`, and no entry with this process's pid.
  - `test_two_sessions_register_separately` — `"A"` from `ses_a`, `"B"` from `ses_b` → two entries; `self` from each returns its own name.
  - `test_send_is_labelled_with_the_calling_session` — `"A"` sends to a filebus peer → peer's inbox message `from.name == "A"`.
  - `test_unregistered_session_sends_as_its_address` → `from.name == "opencode:tui:ses_c"`.
  - `test_from_name_in_arguments_is_still_ignored` — args include `from_name: "Mallory"` → label is `"A"`.
  - `test_child_session_call_acts_as_root` — `ses_child` with `parentID = ses_a` → `self` returns `"A"`.
  - `test_session_inbox_is_empty_and_ack_is_noop`.
  - `test_opencode_call_without_meta_behaves_as_before` — no `_meta` → `self` answers via the existing `agents.self_info` path.
- [ ] **Step 2: Run** `uv run python -m pytest tests/agent_bus/mcp/test_opencode_identity.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** the lifecycle change, `root_session`, `session_address`, the agents functions, and the `mcp_server` dispatch change (update every `_CALLS` entry and its call site in `tools/call`).
- [ ] **Step 4: Run** `uv run python -m pytest tests/agent_bus/mcp tests/agent_bus/transport tests/agent_bus/test_surface_naming.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(mcp): opencode tool calls act as the calling session`.

---

### Task 4: The OpenCode TUI presence plugin

**Files:**
- Create: `integrations/opencode/agent-bus/tui.js`
- Create: `integrations/opencode/README.md`
- Test: `tests/agent_bus/integration/test_opencode_tui_plugin.py` (new; skipped when `node` is not on PATH)

**Interfaces:**
- Consumes: the presence file contract in Global Constraints (Task 1 reads it).
- Produces: default export `{ id: "agent-bus.opencode.presence", setup(api) }`, no dependencies, Node built-ins only.

Behaviour of `setup(api)`: resolve bus home (`process.env.AGENT_BUS_HOME` or `~/.agent-bus`); every 500 ms read `api.ui.router.current()`; when `type === "session"`, map `sessionID` to its root via `api.data.session.get(id)?.parentID` (as `herdr-tui-session.js`'s `root()` does); if the root is new, add it to the set and rewrite `<home>/opencode-tui/<process.pid>.json` atomically (write `<file>.<pid>.tmp`, `renameSync`). On `process` `exit`, remove the file. The interval is `unref()`ed. Never throw out of `setup`; swallow fs errors.

- [ ] **Step 1: Write the failing test**: a Node script imports `tui.js`, calls `setup` with a fake `api` whose `ui.router.current()` returns `{type:"session", sessionID:"ses_child"}` and whose `data.session.get` maps `ses_child → {parentID:"ses_root"}`, `ses_root → {}`; with `AGENT_BUS_HOME=tmp_path`, wait ~1 s, then print the presence file. Assert the file exists at `tmp_path/opencode-tui/<node pid>.json` with `sessionIDs == ["ses_root"]`, and is gone after the Node process exits.
- [ ] **Step 2: Run** `uv run python -m pytest tests/agent_bus/integration/test_opencode_tui_plugin.py -q` — Expected: FAIL (file missing).
- [ ] **Step 3: Implement** `tui.js` and the README (install: add `"<absolute path>/integrations/opencode/agent-bus"` to `~/.config/opencode/cli.json` `"plugins"`, or `opencode plugin add <path>`; then restart the TUI; note that `tui.jsonc` is not read by OpenCode v2; note the service must run this agent-bus: `opencode service restart` after installing).
- [ ] **Step 4: Run** the test — Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): TUI plugin publishes which sessions this TUI has open`.

---

### Task 6: Per-session listener so OpenCode sessions can talk to Claude sessions

Added after live testing: an OpenCode session's `send_message` to a Claude peer fails with "claude peer ... refused the message", because `uds.send_peer_message` needs the sender's own return socket (`uds._our_socket()`), and a session registration starts no listener. Spec section 5.

**Files:**
- Modify: `src/agent_bus/listener.py` (new `start_session_listen`)
- Modify: `src/agent_bus/cli.py` (`listen --session <sessionID>`)
- Modify: `src/agent_bus/uds.py` (session-mode inbound delivery; `_our_socket` accepts the sender)
- Modify: `src/agent_bus/adapters/transport/claude.py` (pass the sender through)
- Modify: `src/agent_bus/commands/agents.py` (`register_session` and `touch_session` ensure the listener)
- Test: `tests/agent_bus/transport/test_opencode_session_listener.py` (new)

**Interfaces:**
- Consumes: Task 2 `store.register_session`; Task 3 `agents.register_session`, `agents.touch_session`, `opencode.session_address`; Task 1 `addressing.is_live`.
- Produces:
  - `listener.start_session_listen(name: str, session_id: str, home: str | None = None) -> int | None` — idempotent: a live listener recorded in `<listener dir>/opencode-<sessionID>.pid` is reused; otherwise spawns `python -m agent_bus listen --session <sessionID> --name <name>` detached (same spawn style, env and log handling as `start_uds_listen`). Returns the listener pid.
  - `agent-bus listen --session <sessionID>`: binds and publishes exactly like a normal listener (its own pid, its own socket, its Claude-compatible session file), records its socket path into the session's roster entry as `native.listenSock`, delivers each inbound user frame with `commands.messages.send(to=<session address>, text=<extracted text>, from_name=<extracted from-name>)` instead of filing it into a file inbox, and exits (cleaning up socket, session file, pid file, and `native.listenSock`) once `addressing.is_live(<session entry>)` is False, checked at least every 5 s, or on SIGTERM.
  - `uds._our_socket(sender: str | None = None) -> str | None`: when `sender` resolves to a roster entry in the `tui` space whose `native.listenSock` exists, return that socket first; otherwise unchanged behaviour. `send_peer_message(target_sock, text, from_name)` passes `from_name` as the sender.
  - Renaming a registered session restarts its listener under the new name (stop the recorded listener pid only, never a pattern kill).

- [ ] **Step 1: Write failing tests:**
  - `test_our_socket_prefers_the_sending_sessions_listener` — tui entry with `native.listenSock` = an existing temp file; `_our_socket("LiveA")` returns it; `_our_socket(None)` unchanged.
  - `test_register_session_starts_one_listener` — monkeypatch the spawn; `agents.register_session` twice for the same session spawns once.
  - `test_touch_restarts_a_missing_listener_for_a_live_session` and `test_touch_does_not_start_a_listener_for_a_closed_session`.
  - `test_session_listener_delivers_inbound_frames_into_the_session` — drive the session-mode frame handler with an authenticated user frame carrying `from-name="Claude A"`; assert `messages.send` (or the opencode transport's `_api`, faked) received the text addressed to `opencode:tui:<id>` with sender `Claude A`.
  - `test_session_listener_exits_when_the_session_closes` — real `agent-bus listen --session` subprocess against a tmp `AGENT_BUS_HOME` with presence toggled off; it exits within 10 s and removes its socket and pid file.
- [ ] **Step 2: Run** them — Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the new file plus `tests/agent_bus/transport tests/agent_bus/mcp -q` — Expected: PASS. Then the full gate.
- [ ] **Step 5: Commit** `feat(opencode): per-session listener so sessions can message Claude peers`.

---

### Task 5: Full gate and live verification

**Files:** none new (fixes only, if verification finds bugs; each fix gets its own failing test first).

- [ ] **Step 1: Gate.** Run the three gate commands; Expected: `All checks passed!`, `0 errors, 0 warnings, 0 notes`, pytest `N passed` with 0 failed.
- [ ] **Step 2: Install.** `uv tool install --force ~/code/projects/agent-bus`; add the plugin path to `~/.config/opencode/cli.json` `plugins` (keep `./herdr-opencode`); `opencode service restart`. **Ask the user before restarting the service and before editing `cli.json`** (both affect their running OpenCode sessions).
- [ ] **Step 3: Run the spec's verification list** (spec "Verification", items 1–5) with two throwaway TUIs in a private tmux session, registering "A" and "B" via each session's prompt. Check results with `agent-bus list`, `agent-bus send`, and `opencode api session.message.list --param sessionID=…`.
- [ ] **Step 4: Record** the verbatim gate summaries and each verification item's observed result for the hand-off.

---

## Self-review notes

- Spec coverage: goals 1–6 → Tasks 1–3; presence → Task 4; reachability gate incl. `resolve_unknown` → Task 1 Step 4; service identity → Task 3 (register files the session) + Task 5 restart; sticky names → Task 2; verification → Task 5.
- Decisions made here, not in the spec (builder's calls): `tui` address space rather than reusing `thread`; name release by deleting the closed session's entry; 7-day retention; presence file path/shape; `read_message` raising, `ack_message` no-op shape.
