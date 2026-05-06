"""Tests for Priority 19 — orchestrator self-instrumentation.

Run 5's meta-validation surfaced that the live orchestrator turn doesn't
always invoke `capability_shortlist.py` at Phase 1 — only when subagent
fan-out is imminent. So `state.json.activeCapabilities` stayed cold for
inline-execution builds.

Priority 19 fix: make Phase 1 shortlist invocation mandatory in the
orchestrator agent definition AND in the `capability_shortlist.py` CLI by
adding a `--cache-into-state` flag that callers (notably the orchestrator)
use to express that caching is required, not optional.

These tests pin the contract:
  1. The script supports `--cache-into-state` and writes to the same
     `state.json.activeCapabilities[<phase>]` location regardless of whether
     subagent fan-out occurs downstream.
  2. The agent definition mandates the Phase 1 invocation (regression guard
     against silent removal in future doc edits).
  3. The two flags `--no-cache` and `--cache-into-state` are mutually
     exclusive — the orchestrator can never be tricked into a no-op cache.

Static-analysis style: no orchestrator runtime is required. We invoke the
script directly and grep the agent file.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
ORCHESTRATOR = REPO / "agents" / "build-orchestrator.md"
SHORTLIST_CLI = SCRIPTS / "capability_shortlist.py"


def _run_shortlist(args: list[str], workdir: Path) -> subprocess.CompletedProcess[str]:
    """Run capability_shortlist.py with the given args and a synthetic workdir."""
    cmd = [sys.executable, str(SHORTLIST_CLI), *args, "--workdir", str(workdir)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _seed_registry(workdir: Path) -> None:
    """Symlink the real capability registry into a synthetic workdir so the
    matcher has something to score against. The shortlist auto-rebuilds when
    missing, but the rebuild scans the repo — quicker to symlink the
    pre-built registry from the build-loop repo root."""
    real = REPO / ".build-loop" / "capability-registry.json"
    if not real.is_file():
        # Trigger rebuild against the real repo first.
        subprocess.run(
            [sys.executable, str(SCRIPTS / "build_capability_registry.py"),
             "--workdir", str(REPO)],
            capture_output=True, text=True, timeout=30,
        )
    target = workdir / ".build-loop" / "capability-registry.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI contract: --cache-into-state writes to activeCapabilities[<phase>]
# ---------------------------------------------------------------------------

def test_shortlist_runs_even_for_inline_execution(tmp_path: Path) -> None:
    """Synthetic build flow with no fan-out — caching must still happen so
    Phase 2 / Phase 3 dispatchers (which read the cache) are never empty."""
    _seed_registry(tmp_path)
    result = _run_shortlist(
        ["--phase", "1", "--intent", "inline-execution build with no fan-out",
         "--json", "--cache-into-state"],
        tmp_path,
    )
    assert result.returncode == 0, f"stderr={result.stderr}"

    state_path = tmp_path / ".build-loop" / "state.json"
    assert state_path.is_file(), "state.json must be created/updated by --cache-into-state"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    cap = state.get("activeCapabilities")
    assert isinstance(cap, dict), "activeCapabilities must be phase-keyed dict"
    assert "1" in cap, "Phase 1 bucket must be populated"
    bucket = cap["1"]
    assert isinstance(bucket, list) and len(bucket) >= 1
    latest = bucket[-1]
    assert latest["phase"] == 1
    assert "results" in latest
    assert len(latest["results"]) > 0, "shortlist must contain entries"


def test_shortlist_caches_to_phase_keyed_dict(tmp_path: Path) -> None:
    """`--cache-into-state` writes to activeCapabilities[<phase>] (not a flat
    list). Multiple phases populate independent buckets."""
    _seed_registry(tmp_path)
    for phase, intent in [(1, "assess phase"), (2, "plan phase"), (3, "execute phase")]:
        rc = _run_shortlist(
            ["--phase", str(phase), "--intent", intent, "--json", "--cache-into-state"],
            tmp_path,
        )
        assert rc.returncode == 0, f"phase {phase}: stderr={rc.stderr}"

    state = json.loads((tmp_path / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    cap = state["activeCapabilities"]
    assert isinstance(cap, dict)
    for phase_key in ("1", "2", "3"):
        assert phase_key in cap, f"phase {phase_key} bucket missing"
        latest = cap[phase_key][-1]
        assert latest["phase"] == int(phase_key)


def test_no_cache_and_cache_into_state_are_mutually_exclusive(tmp_path: Path) -> None:
    """The orchestrator can never accidentally combine the opt-out with the
    explicit-cache flag. Exit code 2 (argparse-style usage error)."""
    _seed_registry(tmp_path)
    rc = _run_shortlist(
        ["--phase", "1", "--intent", "x", "--no-cache", "--cache-into-state"],
        tmp_path,
    )
    assert rc.returncode == 2
    assert "mutually exclusive" in (rc.stderr or "").lower()


# ---------------------------------------------------------------------------
# Agent-definition contract: Phase 1 invocation is mandatory
# ---------------------------------------------------------------------------

def test_orchestrator_phase_1_mandates_shortlist_invocation() -> None:
    """The build-orchestrator agent definition must explicitly mandate a
    Phase 1 capability-shortlist invocation with `--cache-into-state` so the
    cache is populated even for inline-execution builds (Run 5 regression).

    Pin the marker phrases to catch silent removal.
    """
    text = ORCHESTRATOR.read_text(encoding="utf-8")
    # 1. The Phase 1 Assess block must reference the shortlist script.
    assert "capability_shortlist.py" in text, \
        "Phase 1 Assess must invoke capability_shortlist.py"
    # 2. The mandatory framing must be present.
    lower = text.lower()
    assert "mandatory" in lower or "always" in lower, \
        "Phase 1 shortlist invocation must be framed as mandatory/always"
    # 3. The --cache-into-state flag must appear so the agent uses the
    #    explicit-cache path (not an unflagged default that future authors
    #    might silently inline-comment out).
    assert "--cache-into-state" in text, \
        "Phase 1 invocation must use --cache-into-state explicitly"
    # 4. Inline-execution justification must be present so future readers
    #    understand why this fires regardless of fan-out.
    assert "inline" in lower or "fan-out" in lower or "fan out" in lower, \
        "Phase 1 mandate must justify itself against the inline-execution case"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
