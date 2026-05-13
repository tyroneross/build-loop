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

# Files where legacy-path references are LEGITIMATE.
# Annotated by whether PR 3 (read-shim removal) will require updates.
ALLOWLIST = {
    # Migration tooling — operates on the legacy paths by definition
    "scripts/migrate_project_memory.py",
    # Tests — exercise the read-path tolerance and migration historically
    "tests/test_memory_consolidation_pr1.py",
    "tests/test_migrate_project_memory.py",
    "tests/test_no_legacy_memory_paths.py",  # this file
    # Historical plan docs — frozen snapshots, don't rewrite
    "docs/plans/2026-05-09-capture-tuning-plus-live-smoke-gate.md",
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
# "transition", "deprecated", "removed in PR 3") AND the line is NOT
# directing a write or read against the legacy path. The directive_form
# regex below catches imperative constructions; if either of those fires,
# the narrative carve-out is denied even when transition keywords appear.
NARRATIVE_CONTEXT = re.compile(
    r"\b(legacy|transition|deprecated|read-shimmed?|removed in PR 3|"
    r"PR 1/2 transition|pre-migration|before the memory-consolidation)\b",
    re.IGNORECASE,
)

# If a line uses any of these imperative forms in the same line as the
# legacy path, the narrative carve-out does NOT apply. The line is still
# directing a read/write against the legacy path even if it also
# mentions the transition.
DIRECTIVE_FORM = re.compile(
    r"\b(Read\(|Write\(|write[s]? to|writes go to|points at|read from|"
    r"load[s]? from|append to|create[s]? in|stored in|located at)\b",
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
                    # legacy path) — UNLESS the line also uses an
                    # imperative directive form that would direct a read
                    # or write against the legacy path.
                    if NARRATIVE_CONTEXT.search(line) and not DIRECTIVE_FORM.search(line):
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
