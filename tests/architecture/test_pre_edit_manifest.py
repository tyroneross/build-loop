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
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    shutil.copy(REPO / "scripts" / "architecture_freshness.py",
                scripts_dir / "architecture_freshness.py")
    os.chmod(hooks_dir / "pre-edit-architecture.sh", 0o755)
    return hooks_dir / "pre-edit-architecture.sh"


def _run(hook: Path, workdir: Path, file_path: str) -> int:
    payload = json.dumps({"tool_input": {"file_path": file_path}})
    proc = subprocess.run(
        ["bash", str(hook)], input=payload, capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(workdir)}, timeout=10,
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
    assert _marker(tmp_path).exists(), f"{manifest} must mark enrich-needed"


def test_manifest_edit_does_not_run_enrich_inline(tmp_path: Path):
    # OQ3: the hook marks, it must NOT spawn the bg scan worker for a manifest.
    hook = _seed(tmp_path)
    _run(hook, tmp_path, "package.json")
    # The bg worker writes/scans .scan.lock; a manifest path must not trigger it.
    assert not (tmp_path / ".build-loop" / "architecture" / ".scan.lock").exists()
    # Manifest edit does not flip the source-staleness flag either.
    assert _is_stale(tmp_path) is False


def test_source_edit_path_unchanged(tmp_path: Path):
    # Regression: a tracked .py edit still marks stale, no enrich marker.
    hook = _seed(tmp_path)
    rc = _run(hook, tmp_path, "src/foo.py")
    assert rc == 0
    assert _is_stale(tmp_path) is True
    assert not _marker(tmp_path).exists()


def test_doc_edit_still_bails(tmp_path: Path):
    # A plain .md edit: no stale, no enrich marker.
    hook = _seed(tmp_path)
    rc = _run(hook, tmp_path, "README.md")
    assert rc == 0
    assert _is_stale(tmp_path) is False
    assert not _marker(tmp_path).exists()
