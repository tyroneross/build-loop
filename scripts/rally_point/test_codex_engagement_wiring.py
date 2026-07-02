"""Regression tests for Codex Rally engagement hook wiring."""

from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CODEX_HOOKS = REPO / ".codex" / "hooks.json"


def _commands_for(event: str) -> list[str]:
    data = json.loads(CODEX_HOOKS.read_text(encoding="utf-8"))
    commands: list[str] = []
    for matcher in data.get("hooks", {}).get(event, []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command")
            if isinstance(command, str):
                commands.append(command)
    return commands


def test_codex_session_start_probe_starts_watcher() -> None:
    """Presence without --start-watch recreates the dormant-engagement failure."""
    probe_commands = [
        cmd
        for cmd in _commands_for("SessionStart")
        if "session_probe.py" in cmd and "--tool codex" in cmd
    ]

    assert probe_commands, "Codex SessionStart must invoke session_probe.py"
    assert any("--mode hook" in cmd for cmd in probe_commands)
    assert any("--start-watch" in cmd for cmd in probe_commands), (
        "Codex SessionStart must start the coordination watcher; "
        "rally presence alone does not keep Codex engaged."
    )
    assert any("--watch-parent-pid" in cmd and "PPID" in cmd for cmd in probe_commands), (
        "Codex SessionStart must tie the watcher to the long-lived host parent; "
        "using session_probe.py as the parent lets the watcher exit when the hook returns."
    )
