"""The OpenCode TUI plugin publishes which root sessions its TUI has opened."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[3] / "integrations/opencode/agent-bus/tui.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

DRIVER = """
import plugin from %(plugin)r;
import fs from "node:fs";
import path from "node:path";
const sessions = { ses_child: { parentID: "ses_root" }, ses_root: {} };
const api = {
  ui: { router: { current: () => ({ type: "session", sessionID: "ses_child" }) } },
  data: { session: { get: (id) => sessions[id] } },
};
plugin.setup(api);
await new Promise((r) => setTimeout(r, 1200));
const file = path.join(process.env.AGENT_BUS_HOME, "opencode-tui", process.pid + ".json");
const content = fs.readFileSync(file, "utf8");
console.log(JSON.stringify({ id: plugin.id, pid: process.pid, content }));
"""


def test_presence_file_lists_root_session_and_is_removed_on_exit(tmp_path: Path) -> None:
    driver = tmp_path / "driver.mjs"
    driver.write_text(DRIVER % {"plugin": PLUGIN.as_uri()})
    home = tmp_path / "bus"
    result = subprocess.run(
        ["node", str(driver)],
        env={**os.environ, "AGENT_BUS_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = json.loads(result.stdout)
    assert out["id"] == "agent-bus.opencode.presence"
    presence = json.loads(out["content"])
    assert presence["pid"] == out["pid"]
    assert presence["sessionIDs"] == ["ses_root"]
    assert presence["updatedAt"]
    assert not (home / "opencode-tui" / f"{out['pid']}.json").exists()
    assert list((home / "opencode-tui").glob("*.tmp")) == []
