"""Regression tests for retained Claude Rally engagement hook wiring.

Codex intentionally has no automatic hooks. Manifest absence and generator
non-restoration are covered by test_hook_registration_contract; explicit probe
and watcher behavior remain covered by test_session_probe."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CLAUDE_SESSION_HOOK = REPO / "hooks" / "session-start-rally-point.sh"
CLAUDE_PRE_EDIT_HOOK = REPO / "hooks" / "pre-edit-rally-point.sh"
CLAUDE_PLUGIN_HOOKS = REPO / "hooks" / "hooks.json"


def test_claude_session_start_watcher_uses_long_lived_parent(tmp_path: Path) -> None:
    import time
    hook_dir, _ = _recording_claude_hook_plugin(tmp_path)
    shutil.copyfile(REPO / "hooks/_bounded_hook.sh", hook_dir / "_bounded_hook.sh")
    workdir = tmp_path / "repo"
    workdir.mkdir()
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    capture = tmp_path / "watcher.jsonl"
    env = dict(os.environ)
    for key in ("BUILD_LOOP_RALLY_SESSION_START_FOREGROUND", "BUILD_LOOP_RALLY_POINT_SKIP_WATCH", "BUILD_LOOP_RALLY_START_PARENT_PID"):
        env.pop(key, None)
    env.update({"CLAUDE_PROJECT_DIR": str(workdir), "RALLY_TOOL_ID": "claude_code", "_BL_HOOK_CAPTURE": str(capture)})
    result = subprocess.run(["bash", str(hook_dir / "session-start-rally-point.sh")],
                            input=json.dumps({"session_id":"watcher-session"}), capture_output=True,
                            text=True, env=env, timeout=5)
    assert result.returncode == 0
    deadline = time.monotonic() + 3
    records = []
    while time.monotonic() < deadline:
        if capture.exists():
            records = [json.loads(line) for line in capture.read_text().splitlines() if line]
            if any(record["script"] == "session_probe.py" for record in records):
                break
        time.sleep(0.02)
    probe = next(record["argv"] for record in records if record["script"] == "session_probe.py")
    assert probe[probe.index("--tool") + 1] == "claude_code"
    assert probe[probe.index("--session-id") + 1] == "watcher-session"
    assert "--start-watch" in probe
    assert probe[probe.index("--watch-parent-pid") + 1] == str(os.getpid())


def test_claude_plugin_preserves_session_start_event_stdin() -> None:
    data = json.loads(CLAUDE_PLUGIN_HOOKS.read_text(encoding="utf-8"))
    commands = [
        hook.get("command", "")
        for matcher in data["hooks"]["SessionStart"]
        for hook in matcher.get("hooks", [])
    ]
    from scripts import session_start_hook_command as registration
    assert commands == [registration.NATIVE]
    assert all("</dev/null" not in command for command in commands), (
        "The consolidated SessionStart command must receive Claude's event JSON session_id."
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
            "BUILD_LOOP_RALLY_SESSION_START_FOREGROUND": "1",
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
