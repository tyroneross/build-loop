"""Regression tests for Codex Rally engagement hook wiring."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CODEX_HOOKS = REPO / ".codex" / "hooks.json"
CLAUDE_SESSION_HOOK = REPO / "hooks" / "session-start-rally-point.sh"
CLAUDE_PRE_EDIT_HOOK = REPO / "hooks" / "pre-edit-rally-point.sh"
CLAUDE_PLUGIN_HOOKS = REPO / "hooks" / "hooks.json"


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
    assert all("--session-id" in cmd for cmd in probe_commands)
    assert all("CODEX_THREAD_ID" in cmd for cmd in probe_commands), (
        "Codex SessionStart must reuse the host thread id so sibling Codex "
        "sessions do not collapse into one Rally actor."
    )
    assert any("--start-watch" in cmd for cmd in probe_commands), (
        "Codex SessionStart must start the coordination watcher; "
        "rally presence alone does not keep Codex engaged."
    )
    assert any("--watch-parent-pid" in cmd and "PPID" in cmd for cmd in probe_commands), (
        "Codex SessionStart must tie the watcher to the long-lived host parent; "
        "using session_probe.py as the parent lets the watcher exit when the hook returns."
    )


def test_claude_session_start_watcher_uses_long_lived_parent() -> None:
    script = CLAUDE_SESSION_HOOK.read_text(encoding="utf-8")

    assert "--tool claude_code --session-id" in script
    assert "session-start-safe --workdir" in script
    assert "session-start-advance --workdir" in script
    assert script.count('--session-id "$RALLY_HOOK_SESSION_ID"') >= 4
    assert "CLAUDE_SESSION_ID" in script
    assert "--mode hook --start-watch" in script
    assert '--watch-parent-pid "${PPID}"' in script, (
        "Claude SessionStart must tie the watcher to the host parent; the "
        "short-lived session_probe process cannot own the detached watcher."
    )


def test_claude_plugin_preserves_session_start_event_stdin() -> None:
    data = json.loads(CLAUDE_PLUGIN_HOOKS.read_text(encoding="utf-8"))
    commands = [
        hook.get("command", "")
        for matcher in data["hooks"]["SessionStart"]
        for hook in matcher.get("hooks", [])
        if "session-start-rally-point.sh" in str(hook.get("command", ""))
    ]
    assert commands
    assert all("</dev/null" not in command for command in commands), (
        "The Rally SessionStart hook must receive Claude's event JSON session_id."
    )


def _recording_claude_hook_plugin(tmp_path: Path) -> tuple[Path, Path]:
    plugin = tmp_path / "plugin"
    hook_dir = plugin / "hooks"
    package = plugin / "scripts" / "rally_point"
    hook_dir.mkdir(parents=True)
    package.mkdir(parents=True)
    for source in (
        CLAUDE_SESSION_HOOK,
        CLAUDE_PRE_EDIT_HOOK,
        REPO / "hooks" / "_session_start_lib.sh",
    ):
        shutil.copyfile(source, hook_dir / source.name)
    shutil.copyfile(
        REPO / "scripts" / "rally_point" / "actor_identity.py",
        package / "actor_identity.py",
    )
    recorder = """import json, os, sys
record = {\"script\": os.path.basename(sys.argv[0]), \"argv\": sys.argv[1:]}
with open(os.environ[\"_BL_HOOK_CAPTURE\"], \"a\", encoding=\"utf-8\") as fh:
    fh.write(json.dumps(record) + \"\\n\")
"""
    (package / "hooks.py").write_text(recorder, encoding="utf-8")
    (package / "session_probe.py").write_text(recorder, encoding="utf-8")
    return hook_dir, package


def test_claude_event_session_ids_reach_start_and_pre_edit_without_env(
    tmp_path: Path,
) -> None:
    """Two event identities stay distinct even without CLAUDE_SESSION_ID."""
    hook_dir, _package = _recording_claude_hook_plugin(tmp_path)
    workdir = tmp_path / "repo"
    workdir.mkdir()
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    capture = tmp_path / "hook-calls.jsonl"
    env = dict(os.environ)
    env.pop("CLAUDE_SESSION_ID", None)
    env.update(
        {
            "CLAUDE_PROJECT_DIR": str(workdir),
            "BUILD_LOOP_RALLY_POINT_SKIP_WATCH": "1",
            "_BL_HOOK_CAPTURE": str(capture),
        }
    )

    for session_id in ("claude-event-a", "claude-event-b"):
        event = json.dumps(
            {
                "session_id": session_id,
                "tool_input": {"file_path": str(workdir / "file.py")},
            }
        )
        for hook in (
            hook_dir / "session-start-rally-point.sh",
            hook_dir / "pre-edit-rally-point.sh",
        ):
            result = subprocess.run(
                ["bash", str(hook)],
                input=event,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            assert result.returncode == 0, result.stderr

    records = [json.loads(line) for line in capture.read_text().splitlines() if line]
    for script in ("hooks.py", "session_probe.py"):
        seen = {
            record["argv"][record["argv"].index("--session-id") + 1]
            for record in records
            if record["script"] == script and "--session-id" in record["argv"]
        }
        assert {"claude-event-a", "claude-event-b"}.issubset(seen)


def test_codex_stop_targets_the_same_session_actor_as_start() -> None:
    stop_commands = _commands_for("Stop")
    commands = [cmd for cmd in stop_commands if "actor_identity.py" in cmd]

    assert commands, "Codex Stop must resolve the exact SessionStart Rally actor"
    assert all("CODEX_THREAD_ID" in cmd for cmd in commands)
    assert all("agent_rally.py" in cmd and " stop " in cmd for cmd in commands)
    assert all('--tool \"$actor\"' in cmd for cmd in commands)
    assert all('--session-id \"$rally_session\"' in cmd for cmd in commands)
    assert all("rally stop codex" not in cmd for cmd in stop_commands)
