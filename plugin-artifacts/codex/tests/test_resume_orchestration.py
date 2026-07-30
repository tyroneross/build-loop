"""End-to-end Path A acceptance test for crash-recovery via state.json.

Simulates the orchestrator's helper-layer behavior:
  1. Phase 1 Assess complete → update_execution_state('start', ...)
  2. Phase 3 Execute dispatches 4 chunks; chunks 1+2 return cleanly with M1 envelopes;
     fault injected via BUILD_LOOP_INJECT_FAULT=after_chunk_2 raises after chunk 2 return.
  3. Inspect state.json + envelopes — verify the contract the resolver depends on.
  4. Re-dispatch via resume_resolver — verify it returns decision='resume' with
     exactly chunks 3 + 4 in remaining_chunks.
  5. Complete chunks 3 + 4 with envelopes + state updates.
  6. update_execution_state('complete') and verify phase=='report'.

This is the acceptance gate from docs/plans/crash-recovery-state-json.md §Acceptance gate
Path A. Without this test passing, the build is incomplete.

The test does NOT exercise the live build-orchestrator agent; it exercises the SAME helper
functions the agent calls. If the helpers preserve the right state, the agent's resume
behavior is correct (the agent code path is the prompt text in §0 Resume mode, which is
human-language instructions — not unit-testable in isolation).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from resume_resolver import resolve  # noqa: E402
from write_run_entry import update_execution_state  # noqa: E402
from write_subagent_result import write_subagent_result  # noqa: E402


class SimulatedFault(RuntimeError):
    """Stand-in for a real Anthropic 529 / OOM / SIGKILL terminating the orchestrator stream."""


def _maybe_inject_fault(after_chunk_index: int, current_index: int) -> None:
    """Mirrors the orchestrator's implementer-dispatch wrapper check.

    The build-orchestrator agent reads BUILD_LOOP_INJECT_FAULT after each chunk return.
    Recognized values: 'after_chunk_<n>' (1-indexed). Raises SimulatedFault if matched.
    """
    flag = os.environ.get("BUILD_LOOP_INJECT_FAULT", "")
    if flag.startswith("after_chunk_"):
        try:
            target = int(flag.split("_")[-1])
        except ValueError:
            return
        if current_index == target:
            raise SimulatedFault(f"Synthetic 529 injected after chunk {current_index}")


def _dispatch_and_return(workdir: Path, run_id: str, state_path: Path, chunk_id: str, attempt: int = 1) -> None:
    """Helper-layer simulation of one full dispatch + implementer-return cycle.

    1. update_execution_state('dispatch_chunk') — moves queued → in_flight
    2. (orchestrator dispatches the implementer subagent, which writes the file)
    3. write_subagent_result(envelope) — atomic-write the M1 envelope
    4. update_execution_state('return_chunk', status='fixed') — moves in_flight → completed
    """
    update_execution_state(state_path, "dispatch_chunk", chunk_id=chunk_id)
    # Implementer "writes a file" — path matches file_ownership for the chunk
    target = workdir / f"{chunk_id}.py"
    target.write_text(f"# implementation for {chunk_id}\n")
    write_subagent_result(workdir, run_id, {
        "chunk_id": chunk_id,
        "status": "fixed",
        "files_changed": [f"{chunk_id}.py"],
        "verifications": [f"pytest test_{chunk_id}.py: 1/1 passed"],
        "attempt": attempt,
    })
    update_execution_state(state_path, "return_chunk", chunk_id=chunk_id, status="fixed")


def test_path_a_synthetic_529_injection_round_trip(tmp_path, monkeypatch):
    """Full Path A acceptance gate.

    Reproduces the conditions of the original 529 crash that motivated the plan.
    """
    workdir = tmp_path
    run_id = "run_20260506T220000Z_pathA0001"
    chunks = ["c1", "c2", "c3", "c4"]
    file_ownership = {c: [f"{c}.py"] for c in chunks}

    state_path = workdir / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Phase 1 Assess complete: orchestrator generates run_id and writes execution.start
    update_execution_state(
        state_path, "start",
        run_id=run_id,
        queued_chunks=chunks,
        file_ownership=file_ownership,
        now=datetime(2026, 5, 6, 22, 0, 0, tzinfo=timezone.utc),
    )

    # ── Phase 3 Execute begins. Inject fault after chunk 2.
    monkeypatch.setenv("BUILD_LOOP_INJECT_FAULT", "after_chunk_2")

    crash_caught = False
    for idx, chunk_id in enumerate(chunks, start=1):
        try:
            _dispatch_and_return(workdir, run_id, state_path, chunk_id)
            _maybe_inject_fault(after_chunk_index=2, current_index=idx)
        except SimulatedFault:
            crash_caught = True
            break

    assert crash_caught, "fault injection must terminate the loop after chunk 2"

    # ── Inspect state.json — load-bearing assertion #1 (M2 schema correct)
    state = json.loads(state_path.read_text())
    execution = state["execution"]
    assert execution["run_id"] == run_id
    assert execution["phase"] == "execute"  # never transitioned to review
    assert execution["iterate_attempt"] == 0
    assert execution["schema_version"] == 1
    assert execution["in_flight_chunks"] == [], "all dispatched chunks returned"
    assert {c["chunk_id"] for c in execution["completed_chunks"]} == {"c1", "c2"}
    assert all(c["status"] == "fixed" for c in execution["completed_chunks"])
    assert execution["queued_chunks"] == ["c3", "c4"]

    # ── Inspect subagent-results — load-bearing assertion #2 (M1 envelopes survive)
    envelope_dir = workdir / ".build-loop" / "subagent-results" / run_id
    envelope_files = sorted(p.name for p in envelope_dir.iterdir())
    assert envelope_files == ["c1.attempt-1.json", "c2.attempt-1.json"]
    for fp in envelope_dir.iterdir():
        env = json.loads(fp.read_text())
        assert env["status"] == "fixed"
        assert env["files_changed"]

    # ── Stop the fault injection so the next call doesn't blow up
    monkeypatch.delenv("BUILD_LOOP_INJECT_FAULT", raising=False)

    # ── Phase: user re-dispatches with /build-loop:run --resume <run_id>.
    # Skill body calls resume_resolver.resolve()
    resolved = resolve(workdir, run_id)
    assert resolved["decision"] == "resume"
    assert resolved["run_id"] == run_id
    remaining_ids = {r["chunk_id"] for r in resolved["remaining_chunks"]}
    assert remaining_ids == {"c3", "c4"}, "resume must compute exactly the unfinished chunks"
    assert resolved["concurrent_modifications"] == [], "no files hand-modified between crash and resume"
    assert resolved["iterate_attempt"] == 0

    # ── Agent enters §0 Resume mode and finishes the build by dispatching c3 + c4 only
    for chunk_id in ("c3", "c4"):
        _dispatch_and_return(workdir, run_id, state_path, chunk_id)

    # ── Phase 4 Review-F: orchestrator transitions phase + completes
    update_execution_state(state_path, "phase_transition", phase="review")
    update_execution_state(state_path, "complete")

    # ── Final state inspection
    state = json.loads(state_path.read_text())
    execution = state["execution"]
    assert execution["phase"] == "report", "clean-completion sentinel set"
    assert execution["queued_chunks"] == []
    assert execution["in_flight_chunks"] == []
    assert {c["chunk_id"] for c in execution["completed_chunks"]} == set(chunks)
    # Iterate attempt preserved (still 0 — no Iterate cycle in this scenario)
    assert execution["iterate_attempt"] == 0

    # All 4 envelopes on disk
    envelope_files = sorted(p.name for p in envelope_dir.iterdir())
    assert envelope_files == [
        "c1.attempt-1.json", "c2.attempt-1.json",
        "c3.attempt-1.json", "c4.attempt-1.json",
    ]

    # ── Once complete, --resume against this run_id must refuse
    redo = resolve(workdir, run_id)
    assert redo["decision"] == "abort"
    assert "already complete" in redo["reason"]


def test_path_a_iterate_attempt_preserved_across_resume(tmp_path, monkeypatch):
    """Cross-cutting validation: iterate_attempt counter survives the crash.

    Without this, resume could silently bypass the 5x iteration cap.
    """
    workdir = tmp_path
    run_id = "run_iter_test"
    state_path = workdir / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(
        state_path, "start",
        run_id=run_id,
        queued_chunks=["c1", "c2"],
        file_ownership={"c1": ["c1.py"], "c2": ["c2.py"]},
    )
    # Simulate getting through 2 iterate cycles before the crash
    update_execution_state(state_path, "iterate_attempt")
    update_execution_state(state_path, "iterate_attempt")
    # Crash. Resume.
    resolved = resolve(workdir, run_id)
    assert resolved["decision"] == "resume"
    assert resolved["iterate_attempt"] == 2, (
        "5x cap would silently bypass without this — must surface to the agent's §0 branch"
    )


def test_path_a_concurrent_modification_detected(tmp_path, monkeypatch):
    """Cross-cutting validation: hand-edits between crash and resume are flagged."""
    import subprocess
    import time

    workdir = tmp_path
    subprocess.check_call(["git", "init", "-q"], cwd=workdir)
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=workdir)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=workdir)

    run_id = "run_cm_test"
    state_path = workdir / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(
        state_path, "start",
        run_id=run_id,
        queued_chunks=["c1", "c2"],
        file_ownership={"c1": ["c1.py"], "c2": ["c2.py"]},
    )

    # Complete c1 cleanly
    (workdir / "c1.py").write_text("# v1\n")
    subprocess.check_call(["git", "add", "c1.py"], cwd=workdir)
    subprocess.check_call(["git", "commit", "-qm", "v1"], cwd=workdir)
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    write_subagent_result(workdir, run_id, {
        "chunk_id": "c1", "status": "fixed",
        "files_changed": ["c1.py"], "verifications": ["ok"], "attempt": 1,
    })
    update_execution_state(state_path, "return_chunk", chunk_id="c1", status="fixed")

    # Crash. User hand-edits c1.py in the gap.
    time.sleep(0.05)
    (workdir / "c1.py").write_text("# v2 hand-edited mid-crash\n")

    # Resume should flag c1 as concurrent_modification_detected
    resolved = resolve(workdir, run_id)
    assert resolved["decision"] == "resume"
    flagged = {m["chunk_id"] for m in resolved["concurrent_modifications"]}
    assert "c1" in flagged
    # c1 is now back in remaining_chunks with the demoted prior_status
    remaining_c1 = [r for r in resolved["remaining_chunks"] if r["chunk_id"] == "c1"]
    assert remaining_c1
    assert remaining_c1[0]["prior_status"] == "concurrent_modification_detected"
