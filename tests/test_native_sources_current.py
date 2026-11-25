# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""bin/native/sources.current: the untracked digest of the tree that the
native hook launchers compare with bin/native/bl.sources.

No digest is tracked in git (a merge that skipped the old pre-commit refresh
turned main CI red for four days, 2026-11-21). SessionStart and the
PostToolUse Edit|Write hook write this file instead; see
scripts/build_native.py for the contract.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
try:
    import session_start_hook_command as registration
    import trace_hook_command as trace
finally:
    sys.path.pop(0)
_spec = importlib.util.spec_from_file_location("build_native", REPO / "scripts" / "build_native.py")
bn = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("build_native", bn)
_spec.loader.exec_module(bn)

STUB_BL = '#!/bin/sh\n: > "$BL_STUB_MARK"\nprintf "{}"\nexit 0\n'


@pytest.fixture
def plugin(tmp_path):
    """A git checkout holding every watched path, the real build_native.py and
    PostToolUse edit hook, and a stub bl that records that it ran."""
    root = tmp_path / "plugin"
    for rel in bn.MIRRORED + ("Cargo.toml", "Cargo.lock", "crates/bl/src/main.rs"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(rel.encode() + b"\n")
    for rel in ("scripts/build_native.py", "hooks/pre-edit-architecture.sh"):
        (root / rel).write_bytes((REPO / rel).read_bytes())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    native = root / "bin" / "native"
    native.mkdir(parents=True)
    (native / "bl").write_text(STUB_BL)
    (native / "bl").chmod(0o755)
    return root


def _env(tmp_path: Path, root: Path, project: Path, **extra: str) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
            "CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_PROJECT_DIR": str(project),
            "BL_STUB_MARK": str(tmp_path / "native-ran"), **extra}


def _wait_for(path: Path, want: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        seen = bn.read_first_line(path)
        if seen == want:
            return seen
        time.sleep(0.05)
    return seen


def test_session_start_writes_the_tree_digest_in_the_background(plugin, tmp_path):
    current = plugin / "bin/native/sources.current"
    assert not current.exists()
    subprocess.run(["/bin/sh", "-c", registration.NATIVE], input=b"{}",
                   env=_env(tmp_path, plugin, tmp_path), capture_output=True, timeout=30)
    assert _wait_for(current, bn.compute_digest(plugin)) == bn.compute_digest(plugin)


def test_session_start_starts_no_refresh_without_a_binary(plugin, tmp_path):
    (plugin / "bin/native/bl").unlink()
    subprocess.run(["/bin/sh", "-c", registration.NATIVE], input=b"{}",
                   env=_env(tmp_path, plugin, tmp_path), capture_output=True, timeout=30)
    time.sleep(1.0)
    assert not (plugin / "bin/native/sources.current").exists()


def _edit_hook(root: Path, tmp_path: Path, project: Path) -> None:
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(root / "scripts/tool_trace.py")}})
    subprocess.run(["bash", str(root / "hooks/pre-edit-architecture.sh")], input=payload.encode(),
                   env=_env(tmp_path, root, project), capture_output=True, timeout=30)


def test_edit_to_a_mirrored_file_invalidates_at_once_then_refreshes(plugin, tmp_path):
    native = plugin / "bin/native"
    old = bn.write_current(plugin, native)
    (native / "bl.sources").write_text(old + "\n")
    target = plugin / "scripts/tool_trace.py"
    target.write_bytes(target.read_bytes() + b"# edited\n")
    _edit_hook(plugin, tmp_path, plugin)
    # The hook returns before the refresh finishes; the marker already turned the binary off.
    assert bn.read_first_line(native / "sources.current") != old
    new = bn.compute_digest(plugin)
    assert new != old
    assert _wait_for(native / "sources.current", new) == new


def test_edit_from_another_project_leaves_the_digest_alone(plugin, tmp_path):
    native = plugin / "bin/native"
    old = bn.write_current(plugin, native)
    other = tmp_path / "other-project"
    other.mkdir()
    _edit_hook(plugin, tmp_path, other)
    assert bn.read_first_line(native / "sources.current") == old


def test_write_current_yields_to_a_writer_that_started_later(plugin, monkeypatch):
    native = plugin / "bin/native"
    real = bn.compute_digest

    def overtaken(repo):
        digest = real(repo)
        (native / "sources.current").write_text("stale 999\n")
        return digest

    monkeypatch.setattr(bn, "compute_digest", overtaken)
    assert bn.write_current(plugin, native) is None
    assert bn.read_first_line(native / "sources.current") == "stale 999"


def test_write_current_without_git_writes_nothing(tmp_path):
    native = tmp_path / "native"
    assert bn.write_current(tmp_path, native) is None
    assert not (native / "sources.current").exists()


def test_digest_is_location_independent(plugin, tmp_path):
    copy = tmp_path / "elsewhere" / "plugin"
    shutil.copytree(plugin, copy)
    assert bn.compute_digest(plugin) == bn.compute_digest(copy)


def test_untracked_files_do_not_count(plugin):
    before = bn.compute_digest(plugin)
    (plugin / "crates" / "scratch.swp").write_text("junk")
    bn.write_current(plugin, plugin / "bin/native")
    assert bn.compute_digest(plugin) == before


def test_untracked_mirrored_file_is_an_error(plugin):
    subprocess.run(["git", "-C", str(plugin), "rm", "-q", "--cached", "hooks/_bounded_hook.sh"], check=True)
    with pytest.raises(bn.BuildError, match="not tracked"):
        bn.compute_digest(plugin)


# --- the launcher guard: the binary runs only on equal digests -----------

GUARDED = {
    "trace": trace.command("end"),
    "session-start-legacy": registration.legacy_command("printf legacy"),
}


@pytest.mark.parametrize("hook", sorted(GUARDED))
@pytest.mark.parametrize("current,uses_binary", [
    ("a" * 64, True),        # equal digests
    (None, False),           # missing file
    ("", False),             # empty file
    ("stale 123", False),    # an edit's marker
    ("b" * 64, False),       # another tree
])
def test_guard_uses_the_binary_only_when_digests_match(tmp_path, hook, current, uses_binary):
    root = tmp_path / "plugin"
    native = root / "bin/native"
    native.mkdir(parents=True)
    (native / "bl").write_text(STUB_BL)
    (native / "bl").chmod(0o755)
    (native / "bl.sources").write_text("a" * 64 + "\n")
    if current is not None:
        (native / "sources.current").write_text(current + "\n")
    project = tmp_path / "project"
    (project / ".build-loop").mkdir(parents=True)
    (project / ".build-loop/state.json").write_text("{}")
    cp = subprocess.run(["/bin/sh", "-c", GUARDED[hook]], input=b"{}",
                        env=_env(tmp_path, root, project), capture_output=True, timeout=30)
    if hook == "session-start-legacy":
        # The legacy command is skipped exactly when the native entry would run.
        assert (cp.stdout != b"legacy") is uses_binary
    else:
        assert (tmp_path / "native-ran").exists() is uses_binary


def test_no_tracked_digest_remains():
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "--", "crates/*.digest"],
                             capture_output=True, text=True, check=True).stdout
    assert tracked == ""
