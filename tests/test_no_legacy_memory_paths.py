"""Drift gate: ensure no documentation references the legacy
``<repo>/.build-loop/memory/`` path outside the explicit allowlist.

After PR 2 (write cutover), orchestrator prompts and skill docs should
direct callers to ``~/.build-loop/memory/projects/<slug>/`` exclusively.
The legacy path remains a read shim in ``memory_facade.py`` (removed in
PR 3) and a source path the migration script operates on, but it MUST
NOT appear in user-facing prompts that direct WRITES.

This test greps tracked text files for legacy-path references and asserts
each one is either in the allowlist or in a documented exception.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files where legacy-path references are LEGITIMATE — read shim, migration
# tool, audit probes, historical docs. Listed by repo-relative path.
ALLOWLIST = {
    # Read-shim code paths
    "scripts/_paths.py",                  # defines legacy_project_memory_dir
    "scripts/audit_memory_invocation.py", # probes both paths
    "scripts/memory_facade.py",           # reads both tiers
    # Migration tooling — operates on the legacy paths by definition
    "scripts/migrate_project_memory.py",
    # Tests — exercise the read-path tolerance and migration
    "tests/test_memory_consolidation_pr1.py",
    "tests/test_migrate_project_memory.py",
    "tests/test_no_legacy_memory_paths.py",  # this file
    # Historical plan docs — frozen snapshots, don't rewrite
    "docs/plans/2026-05-09-capture-tuning-plus-live-smoke-gate.md",
    # Setup guide — documents the transition explicitly
    "docs/memory-setup.md",
    # CHANGELOG — historical reference
    "CHANGELOG.md",
}

# Patterns that count as a "legacy reference"
LEGACY_PATTERNS = [
    re.compile(r"<repo>/\.build-loop/memory"),
    re.compile(r"<project>/\.build-loop/memory"),
    re.compile(r"<workdir>/\.build-loop/memory"),
]

# Narrative-context allowance: a line mentioning the legacy path is fine when
# it's clearly *explaining* the transition (uses words like "legacy",
# "transition", "deprecated", "removed in PR 3") rather than directing
# callers to read/write that path.
NARRATIVE_CONTEXT = re.compile(
    r"\b(legacy|transition|deprecated|read-shimmed?|removed in PR 3|"
    r"PR 1/2 transition|pre-migration|before the memory-consolidation)\b",
    re.IGNORECASE,
)


def _git_ls_files() -> list[str]:
    """Return tracked text files (md/py/yaml/json/sh)."""
    result = subprocess.run(
        ["git", "ls-files",
         "*.md", "*.py", "*.yaml", "*.yml", "*.json", "*.sh", "*.txt"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_no_legacy_memory_path_references_outside_allowlist():
    """Assert no tracked text file references the legacy memory path
    outside the explicit allowlist."""
    offenders: list[tuple[str, int, str]] = []
    for relpath in _git_ls_files():
        if relpath in ALLOWLIST:
            continue
        try:
            text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pat in LEGACY_PATTERNS:
                if pat.search(line):
                    # Allow narrative-context lines (those explaining the
                    # transition rather than directing callers to use the
                    # legacy path).
                    if NARRATIVE_CONTEXT.search(line):
                        break
                    offenders.append((relpath, lineno, line.strip()[:160]))
                    break
    assert not offenders, (
        "Files with legacy memory path references outside allowlist:\n"
        + "\n".join(f"  {p}:{n}: {s}" for p, n, s in offenders)
        + "\n\nEither update to point at `~/.build-loop/memory/projects/<slug>/`, "
          "or add the file to ALLOWLIST in tests/test_no_legacy_memory_paths.py."
    )


def test_orchestrator_phase1_uses_new_memory_path():
    """The orchestrator's Phase 1 memory load must NOT point at the legacy path."""
    orch = REPO_ROOT / "agents" / "build-orchestrator.md"
    text = orch.read_text(encoding="utf-8")
    # Phase 1 memory load section — look for the new path
    assert "~/.build-loop/memory/projects/" in text or "build_loop_memory_root" in text, (
        "agents/build-orchestrator.md must reference the consolidated "
        "project memory path post-PR-2"
    )


if __name__ == "__main__":
    test_no_legacy_memory_path_references_outside_allowlist()
    test_orchestrator_phase1_uses_new_memory_path()
    print("ok")
