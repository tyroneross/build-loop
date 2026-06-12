# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for append_run.py — canonical, atomic, Learn-visible run records."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "append_run.py"


def _run(workdir, *args, expect_ok=True):
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), "--json", *args],
        capture_output=True, text=True,
    )
    if expect_ok:
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout)
    return res


def _state(workdir):
    return json.loads((workdir / ".build-loop" / "state.json").read_text())


def test_canonical_shape(tmp_path):
    out = _run(tmp_path, "--run-id", "r1", "--goal", "ship X", "--outcome", "done")
    assert out["action"] == "appended" and out["runs_count"] == 1
    rec = _state(tmp_path)["runs"][0]
    assert rec["outcome"] == "pass"           # done -> canonical pass
    assert isinstance(rec["phases"], dict)     # phases is a DICT, not a list (f3)
    for k in ("run_id", "date", "goal", "outcome", "filesTouched", "manualInterventions"):
        assert k in rec
    # validators.validate_entry must accept it (canonical contract)
    sys.path.insert(0, str(SCRIPT.parent))
    from write_run_entry.validators import validate_entry
    validate_entry(rec)


def test_outcome_mapping(tmp_path):
    _run(tmp_path, "--run-id", "r1", "--outcome", "blocked")
    assert _state(tmp_path)["runs"][0]["outcome"] == "fail"  # blocked -> fail


def test_phases_dict_and_manual_intervention(tmp_path):
    _run(tmp_path, "--run-id", "r3", "--outcome", "done",
         "--manual-intervention", "6:prompted for fable", "--phase", "4:fail", "--phase", "2:pass")
    rec = _state(tmp_path)["runs"][0]
    assert rec["phases"] == {"4": {"status": "fail"}, "2": {"status": "pass"}}
    assert rec["manualInterventions"] == [{"phase": "6", "note": "prompted for fable"}]


def test_idempotent_on_run_id(tmp_path):
    _run(tmp_path, "--run-id", "r1", "--goal", "v1", "--outcome", "done")
    out = _run(tmp_path, "--run-id", "r1", "--goal", "v2", "--outcome", "partial")
    assert out["action"] == "replaced" and out["runs_count"] == 1
    runs = _state(tmp_path)["runs"]
    assert len(runs) == 1 and runs[0]["goal"] == "v2" and runs[0]["outcome"] == "partial"


def test_preserves_other_state_keys(tmp_path):
    sp = tmp_path / ".build-loop" / "state.json"
    sp.parent.mkdir(parents=True)
    sp.write_text(json.dumps({"phase": "report", "execution": {"x": 1}, "runs": [{"run_id": "old", "source": "append_run"}]}))
    _run(tmp_path, "--run-id", "r2", "--outcome", "done")
    st = _state(tmp_path)
    assert st["phase"] == "report" and st["execution"] == {"x": 1}
    assert [r["run_id"] for r in st["runs"]] == ["old", "r2"]


def test_refuses_to_clobber_unparseable_state(tmp_path):  # f5
    sp = tmp_path / ".build-loop" / "state.json"
    sp.parent.mkdir(parents=True)
    sp.write_text("{ this is : not json,,, ")
    before = sp.read_bytes()
    res = _run(tmp_path, "--run-id", "r1", "--outcome", "done", expect_ok=False)
    assert res.returncode != 0 and "refusing to overwrite" in res.stderr
    assert sp.read_bytes() == before  # untouched


def test_refuses_to_replace_richer_orchestrator_record(tmp_path):  # f9
    sp = tmp_path / ".build-loop" / "state.json"
    sp.parent.mkdir(parents=True)
    sp.write_text(json.dumps({"runs": [{"run_id": "r1", "judge_decisions": [{"x": 1}]}]}))  # no source=append_run
    res = _run(tmp_path, "--run-id", "r1", "--outcome", "done", expect_ok=False)
    assert res.returncode != 0 and "refusing to overwrite a richer record" in res.stderr


def test_extra_json_cannot_override_identity(tmp_path):  # f9
    _run(tmp_path, "--run-id", "r1", "--outcome", "done",
         "--extra-json", json.dumps({"run_id": "HACK", "source": "spoof", "security_findings": [{"mapped_risk": "LLM01"}]}))
    rec = _state(tmp_path)["runs"][0]
    assert rec["run_id"] == "r1" and rec["source"] == "append_run"   # identity preserved
    assert rec["security_findings"][0]["mapped_risk"] == "LLM01"     # non-identity extra applied
