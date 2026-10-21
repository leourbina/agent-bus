# agent-bus OpenCode TUI plugin

Makes each OpenCode TUI session its own agent on agent-bus. The plugin writes
`<bus home>/opencode-tui/<tui pid>.json` listing the root sessions that TUI has
opened; the bus reads those files to know which sessions are open. Bus home is
`$AGENT_BUS_HOME`, else `~/.agent-bus`. The file is removed when the TUI exits.

## Install

Either add the absolute path of the `agent-bus` directory here to the
`"plugins"` array in `~/.config/opencode/cli.json`:

```json
{ "plugins": ["<absolute path>/integrations/opencode/agent-bus"] }
```

or run `opencode plugin add <absolute path>/integrations/opencode/agent-bus`.

Then restart the TUI. OpenCode v2 does not read `tui.jsonc`, so listing the
plugin there has no effect.

The OpenCode service must run this agent-bus: run `opencode service restart`
after installing.
