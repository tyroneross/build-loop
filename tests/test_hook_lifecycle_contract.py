"""Ordinary turn completion must preserve an unfinished run owned by a peer."""
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_architecture_observer_runs_after_successful_edit_in_claude():
    for name in ("hooks.json",):
        events = json.loads((ROOT / "hooks" / name).read_text())["hooks"]
        locations = [(event, group.get("matcher"))
                     for event, groups in events.items() for group in groups
                     for handler in group["hooks"]
                     if "pre-edit-architecture.sh" in handler.get("command", "")]
        assert locations == [("PostToolUse", "Edit|Write")]


def test_registered_stop_does_not_label_a_live_peer_as_crashed(tmp_path):
    # Run the registered commands with unrelated Stop services stubbed out.
    # The former crash annotator is deliberately available as shipping code:
    # restoring its registration must reproduce the actual state corruption.
    state = tmp_path / ".build-loop/state.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"execution": {"phase": "execute",
                      "session_id": "peer-still-working"}}))
    plugin = tmp_path / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "hooks").mkdir()
    for name in ("state_finalize.py", "atomic_io.py"):
        (plugin / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    before = state.read_bytes()
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(plugin), CLAUDE_PROJECT_DIR=str(tmp_path))
    event = json.dumps({"hook_event_name": "Stop", "session_id": "different-session",
                        "cwd": str(tmp_path), "stop_hook_active": False})
    for group in json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["Stop"]:
        for handler in group["hooks"]:
            subprocess.run(["bash", "-c", handler["command"]], input=event,
                           text=True, capture_output=True, env=env, cwd=tmp_path,
                           timeout=10, check=True)
    assert state.read_bytes() == before


def test_stop_does_not_report_unowned_tracked_dirt():
    # Global Git dirt is not evidence that this turn left completed work behind.
    for name in ("hooks.json",):
        events = json.loads((ROOT / "hooks" / name).read_text())["hooks"]
        commands = [handler["command"] for group in events["Stop"] for handler in group["hooks"]]
        assert not any("commit-state.sh" in command or "commit_state_check.py" in command
                       for command in commands)
    assert (ROOT / "scripts/commit_state_check.py").exists()  # Explicit check stays available.
