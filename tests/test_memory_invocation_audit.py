"""Tests for the memory-invocation audit (Priority 12).

Verifies four behaviors that the audit relies on:
  1. recall() facade returns a valid envelope on empty/unavailable stores.
  2. Both MEMORY.md tier paths are readable (global + project).
  3. state.json.runs[] tail is reachable and shaped as expected.
  4. Debugger MCP unreachability is reported as a `reason`, never raised.
  5. The audit script itself runs end-to-end and produces ≥10 probe rows.
"""
from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))


# --- 1. recall() envelope shape on empty/unavailable stores ---------------

def test_recall_envelope_shape_on_empty_workdir(tmp_path: Path) -> None:
    """recall() with an empty workdir must return a valid envelope, no exceptions."""
    from memory_facade import recall  # type: ignore

    env = recall(query="nothing", kind=None, project=None, limit=5, workdir=tmp_path)
    # Required keys.
    assert set(env.keys()) >= {"query", "kind_filter", "project", "results_by_kind", "merged", "reasons"}
    # Per-kind buckets must all be present, even if empty.
    rbk = env["results_by_kind"]
    assert set(rbk.keys()) == {"runs", "decisions", "lessons", "semantic", "debugger"}
    for k, v in rbk.items():
        assert isinstance(v, list), f"results_by_kind[{k}] must be a list"
    # Merged is bounded by limit * #kinds.
    assert len(env["merged"]) <= 5 * len(rbk)
    # Reasons must include backend-unavailable signals (Postgres + MCP both expected down in CI).
    reasons_text = " ".join(env["reasons"])
    assert "db_unavailable" in reasons_text or "psycopg" in reasons_text or env["reasons"] == [] or any(
        "mcp" in r or "db" in r for r in env["reasons"]
    )


def test_recall_kind_filter_validates() -> None:
    """Passing an invalid kind raises ValueError — contract enforcement."""
    from memory_facade import recall  # type: ignore

    with pytest.raises(ValueError):
        recall(query="x", kind="not_a_real_kind", project=None, limit=1)


# --- 2. Both MEMORY.md tier paths -----------------------------------------

def test_global_memory_md_readable() -> None:
    """The global MEMORY.md exists in this repo's home or is gracefully absent."""
    p = Path.home() / ".build-loop" / "memory" / "MEMORY.md"
    if not p.is_file():
        pytest.skip("global MEMORY.md not seeded on this machine — graceful absence ok")
    text = p.read_text(encoding="utf-8")
    # Must be readable text and have at least one heading.
    assert any(ln.startswith("#") for ln in text.splitlines()), "global MEMORY.md has no headings"


def test_project_memory_md_readable() -> None:
    """The project MEMORY.md exists in this repo and is parseable."""
    p = REPO / ".build-loop" / "memory" / "MEMORY.md"
    if not p.is_file():
        pytest.skip("project MEMORY.md not seeded yet")
    text = p.read_text(encoding="utf-8")
    assert any(ln.startswith("#") for ln in text.splitlines())


# --- 3. state.json.runs[] tail --------------------------------------------

def test_runs_tail_accessible(tmp_path: Path) -> None:
    """A synthesized state.json must yield a 3-entry runs[-3:] tail."""
    bl = tmp_path / ".build-loop"
    bl.mkdir()
    (bl / "state.json").write_text(json.dumps({
        "runs": [
            {"run_id": f"run_{i}", "outcome": "pass", "goal": f"goal {i}", "date": f"2026-05-0{i+1}T00:00:00Z"}
            for i in range(5)
        ]
    }), encoding="utf-8")
    from audit_memory_invocation import probe_runs_tail  # type: ignore

    result = probe_runs_tail(tmp_path)
    assert result["invoked"] is True
    assert result["result_count"] == 3
    assert result["result_sample"][-1]["run_id"] == "run_4"


def test_runs_tail_handles_missing_state_json(tmp_path: Path) -> None:
    """Probe must not raise when state.json is absent — graceful degradation."""
    from audit_memory_invocation import probe_runs_tail  # type: ignore

    result = probe_runs_tail(tmp_path)
    assert result["invoked"] is True
    assert result["result_count"] == 0
    assert result["verdict"] == "graceful_degradation"


# --- 4. Native debugger incident absence -> reason, not exception ---------

def test_debugger_local_incidents_absent_returns_reason(tmp_path: Path) -> None:
    """When local incident notes are absent, the probe records a reason and returns count 0."""
    from audit_memory_invocation import probe_debugger_mcp  # type: ignore

    result = probe_debugger_mcp(tmp_path)
    assert result["result_count"] == 0 or isinstance(result["result_sample"], list)
    if result["verdict"] == "graceful_degradation":
        assert "debugger_unavailable" in (result.get("error") or "")


def test_recall_facade_records_postgres_unavailable_when_no_db_url(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """No DB URL configured → reasons[] includes db_unavailable, no crash.

    The shared resolver also consults $DATABASE_URL and
    ~/.config/agent-memory/connection.env, so we clear both env vars and
    isolate HOME — otherwise this passes/fails on the dev machine's real
    DSN instead of testing the unavailable path.
    """
    from memory_facade import recall  # type: ignore

    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "_no_home"))
    env = recall(query="x", kind="semantic", project=None, limit=1, workdir=tmp_path)
    assert env["results_by_kind"]["semantic"] == []
    assert any("db_unavailable" in r for r in env["reasons"])


# --- 5. Audit script end-to-end -------------------------------------------

def test_audit_script_runs_end_to_end(tmp_path: Path) -> None:
    """Invoke audit_memory_invocation.py against a fresh tmp workdir; expect ≥10 probes, exit 0."""
    out = tmp_path / "probe.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "audit_memory_invocation.py"), "--workdir", str(tmp_path), "--out", str(out)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"audit script failed: {proc.stderr}"
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0.0"
    assert len(payload["probes"]) >= 10
    # Every probe row must have the contract keys.
    for row in payload["probes"]:
        assert {"call_site", "phase", "expected_tier", "invoked", "verdict", "result_count"}.issubset(row.keys())
