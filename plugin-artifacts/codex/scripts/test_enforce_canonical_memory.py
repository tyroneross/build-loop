#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Colocated tests for enforce_canonical_memory.py (C-MEMORY/canonical_writer).

Covers the enforcement contract end to end:
  - lane detection (positive + the sidecar/non-lane negatives)
  - provenance presence using the real memory_writer schema
  - the --staged git gate FIRING (exit 1) on a direct write
  - the --staged git gate PASSING (exit 0) on a writer-stamped entry and on a
    normal source edit
  - fail-soft outside a git repo

Run: uv run python -m pytest scripts/test_enforce_canonical_memory.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import enforce_canonical_memory as ecm  # noqa: E402
import memory_writer as mw  # noqa: E402

SCRIPT = HERE / "enforce_canonical_memory.py"


# --- lane detection -------------------------------------------------------

def test_lane_detection_positive():
    assert ecm.is_memory_lane_entry("build-loop-memory/lessons/x.md")
    assert ecm.is_memory_lane_entry("build-loop-memory/projects/foo/decisions/0001-y.md")
    assert ecm.is_memory_lane_entry("a/b/build-loop-memory/design/z.md")


def test_lane_detection_negatives():
    # Not a lane file:
    assert not ecm.is_memory_lane_entry("src/app.py")
    assert not ecm.is_memory_lane_entry("docs/build-loop-memory-notes.md")
    # Wrong subfolder under the store:
    assert not ecm.is_memory_lane_entry("build-loop-memory/indexes/recall.md")
    # Writer-managed sidecars in a lane are exempt:
    assert not ecm.is_memory_lane_entry("build-loop-memory/lessons/INDEX.jsonl")
    assert not ecm.is_memory_lane_entry("build-loop-memory/lessons/MEMORY.md")
    assert not ecm.is_memory_lane_entry("build-loop-memory/projects/foo/milestones.jsonl")


# --- provenance presence (real schema) ------------------------------------

def _provenance_block() -> str:
    fields = "\n".join(f"{k}: x" for k in sorted(mw.REQUIRED_PROVENANCE_FIELDS))
    return f"---\nname: t\ndescription: d\ntype: lesson\n{fields}\n---\nbody\n"


def test_has_provenance_true(tmp_path):
    f = tmp_path / "good.md"
    f.write_text(_provenance_block())
    assert ecm.has_provenance(f)


def test_has_provenance_false(tmp_path):
    f = tmp_path / "bad.md"
    f.write_text("---\nname: t\ndescription: d\n---\nbody\n")
    assert not ecm.has_provenance(f)


# --- git --staged gate (the actual enforcement) ---------------------------

def _git(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(tmp), *args],
        capture_output=True, text=True, check=True,
        env={
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": os.environ.get("PATH", ""),
        },
    )


def _run_gate(workdir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--staged",
         "--workdir", str(workdir), "--json"],
        capture_output=True, text=True,
    )


def test_gate_fires_on_direct_memory_write(tmp_path):
    """FAILING case: a raw write into a memory lane is staged → exit 1."""
    _git(tmp_path, "init", "-q")
    bad = tmp_path / "build-loop-memory" / "lessons" / "sneaky.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("---\nname: sneaky\n---\nbypassed the canonical writer\n")
    _git(tmp_path, "add", "-A")

    r = _run_gate(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    rep = json.loads(r.stdout)
    assert rep["rule"] == "C-MEMORY/canonical_writer"
    assert rep["count"] == 1
    assert rep["violations"][0]["path"].endswith("build-loop-memory/lessons/sneaky.md")


def test_gate_passes_on_writer_stamped_entry(tmp_path):
    """PASSING case: a provenance-stamped lane entry staged → exit 0."""
    _git(tmp_path, "init", "-q")
    good = tmp_path / "build-loop-memory" / "lessons" / "proper.md"
    good.parent.mkdir(parents=True)
    good.write_text(_provenance_block())
    _git(tmp_path, "add", "-A")

    r = _run_gate(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["count"] == 0


def test_gate_passes_on_normal_source_edit(tmp_path):
    """PASSING case: an ordinary source edit staged → exit 0."""
    _git(tmp_path, "init", "-q")
    src = tmp_path / "src" / "app.py"
    src.parent.mkdir(parents=True)
    src.write_text("print('hello')\n")
    _git(tmp_path, "add", "-A")

    r = _run_gate(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["count"] == 0


def test_gate_failsoft_outside_git(tmp_path):
    """Non-git dir → no crash, reports clean (fail-soft)."""
    assert ecm.git_staged_paths(tmp_path) == []
    r = _run_gate(tmp_path)
    assert r.returncode == 0
    assert json.loads(r.stdout)["count"] == 0


def test_no_strict_always_exits_zero(tmp_path):
    """--no-strict downgrades the gate to advisory (exit 0 even with a violation)."""
    bad = tmp_path / "build-loop-memory" / "lessons" / "x.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("no frontmatter here\n")
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--paths", str(bad),
         "--workdir", str(tmp_path), "--no-strict", "--json"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert json.loads(r.stdout)["count"] == 1
