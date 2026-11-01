"""T14 — pre-edit hook unblocks dependency manifests (OQ3).

Design: the pre-edit allowlist used to exclude manifests (.json/.txt bailed
before any I/O). Stage 2 routes a manifest edit to *mark enrich-needed*
(write ``.build-loop/architecture/.enrich-needed``) and EXIT — it does NOT
run enrichment inline (OQ3: actual enrich deferred to the scout pass). The
source-code stale/scan path is unchanged; doc-only edits still bail.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.architecture.hook_async import stub_uv_env, wait_for, wait_idle

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "hooks" / "pre-edit-architecture.sh"


def _seed(tmp_path: Path) -> Path:
    arch = tmp_path / ".build-loop" / "architecture"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "file_map.json").write_text(
        json.dumps({"schema_version": "1.0.0",
                    "files": {"src/foo.py": "abc", "README.md": "dead"}}),
        encoding="utf-8",
    )
    (tmp_path / ".build-loop" / "state.json").write_text(
        json.dumps({"schema_version": "1.0.0", "active": True,
                    "phase": "execute",
                    "architecture": {"stale": False, "staleFiles": []}}),
        encoding="utf-8",
    )
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    shutil.copy(HOOK, hooks_dir / "pre-edit-architecture.sh")
    shutil.copy(REPO / "hooks" / "_arch_scan_bg.py", hooks_dir / "_arch_scan_bg.py")
    shutil.copy(REPO / "hooks" / "_arch_pre_edit_bg.py", hooks_dir / "_arch_pre_edit_bg.py")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    shutil.copy(REPO / "scripts" / "architecture_freshness.py",
                scripts_dir / "architecture_freshness.py")
    # The hook reads the edit payload through scripts/hook_payload.py.
    shutil.copy(REPO / "scripts" / "hook_payload.py", scripts_dir / "hook_payload.py")
    os.chmod(hooks_dir / "pre-edit-architecture.sh", 0o755)
    return hooks_dir / "pre-edit-architecture.sh"


def _run(hook: Path, workdir: Path, file_path: str) -> int:
    payload = json.dumps({"tool_input": {"file_path": file_path}})
    proc = subprocess.run(
        ["bash", str(hook)], input=payload, capture_output=True, text=True,
        env=stub_uv_env(workdir), timeout=10,
    )
    return proc.returncode


def _marker(workdir: Path) -> Path:
    return workdir / ".build-loop" / "architecture" / ".enrich-needed"


def _is_stale(workdir: Path) -> bool:
    s = json.loads((workdir / ".build-loop" / "state.json").read_text())
    return bool(s.get("architecture", {}).get("stale"))


@pytest.mark.parametrize("manifest", [
    "package.json", "package-lock.json", "pnpm-lock.yaml", "requirements.txt",
    "pyproject.toml", "uv.lock", "Cargo.toml", "go.mod", "Gemfile",
])
def test_manifest_edit_marks_enrich_needed(tmp_path: Path, manifest: str):
    hook = _seed(tmp_path)
    rc = _run(hook, tmp_path, manifest)
    assert rc == 0
    assert wait_for(_marker(tmp_path).exists), f"{manifest} must mark enrich-needed"
    wait_idle(tmp_path)


def test_manifest_edit_does_not_run_enrich_inline(tmp_path: Path):
    # OQ3: the hook marks, it must NOT spawn the bg scan worker for a manifest.
    hook = _seed(tmp_path)
    _run(hook, tmp_path, "package.json")
    assert wait_for(_marker(tmp_path).exists)
    wait_idle(tmp_path)
    # The bg worker writes/scans .scan.lock; a manifest path must not trigger it.
    assert not (tmp_path / ".build-loop" / "architecture" / ".scan.lock").exists()
    # Manifest edit does not flip the source-staleness flag either.
    assert _is_stale(tmp_path) is False


def test_source_edit_path_unchanged(tmp_path: Path):
    # Regression: a tracked .py edit still marks stale, no enrich marker.
    hook = _seed(tmp_path)
    rc = _run(hook, tmp_path, "src/foo.py")
    assert rc == 0
    assert wait_for(lambda: _is_stale(tmp_path))
    wait_idle(tmp_path)
    assert not _marker(tmp_path).exists()


def test_doc_edit_still_bails(tmp_path: Path):
    # A plain .md edit: no stale, no enrich marker.
    hook = _seed(tmp_path)
    rc = _run(hook, tmp_path, "README.md")
    assert rc == 0
    wait_idle(tmp_path)
    assert _is_stale(tmp_path) is False
    assert not _marker(tmp_path).exists()


def test_plugin_hook_works_in_a_project_without_scripts(tmp_path: Path) -> None:
    """The installed hook runs from the plugin against a user project that has
    no scripts/ dir of its own; it must still mark manifests and stale files."""
    arch = tmp_path / ".build-loop" / "architecture"
    arch.mkdir(parents=True)
    (arch / "file_map.json").write_text(
        json.dumps({"schema_version": "1.0.0", "files": {"src/foo.py": "abc"}}), encoding="utf-8")
    (tmp_path / ".build-loop" / "state.json").write_text(
        json.dumps({"schema_version": "1.0.0", "active": True, "phase": "execute",
                    "architecture": {"stale": False, "staleFiles": []}}), encoding="utf-8")
    assert not (tmp_path / "scripts").exists()

    assert _run(HOOK, tmp_path, "go.mod") == 0
    # The hook returns at once; its detached runner does the marking.
    assert wait_for(lambda: _marker(tmp_path).exists()), \
        "manifest edit must mark enrich-needed from the plugin path"
    wait_idle(tmp_path)

    assert _run(HOOK, tmp_path, "src/foo.py") == 0
    # Read the mark while the scan runs; a finished scan marks the map fresh.
    state_path = tmp_path / ".build-loop" / "state.json"
    def marked() -> bool:
        arch = json.loads(state_path.read_text(encoding="utf-8")).get("architecture", {})
        return arch.get("stale") is True and "src/foo.py" in arch.get("staleFiles", [])
    assert wait_for(marked)
    wait_idle(tmp_path)


def test_runner_classifies_every_path_of_a_multi_file_edit(tmp_path: Path) -> None:
    """apply_patch and edits[] name several files; each is classified."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("arch_pre_edit_bg", REPO / "hooks" / "_arch_pre_edit_bg.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    file_map = tmp_path / "file_map.json"
    file_map.write_text(json.dumps({"files": {"src/a.py": "1", "src/b.py": "2"}}), encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: src/a.py\n*** Update File: go.mod\n*** Add File: src/b.py\n*** Update File: README.md\n*** End Patch\n"
    payload = json.dumps({"tool_name": "apply_patch", "tool_input": {"command": patch}})
    assert runner.classify(payload, tmp_path, file_map) == ["src/a.py", "MANIFEST", "src/b.py"]
    assert runner.classify("not json", tmp_path, file_map) == []
