# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for judgment_gate.py — current-run-scoped Frontier-dispatch gate."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "judgment_gate.py"


def _write(tmp, state, ledger_rows=None):
    bl = tmp / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "state.json").write_text(json.dumps(state))
    if ledger_rows is not None:
        (bl / "agent-ledger.jsonl").write_text("\n".join(json.dumps(r) for r in ledger_rows))


def _run(tmp, *args, agent_tool="true"):
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(tmp), "--agent-tool-available", agent_tool, "--json", *args],
        capture_output=True, text=True,
    )
    return res.returncode, json.loads(res.stdout)


def _runs(*records):
    return {"runs": list(records)}


def test_no_stakes_passes(tmp_path):
    _write(tmp_path, _runs({"run_id": "r1", "synthesisDensity": 2, "auditor_status": ""}))
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass" and out["stakes_gated"] is False


def test_stakes_with_dispatched_auditor_passes(tmp_path):
    _write(tmp_path, _runs({"run_id": "r1", "riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"}))
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass"


def test_stakes_inline_floor_fails(tmp_path):
    _write(tmp_path, _runs({"run_id": "r1", "riskSurfaceChange": True, "auditor_status": "fallback:inline-opus"}))
    rc, out = _run(tmp_path, agent_tool="true")
    assert rc == 1 and out["verdict"] == "fail"
    assert any(f["layer"] == "independent-auditor" for f in out["findings"])


def test_nested_no_agent_tool_warns(tmp_path):
    _write(tmp_path, _runs({"run_id": "r1", "synthesisDensity": 9, "auditor_status": "not-run:parent-must-dispatch"}))
    rc, out = _run(tmp_path, agent_tool="false")
    assert rc == 0 and out["verdict"] == "warn"


def test_advisor_floor_fails_and_inline_frontier_ok(tmp_path):
    _write(tmp_path, _runs({"run_id": "r1", "riskSurfaceChange": True,
                            "auditor_status": "ran:dispatched-agent", "advisor_status": "fallback:inline-opus"}))
    rc, out = _run(tmp_path)
    assert rc == 1 and any(f["layer"] == "advisor" for f in out["findings"])

    _write(tmp_path, _runs({"run_id": "r1", "stakes": "medium",
                            "auditor_status": "ran:peer-host", "advisor_status": "inline-frontier"}))
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass"


# --- f1: stale top-level triggers must NOT latch the current run ---
def test_stale_top_level_triggers_do_not_latch(tmp_path):
    # Old assessment left triggers.riskSurfaceChange at top level; the CURRENT run
    # record has no stakes → must PASS (the latch bug).
    _write(tmp_path, {"triggers": {"riskSurfaceChange": True},
                      "riskSurfaceChange": True,
                      "runs": [{"run_id": "new", "auditor_status": "fallback:inline-opus"}]})
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass" and out["stakes_gated"] is False


def test_run_id_selects_the_right_run(tmp_path):
    _write(tmp_path, _runs(
        {"run_id": "old", "riskSurfaceChange": True, "auditor_status": "fallback:inline-opus"},
        {"run_id": "new", "synthesisDensity": 1, "auditor_status": ""},
    ))
    rc, out = _run(tmp_path, "--run-id", "new")
    assert rc == 0 and out["verdict"] == "pass"           # 'new' is not stakes-gated
    rc, out = _run(tmp_path, "--run-id", "old")
    assert rc == 1 and out["verdict"] == "fail"            # 'old' is


# --- f2: ledger must be scoped to this run AND to governed agents ---
def test_prior_run_ledger_row_does_not_latch(tmp_path):
    _write(tmp_path,
           _runs({"run_id": "cur", "riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"}),
           ledger_rows=[{"run_id": "OLD", "agent": "independent-auditor", "action": "verify", "tier": "code"}])
    rc, out = _run(tmp_path, "--run-id", "cur")
    assert rc == 0 and out["verdict"] == "pass"           # OLD-run row ignored

def test_non_governed_agent_wrong_tier_ignored(tmp_path):
    # A deliberately-Sonnet verification agent (synthesis-critic) at tier=code must NOT fail.
    _write(tmp_path,
           _runs({"run_id": "cur", "riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"}),
           ledger_rows=[{"run_id": "cur", "agent": "synthesis-critic", "action": "verify", "tier": "code"}])
    rc, out = _run(tmp_path, "--run-id", "cur")
    assert rc == 0 and out["verdict"] == "pass"

def test_this_run_auditor_wrong_tier_fails(tmp_path):
    _write(tmp_path,
           _runs({"run_id": "cur", "riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"}),
           ledger_rows=[{"run_id": "cur", "agent": "independent-auditor", "action": "verify", "tier": "code", "model": "claude-sonnet-5"}])
    rc, out = _run(tmp_path, "--run-id", "cur")
    assert rc == 1 and any(f["layer"] == "agent-ledger" for f in out["findings"])


# --- C1 (opt-in --require-seats): broaden attestation beyond auditor/advisor ---
def test_require_seats_off_by_default_is_backcompat(tmp_path):
    # synthesisDensity>5 with NO plan-critic/scope-auditor ledger rows, flag OFF → unchanged pass.
    _write(tmp_path, _runs({"run_id": "r1", "synthesisDensity": 9, "auditor_status": "ran:dispatched-agent"}))
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass" and out["missing_seats"] == []


def test_require_seats_flags_missing_security_on_risk(tmp_path):
    _write(tmp_path, _runs({"run_id": "r1", "riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"}))
    rc, out = _run(tmp_path, "--require-seats", agent_tool="true")
    assert rc == 1 and out["verdict"] == "fail"
    assert out["missing_seats"] == ["security-reviewer"]
    assert any(f["layer"] == "security-reviewer" for f in out["findings"])


def test_require_seats_present_passes(tmp_path):
    _write(tmp_path,
           _runs({"run_id": "cur", "riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"}),
           ledger_rows=[{"run_id": "cur", "agent": "security-reviewer", "action": "verify", "tier": "frontier"}])
    rc, out = _run(tmp_path, "--require-seats", "--run-id", "cur")
    assert rc == 0 and out["verdict"] == "pass" and out["missing_seats"] == []


def test_require_seats_synthdensity_requires_both_and_reports_partial(tmp_path):
    # plan-critic present, scope-auditor missing → only scope-auditor flagged.
    _write(tmp_path,
           _runs({"run_id": "cur", "synthesisDensity": 9, "auditor_status": "ran:dispatched-agent"}),
           ledger_rows=[{"run_id": "cur", "agent": "plan-critic", "action": "verify", "tier": "frontier"}])
    rc, out = _run(tmp_path, "--require-seats", "--run-id", "cur")
    assert rc == 1 and out["missing_seats"] == ["scope-auditor"]


def test_require_seats_nested_warns_not_fails(tmp_path):
    _write(tmp_path, _runs({"run_id": "r1", "riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"}))
    rc, out = _run(tmp_path, "--require-seats", agent_tool="false")
    assert rc == 0 and out["verdict"] == "warn" and out["missing_seats"] == ["security-reviewer"]
