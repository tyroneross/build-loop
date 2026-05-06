"""Tests for scripts/backend_health.py.

Phase 1 backend health-check surface (Priority 17). No live Postgres, no
live MCP — DB and debugger probes are stubbed via the test-injection
setters. Filesystem-only probes (runs, decisions) read synthetic state
under tmp_path.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backend_health as bh  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic project root with state.json + .episodic/decisions/.

    Pins AGENT_MEMORY_ROOT to a tmp path so the canonical decisions probe
    (Priority 20) resolves to a non-existent dir — keeps the legacy-only
    assertions in pre-Priority-20 tests deterministic. Tests that exercise
    the canonical probe override this in their own scope.
    """
    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_no_canonical"))
    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)

    bl = tmp_path / ".build-loop"
    bl.mkdir()
    state = {
        "runs": [{"run_id": "r1"}, {"run_id": "r2"}],
    }
    (bl / "state.json").write_text(json.dumps(state), encoding="utf-8")

    decisions = tmp_path / ".episodic" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "001-pick-postgres.md").write_text("# decision", encoding="utf-8")
    (decisions / "002-pick-mcp.md").write_text("# decision", encoding="utf-8")

    yield tmp_path

    # Reset overrides so other tests don't see them.
    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------

def test_envelope_shape_all_backends_present(workdir: Path) -> None:
    """All 4 keys + summary must be present in every run."""
    # Stub the two external backends to avoid live calls.
    bh.set_debugger_runner(lambda: (False, "mcp_unreachable: stubbed"))
    bh.set_semantic_runner(lambda: (False, "postgres_unavailable: stubbed"))

    env = bh.run_health_check(workdir)

    for key in ("runs", "decisions", "semantic", "debugger"):
        assert key in env, f"missing backend key: {key}"
        assert "ok" in env[key]
        assert "duration_ms" in env[key]
    assert "summary" in env
    assert "generated_at" in env
    assert "total_duration_ms" in env


def test_runs_probe_counts_entries(workdir: Path) -> None:
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    env = bh.run_health_check(workdir)
    assert env["runs"]["ok"] is True
    assert env["runs"]["count"] == 2


def test_decisions_probe_counts_md_files(workdir: Path) -> None:
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    env = bh.run_health_check(workdir)
    assert env["decisions"]["ok"] is True
    assert env["decisions"]["count"] == 2


def test_semantic_and_debugger_down_emit_reasons(workdir: Path) -> None:
    """Both backends down → both have `reason` populated; envelope still valid."""
    bh.set_debugger_runner(lambda: (False, "mcp_unreachable: stubbed"))
    bh.set_semantic_runner(lambda: (False, "postgres_unavailable: stubbed"))

    env = bh.run_health_check(workdir)
    assert env["semantic"]["ok"] is False
    assert "postgres_unavailable" in env["semantic"]["reason"]
    assert env["debugger"]["ok"] is False
    assert "mcp_unreachable" in env["debugger"]["reason"]


def test_summary_contains_all_four_backend_labels(workdir: Path) -> None:
    """One-liner must mention runs / decisions / semantic / debugger."""
    bh.set_debugger_runner(lambda: (False, "mcp_unreachable: stubbed"))
    bh.set_semantic_runner(lambda: (False, "postgres_unavailable: stubbed"))

    env = bh.run_health_check(workdir)
    summary = env["summary"]
    for label in ("runs:", "decisions:", "semantic:", "debugger:"):
        assert label in summary, f"summary missing label {label}: {summary!r}"


def test_state_json_population_after_write(workdir: Path) -> None:
    """write_into_state populates state.json.architecture.backendHealth."""
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    env = bh.run_health_check(workdir)
    bh.write_into_state(workdir, env)

    state = json.loads((workdir / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    assert "architecture" in state
    assert "backendHealth" in state["architecture"]
    assert state["architecture"]["backendHealth"]["summary"] == env["summary"]


def test_graceful_when_state_json_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `.build-loop/state.json` should not crash; runs reports state_json_missing."""
    # Create an empty workdir with no .build-loop/. Pin canonical to a
    # missing path so this test stays deterministic across hosts that may
    # have a populated ~/dev/git-folder/build-loop-memory/decisions/.
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_no_canonical"))
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    env = bh.run_health_check(tmp_path)
    assert env["runs"]["ok"] is False
    assert env["runs"]["reason"] == "state_json_missing"
    # Other backends still classifiable (decisions reports both stores DOWN).
    assert env["decisions"]["ok"] is False


def test_smoke_real_run_against_repo() -> None:
    """Smoke test on the actual build-loop repo. Postgres known down + npx
    may or may not be on PATH; runs + decisions must succeed."""
    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)
    # Don't write to the live state.json — just probe.
    env = bh.run_health_check(REPO)
    assert env["runs"]["ok"] is True, f"runs probe failed: {env['runs']}"
    # `.episodic/decisions/` may or may not exist on this repo. Either way, the
    # probe must classify gracefully (ok or reason set).
    assert "ok" in env["decisions"]
    # Two backends must minimally be probable.
    ok_count = sum(1 for k in ("runs", "decisions", "semantic", "debugger") if env[k]["ok"])
    assert ok_count >= 1, f"no backends OK on this repo: {env['summary']}"


def test_exit_code_zero_when_backends_down(workdir: Path, capsys: pytest.CaptureFixture) -> None:
    """Even with all 4 backends down, exit code is 0 (graceful)."""
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    rc = bh.main(["--workdir", str(workdir), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "summary" in payload
