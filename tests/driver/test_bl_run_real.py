# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""`bl run` against the installed Claude adapter, in a throwaway project.

Opt-in (`BL_REAL_AGENT=1`): it uses the machine's existing Claude login and
spends quota, so it never runs by default. The measurements live in
`measure_slice1.py`; this holds the two facts the slice rests on: a turn
completes on the subscription login, and with the `quick` profile a write
inside the project asks first while a recursive delete is refused and does
not happen.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BL = Path(os.environ.get("BL_BIN") or REPO / "target" / "release" / "bl")

pytestmark = pytest.mark.skipif(
    os.environ.get("BL_REAL_AGENT") != "1" or not BL.exists() or shutil.which("claude-agent-acp") is None,
    reason="real-agent test: set BL_REAL_AGENT=1, build bl, and have claude-agent-acp installed",
)


def _run(project: Path, task: str, tmp: Path) -> tuple[subprocess.CompletedProcess, dict]:
    summary = tmp / "summary.json"
    cp = subprocess.run([str(BL), "run", "--profile", "quick", "--model", "haiku", "--summary", str(summary), "--cwd", str(project), task], capture_output=True, text=True, env=dict(os.environ, BL_ROOT=str(REPO)), timeout=300)
    return cp, json.loads(summary.read_text()) if summary.exists() else {}


def test_a_turn_completes_on_the_existing_login(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    cp, summary = _run(project, "Reply with the single word ready.", tmp_path)
    assert cp.returncode == 0, cp.stderr[-500:]
    assert "ready" in cp.stdout.lower()
    assert summary["agent_model"] == "haiku" and summary["mode"] == "default"


def test_a_write_asks_first_and_a_recursive_delete_is_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "planted_dir").mkdir(parents=True)
    (project / "planted_dir/x.txt").write_text("x\n")
    one = "Use exactly one tool call, then report in one short sentence. Do not try another way if it is refused. "

    cp, summary = _run(project, one + "Create the file new.txt containing the word one, with the Write tool.", tmp_path)
    assert cp.returncode == 0, cp.stderr[-500:]
    assert (project / "new.txt").exists()
    assert [(t["requested"], t["ran_unasked"], t["decision"]) for t in summary["tools"]] == [(True, False, "allow")]

    cp, summary = _run(project, one + "Run `rm -rf planted_dir` with the Bash tool.", tmp_path)
    assert cp.returncode == 0, cp.stderr[-500:]
    assert (project / "planted_dir/x.txt").exists()
    assert [(t["requested"], t["decision"], t["rule"]) for t in summary["tools"]] == [(True, "refuse", "recursive-delete")]
