# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for judgment_gate.py — stakes-conditional Frontier-dispatch gate."""
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


def _run(tmp, agent_tool="true"):
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(tmp), "--agent-tool-available", agent_tool, "--json"],
        capture_output=True, text=True,
    )
    return res.returncode, json.loads(res.stdout)


def test_no_stakes_passes(tmp_path):
    _write(tmp_path, {"synthesisDensity": 2, "auditor_status": ""})
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass" and out["stakes_gated"] is False


def test_stakes_with_dispatched_auditor_passes(tmp_path):
    _write(tmp_path, {"triggers": {"riskSurfaceChange": True}, "auditor_status": "ran:dispatched-agent"})
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass" and "riskSurfaceChange" in out["stakes_reasons"]


def test_stakes_inline_floor_top_level_fails(tmp_path):
    # The session-this-fixes case: stakes fired, judgment sat at the inline-Opus floor, Agent tool was reachable.
    _write(tmp_path, {"riskSurfaceChange": True, "auditor_status": "fallback:inline-opus"})
    rc, out = _run(tmp_path, agent_tool="true")
    assert rc == 1 and out["verdict"] == "fail"
    assert any(f["layer"] == "independent-auditor" and f["severity"] == "fail" for f in out["findings"])


def test_missing_auditor_status_top_level_fails(tmp_path):
    _write(tmp_path, {"stakes": "high"})  # no auditor_status recorded at all
    rc, out = _run(tmp_path, agent_tool="true")
    assert rc == 1 and out["verdict"] == "fail"


def test_nested_no_agent_tool_warns_not_fails(tmp_path):
    _write(tmp_path, {"synthesisDensity": 9, "auditor_status": "not-run:parent-must-dispatch"})
    rc, out = _run(tmp_path, agent_tool="false")
    assert rc == 0 and out["verdict"] == "warn"


def test_advisor_inline_floor_fails(tmp_path):
    _write(tmp_path, {"riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent",
                      "advisor_status": "fallback:inline-opus"})
    rc, out = _run(tmp_path)
    assert rc == 1 and any(f["layer"] == "advisor" for f in out["findings"])


def test_advisor_inline_frontier_ok(tmp_path):
    _write(tmp_path, {"stakes": "medium", "auditor_status": "ran:peer-host",
                      "advisor_status": "inline-frontier"})
    rc, out = _run(tmp_path)
    assert rc == 0 and out["verdict"] == "pass"


def test_ledger_wrong_tier_fails(tmp_path):
    _write(tmp_path,
           {"riskSurfaceChange": True, "auditor_status": "ran:dispatched-agent"},
           ledger_rows=[{"agent": "independent-auditor", "action": "verify", "tier": "code", "model": "claude-sonnet-4-6"}])
    rc, out = _run(tmp_path)
    assert rc == 1 and any(f["layer"] == "agent-ledger" for f in out["findings"])


def test_stakes_from_latest_run_entry(tmp_path):
    _write(tmp_path, {"runs": [{"run_id": "r1", "riskSurfaceChange": True, "auditor_status": "fallback:inline-opus"}]})
    rc, out = _run(tmp_path, agent_tool="true")
    assert rc == 1 and out["stakes_gated"] is True
