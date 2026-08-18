# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the portability gate.

Both directions matter. A gate only proven on clean input certifies nothing —
these assert it FIRES on each defect shape and stays SILENT on the legitimate
lookalikes that would otherwise make it too noisy to leave armed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent / "check_portability.py"


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "agents").mkdir(parents=True)
    (r / "skills").mkdir(parents=True)
    (r / "scripts").mkdir(parents=True)
    (r / "docs").mkdir(parents=True)
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
    return r


def _scan(repo: Path, rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), rel],
        cwd=repo, capture_output=True, text=True,
    )


def _write(repo: Path, rel: str, body: str) -> str:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return rel


# --- must FIRE ------------------------------------------------------------
@pytest.mark.parametrize("body", [
    'Read("~/dev/git-folder/build-loop-memory/constitution.md")',
    'BL=$HOME/dev/git-folder/build-loop',
    'cd ~/Desktop/git-folder/build-loop',
    'see ~/dev/research/topics/foo.md',
    'sessions = "~/.claude/projects/-Users-someone/memory"',
    'DSN = "postgresql://realname@localhost:5432/agent_memory"',
])
def test_fires_on_defect_shapes(tmp_path, body):
    repo = _repo(tmp_path)
    rel = _write(repo, "agents/a.md", body)
    r = _scan(repo, rel)
    assert r.returncode == 1, f"gate did NOT fire on: {body}\n{r.stdout}{r.stderr}"


def test_fires_on_python_source(tmp_path):
    repo = _repo(tmp_path)
    rel = _write(repo, "scripts/thing.py", 'ROOT = "~/dev/git-folder/build-loop-memory"\n')
    assert _scan(repo, rel).returncode == 1


# --- must stay SILENT -----------------------------------------------------
@pytest.mark.parametrize("body", [
    # the README/memory-setup case: accurately documenting the real order
    'defaults to `~/.build-loop-memory`, or an existing legacy root when present',
    # generic placeholders the docs deliberately show
    'BL=/Users/you/dev/git-folder/build-loop  # set to your install path',
    'set `<path-to-your-marketplace-hub>/.claude-plugin/marketplace.json`',
    'clone into <your-local-checkout>',
    'glob ~/.claude/projects/*/memory/',
    # SPDX attribution is intentional, not leakage
    '# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <x@users.noreply.github.com>',
    # env-driven is the fix, not the defect
    'GW="$GROUNDWORK_ROOT"',
    'postgresql://$USER@localhost:5432/agent_memory',
])
def test_silent_on_legitimate(tmp_path, body):
    repo = _repo(tmp_path)
    rel = _write(repo, "skills/s/SKILL.md", body)
    r = _scan(repo, rel)
    assert r.returncode == 0, f"false positive on: {body}\n{r.stdout}{r.stderr}"


def test_docs_are_not_a_shipped_surface(tmp_path):
    """docs/ is read by humans, not followed by an installed agent."""
    repo = _repo(tmp_path)
    rel = _write(repo, "docs/notes.md", "cd ~/dev/git-folder/build-loop")
    assert _scan(repo, rel).returncode == 0


def test_tests_are_not_graded(tmp_path):
    """Test files must be able to name the literal they guard against."""
    repo = _repo(tmp_path)
    rel = _write(repo, "scripts/test_x.py", 'assert "~/dev/git-folder/x" not in src')
    assert _scan(repo, rel).returncode == 0


def test_live_tree_is_clean():
    """The real repo must pass, or the gate cannot be armed."""
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(GATE), "--all"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, f"live tree has portability hits:\n{r.stderr}"
