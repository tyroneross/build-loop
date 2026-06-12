# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for append_run.py — Learn-visible run records into state.json.runs[]."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "append_run.py"


def _run(workdir, *args):
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--json", *args],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _state(workdir):
    return json.loads((workdir / ".build-loop" / "state.json").read_text())


def test_append_creates_state_and_grows_runs(tmp_path):
    out = _run(tmp_path, "--run-id", "r1", "--goal", "ship X", "--outcome", "done")
    assert out["action"] == "appended" and out["runs_count"] == 1
    rec = _state(tmp_path)["runs"][0]
    # canonical fields the detector scans must exist
    for k in ("run_id", "date", "goal", "outcome", "phases", "manualInterventions", "security_findings"):
        assert k in rec, f"missing {k}"
    assert rec["run_id"] == "r1" and rec["outcome"] == "done" and rec["source"] == "append_run"


def test_idempotent_on_run_id(tmp_path):
    _run(tmp_path, "--run-id", "r1", "--goal", "v1", "--outcome", "done")
    out = _run(tmp_path, "--run-id", "r1", "--goal", "v2", "--outcome", "partial")
    assert out["action"] == "replaced" and out["runs_count"] == 1  # no duplicate
    runs = _state(tmp_path)["runs"]
    assert len(runs) == 1 and runs[0]["goal"] == "v2" and runs[0]["outcome"] == "partial"


def test_preserves_other_state_keys(tmp_path):
    sp = tmp_path / ".build-loop" / "state.json"
    sp.parent.mkdir(parents=True)
    sp.write_text(json.dumps({"phase": "report", "execution": {"x": 1}, "runs": [{"run_id": "old"}]}))
    _run(tmp_path, "--run-id", "r2", "--outcome", "done")
    st = _state(tmp_path)
    assert st["phase"] == "report" and st["execution"] == {"x": 1}
    assert [r["run_id"] for r in st["runs"]] == ["old", "r2"]


def test_manual_intervention_and_phase_parsing(tmp_path):
    _run(tmp_path, "--run-id", "r3", "--outcome", "done",
         "--manual-intervention", "6:user prompted for fable",
         "--phase", "4:fail", "--phase", "2:pass")
    rec = _state(tmp_path)["runs"][0]
    assert rec["manualInterventions"] == [{"phase": "6", "note": "user prompted for fable"}]
    assert {"phase": "4", "status": "fail"} in rec["phases"]
    assert {"phase": "2", "status": "pass"} in rec["phases"]


def test_extra_json_merge(tmp_path):
    _run(tmp_path, "--run-id", "r4", "--outcome", "done",
         "--extra-json", json.dumps({"security_findings": [{"mapped_risk": "LLM01", "severity": "high"}]}))
    rec = _state(tmp_path)["runs"][0]
    assert rec["security_findings"][0]["mapped_risk"] == "LLM01"


def test_files_touched_split(tmp_path):
    _run(tmp_path, "--run-id", "r5", "--outcome", "done", "--files-touched", "a.py, b.sh ,c.md")
    assert _state(tmp_path)["runs"][0]["filesTouched"] == ["a.py", "b.sh", "c.md"]
