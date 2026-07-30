"""Tests for the PreToolUse architecture hook extension allowlist.

The hook (`hooks/pre-edit-architecture.sh`) must NOT mark architecture stale
or fire a scan for doc-only edits (`.md`, `.txt`, `.json`, etc.). Source-code
edits in the allowlist (`.py .ts .tsx .js .jsx .mjs .cjs .rs`) still mark stale.

This locks Priority 4 of the architecture-awareness follow-up.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "hooks" / "pre-edit-architecture.sh"


def _seed_workspace(tmp_path: Path) -> Path:
    """Set up a minimal workspace mirroring what the hook expects to find."""
    arch = tmp_path / ".build-loop" / "architecture"
    arch.mkdir(parents=True, exist_ok=True)
    # file_map.json with one tracked .py and one tracked .md (the .md should
    # still be ignored because of the extension allowlist gate).
    (arch / "file_map.json").write_text(
        json.dumps({
            "schema_version": "1.0.0",
            "files": {
                "src/foo.py": "abc123",
                "src/lib.rs": "cafef00d",
                "README.md": "deadbeef",
            },
        }),
        encoding="utf-8",
    )
    # state.json with explicit stale=false.
    state = tmp_path / ".build-loop" / "state.json"
    state.write_text(
        json.dumps({
            "schema_version": "1.0.0",
            "active": True,
            "phase": "execute",
            "architecture": {"stale": False, "staleFiles": []},
        }),
        encoding="utf-8",
    )
    # Copy hook + workers + freshness script into the workspace so the hook's
    # ${BASH_SOURCE[0]} relative resolution finds them.
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    shutil.copy(HOOK, hooks_dir / "pre-edit-architecture.sh")
    shutil.copy(REPO / "hooks" / "_arch_scan_bg.py", hooks_dir / "_arch_scan_bg.py")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    shutil.copy(
        REPO / "scripts" / "architecture_freshness.py",
        scripts_dir / "architecture_freshness.py",
    )
    os.chmod(hooks_dir / "pre-edit-architecture.sh", 0o755)
    return hooks_dir / "pre-edit-architecture.sh"


def _run_hook(hook: Path, workdir: Path, file_path: str) -> int:
    """Invoke the hook with the given tool_input.file_path on stdin."""
    payload = json.dumps({"tool_input": {"file_path": file_path}})
    proc = subprocess.run(
        ["bash", str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(workdir)},
        timeout=10,
    )
    return proc.returncode


def _is_stale(workdir: Path) -> bool:
    state_path = workdir / ".build-loop" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return bool(state.get("architecture", {}).get("stale"))


def test_md_edit_does_not_mark_stale(tmp_path: Path) -> None:
    """A .md edit must not flip architecture.stale to True."""
    hook = _seed_workspace(tmp_path)
    rc = _run_hook(hook, tmp_path, "README.md")
    assert rc == 0
    assert _is_stale(tmp_path) is False, "stale must remain false after .md edit"


def test_txt_edit_does_not_mark_stale(tmp_path: Path) -> None:
    """A .txt edit must not flip architecture.stale to True."""
    hook = _seed_workspace(tmp_path)
    rc = _run_hook(hook, tmp_path, "notes.txt")
    assert rc == 0
    assert _is_stale(tmp_path) is False


def test_json_edit_does_not_mark_stale(tmp_path: Path) -> None:
    """JSON config edits don't fire the scan."""
    hook = _seed_workspace(tmp_path)
    rc = _run_hook(hook, tmp_path, "config.json")
    assert rc == 0
    assert _is_stale(tmp_path) is False


def test_py_edit_marks_stale(tmp_path: Path) -> None:
    """A tracked .py edit must mark stale (regression check)."""
    hook = _seed_workspace(tmp_path)
    rc = _run_hook(hook, tmp_path, "src/foo.py")
    assert rc == 0
    assert _is_stale(tmp_path) is True, "stale must flip to true after .py edit"


def test_rs_edit_marks_stale(tmp_path: Path) -> None:
    """A tracked .rs edit must mark stale — Rust source edits previously
    never marked architecture stale (2026-07-03 harness assessment gap)."""
    hook = _seed_workspace(tmp_path)
    rc = _run_hook(hook, tmp_path, "src/lib.rs")
    assert rc == 0
    assert _is_stale(tmp_path) is True, "stale must flip to true after .rs edit"


def test_untracked_py_does_not_mark_stale(tmp_path: Path) -> None:
    """An allowlisted extension that's NOT in file_map still bails (the
    file_map gate is downstream of the extension gate)."""
    hook = _seed_workspace(tmp_path)
    rc = _run_hook(hook, tmp_path, "src/not_tracked.py")
    assert rc == 0
    assert _is_stale(tmp_path) is False
