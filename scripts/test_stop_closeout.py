# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for stop_closeout.py — structural Stop-hook closeout for inline runs (f6)."""
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stop_closeout  # noqa: E402

SCRIPT = Path(__file__).parent / "stop_closeout.py"
SESSION = "sess-abc"


def _now():
    return stop_closeout._utc_now()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_state(tmp: Path, state: dict) -> Path:
    bl = tmp / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "state.json").write_text(json.dumps(state))
    return bl / "state.json"


def _base_state(session=SESSION, run_id="bl-test-001", phase="done", stakes_trigger=True, runs=None):
    return {
        "phase": phase,
        "triggers": {"riskSurfaceChange": True} if stakes_trigger else {"riskSurfaceChange": False},
        "execution": {
            "build_loop_id": run_id,
            "current_session_id": session,
            "started_by_session_id": session,
            "last_heartbeat_at": _iso(_now()),
            "run_label": "test#001",
        },
        "runs": runs if runs is not None else [],
    }


def _runs(tmp: Path) -> list:
    return json.loads((tmp / ".build-loop" / "state.json").read_text()).get("runs", [])


# --- record path -----------------------------------------------------------

def test_records_inline_run_and_warns_when_stakes_gated(tmp_path):
    _write_state(tmp_path, _base_state())
    out = stop_closeout.run_stop(tmp_path, SESSION)

    runs = _runs(tmp_path)
    assert len(runs) == 1
    rec = runs[0]
    assert rec["run_id"] == "bl-test-001"
    assert rec["source"] == "append_run"
    assert rec["auditor_status"] == "not-run:parent-must-dispatch"  # honest floor
    assert rec["triggers"]["riskSurfaceChange"] is True  # stakes signal propagated
    # stakes-gated + judgment skipped + no Agent tool → WARN surfaced advisory.
    assert "systemMessage" in out
    assert "WARN" in out["systemMessage"]
    # marker written.
    assert (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").exists()


def test_warns_on_production_synthesisdensity_dict_shape(tmp_path):
    # Phase 1 Assess writes synthesisDensity as {count, escalated, reason} — the
    # signal the INLINE path actually produces (riskSurfaceChange is orchestrator-
    # only). The gate must read the dict, not int({...})→0. Regression for the
    # dormant-WARN defect (f6 independent audit, finding f1).
    st = _base_state(stakes_trigger=False)   # no riskSurfaceChange
    st["synthesisDensity"] = {"count": 9, "escalated": True, "reason": "9 modules"}
    _write_state(tmp_path, st)
    out = stop_closeout.run_stop(tmp_path, SESSION)
    assert "systemMessage" in out and "WARN" in out["systemMessage"]
    assert _runs(tmp_path)[0]["synthesisDensity"]["count"] == 9


def test_floor_auditor_status_never_inherits_stale_ran(tmp_path):
    # A stale top-level/execution `ran:` from a PRIOR run must NOT be materialized
    # as an earned status on this Stop-recorded run (f6 audit, finding f3).
    st = _base_state()
    st["auditor_status"] = "ran:dispatched-agent"
    st["execution"]["auditor_status"] = "ran:dispatched-agent"
    _write_state(tmp_path, st)
    stop_closeout.run_stop(tmp_path, SESSION)
    assert _runs(tmp_path)[0]["auditor_status"] == "not-run:parent-must-dispatch"


def test_records_quietly_when_no_stakes_trigger(tmp_path):
    _write_state(tmp_path, _base_state(stakes_trigger=False))
    out = stop_closeout.run_stop(tmp_path, SESSION)
    assert len(_runs(tmp_path)) == 1            # still recorded (Learn visibility)
    assert out == {}                            # no judgment gap → no advisory


def test_outcome_partial_when_not_done(tmp_path):
    _write_state(tmp_path, _base_state(phase="execute"))
    stop_closeout.run_stop(tmp_path, SESSION)
    assert _runs(tmp_path)[0]["outcome"] == "partial"


# --- idempotency -----------------------------------------------------------

def test_second_stop_same_run_no_double_record_no_repeat_warn(tmp_path):
    _write_state(tmp_path, _base_state())
    out1 = stop_closeout.run_stop(tmp_path, SESSION)
    assert "systemMessage" in out1                     # WARN surfaced once
    out2 = stop_closeout.run_stop(tmp_path, SESSION)    # re-record (replace), not append
    assert out2 == {}                                  # advisory not repeated
    runs = _runs(tmp_path)
    assert len(runs) == 1 and runs[0]["run_id"] == "bl-test-001"  # no double-record


def test_idle_stop_skips_rewrite_when_record_current(tmp_path):
    # A Stop fires every turn boundary; once the record carries the current
    # outcome, later Stops must not rewrite state.json/marker (write amplification).
    # Non-terminal phase: terminal outcomes release identity instead (below).
    _write_state(tmp_path, _base_state(phase="execute"))
    stop_closeout.run_stop(tmp_path, SESSION)
    sp = tmp_path / ".build-loop" / "state.json"
    before = sp.read_text()
    d = stop_closeout.decide(tmp_path, json.loads(before), SESSION, stop_closeout._utc_now())
    assert d["action"] == "skip" and "unchanged" in d["reason"]
    stop_closeout.run_stop(tmp_path, SESSION)
    assert sp.read_text() == before        # byte-identical: no rewrite happened


# --- Rally file-claim release on Stop (2026-06-29) --------------------------


class _FakeRally:
    """In-memory rally stand-in: claims live until released by event id.

    Models the verified live behavior — ``room --tool`` lists this tool's open
    claims with event ids; ``say release --tool --ref <id>`` releases that claim.
    Lets the acceptance test claim a temp path, run the closeout release logic,
    and assert the claim is gone, with no live rally binary.
    """

    def __init__(self, claims):
        # claims: list of (event_id, tool, scope_path)
        self.claims = list(claims)
        self.calls = []

    def __call__(self, args, workdir):
        import subprocess as _sp
        self.calls.append(list(args))
        if args[:1] == ["room"]:
            tool = args[args.index("--tool") + 1]
            facts = [
                {"kind": "claim", "tool": t, "event_id": e, "scope": [f"file:{p}"]}
                for (e, t, p) in self.claims
                if t == tool
            ]
            envelope = {"data": {"room": {"facts": facts}}}
            return _sp.CompletedProcess(args, 0, stdout=json.dumps(envelope), stderr="")
        if args[:2] == ["say", "release"]:
            tool = args[args.index("--tool") + 1]
            ref = args[args.index("--ref") + 1]
            before = len(self.claims)
            self.claims = [c for c in self.claims if not (c[0] == ref and c[1] == tool)]
            ok = len(self.claims) < before
            return _sp.CompletedProcess(args, 0 if ok else 3, stdout="{}", stderr="")
        return _sp.CompletedProcess(args, 1, stdout="", stderr="unexpected")


def test_release_my_claims_releases_this_tools_claims(tmp_path):
    # ACCEPTANCE: claim a temp path as a fake claude_code session, run the
    # release logic, assert the claim is released and other tools are untouched.
    probe = "/tmp/__stop_release_probe__.txt"
    fake = _FakeRally(
        claims=[
            ("fact_mine_1", "claude_code", probe),
            ("fact_mine_2", "claude_code", "/tmp/other_mine.txt"),
            ("fact_peer_1", "codex", "/tmp/peer.txt"),  # must NOT be touched
        ]
    )
    released = stop_closeout.release_my_claims(tmp_path, "claude_code", runner=fake)
    assert released == 2  # both claude_code claims released
    remaining = {(e, t) for (e, t, _p) in fake.claims}
    assert ("fact_mine_1", "claude_code") not in remaining  # the probe claim is gone
    assert ("fact_mine_2", "claude_code") not in remaining
    assert ("fact_peer_1", "codex") in remaining  # peer's claim preserved


def test_release_my_claims_fail_open_when_rally_absent(tmp_path):
    # rally absent → runner returns None → 0 released, no raise.
    none_runner = lambda args, workdir: None  # noqa: E731
    assert stop_closeout.release_my_claims(tmp_path, "claude_code", runner=none_runner) == 0


def test_release_my_claims_capped(tmp_path):
    many = [(f"fact_{i}", "claude_code", f"/tmp/f{i}") for i in range(stop_closeout._MAX_RELEASE_PER_STOP + 50)]
    fake = _FakeRally(claims=many)
    released = stop_closeout.release_my_claims(tmp_path, "claude_code", runner=fake)
    assert released == stop_closeout._MAX_RELEASE_PER_STOP  # capped, not all 250


def test_run_stop_invokes_claim_release(tmp_path, monkeypatch):
    # Integration: run_stop calls release_my_claims on EVERY Stop, even when the
    # run-record path skips (no state) and regardless of outcome.
    called = {"n": 0}
    monkeypatch.setattr(stop_closeout, "release_my_claims", lambda wd, **k: called.__setitem__("n", called["n"] + 1) or 0)
    # No state.json present → record path returns {} early, but release still ran.
    out = stop_closeout.run_stop(tmp_path, SESSION)
    assert out == {}
    assert called["n"] == 1


# --- terminal identity release (W5) -----------------------------------------

def test_terminal_stop_releases_identity(tmp_path):
    # A recorded `pass` closes the run: identity archived + cleared so the next
    # effort mints fresh instead of resuming a finished run (which would then
    # be silently swallowed by skip-if-unchanged).
    _write_state(tmp_path, _base_state(phase="done"))
    stop_closeout.run_stop(tmp_path, SESSION)
    st = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    assert st["execution"] == {}
    assert st["historicalExecutions"][-1]["build_loop_id"] == "bl-test-001"
    assert _runs(tmp_path)[0]["outcome"] == "pass"      # record survives release
    # next Stop: no identity → silent skip, record untouched.
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    assert len(_runs(tmp_path)) == 1


def test_partial_stop_keeps_identity_for_resume(tmp_path):
    # Crash-resume contract: resume_resolver reads execution — a non-terminal
    # outcome must NOT release identity.
    _write_state(tmp_path, _base_state(phase="execute"))
    stop_closeout.run_stop(tmp_path, SESSION)
    st = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    assert st["execution"].get("build_loop_id") == "bl-test-001"


def test_skip_path_releases_when_already_recorded_terminal(tmp_path):
    # A pass record that landed BEFORE this Stop (explicit append_run with judge
    # decisions, or replay) takes the skip-unchanged path — identity must still
    # close, else the next effort resumes a finished run.
    st = _base_state(phase="done")
    st["runs"] = [{"run_id": "bl-test-001", "outcome": "pass", "source": "append_run",
                   "date": "2026-06-13T00:00:00Z", "goal": "g", "phases": {}}]
    _write_state(tmp_path, st)
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    st2 = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    assert st2["execution"] == {}
    assert st2["historicalExecutions"][-1]["build_loop_id"] == "bl-test-001"
    assert len(st2["runs"]) == 1                      # record untouched


def test_skip_path_releases_richer_terminal_record(tmp_path):
    # Audit f3: a Review-G/write_run_entry record (NO source key) with outcome
    # pass must also close identity via the already_recorded arm.
    st = _base_state(phase="done")
    st["runs"] = [{"run_id": "bl-test-001", "outcome": "pass",
                   "date": "2026-06-13T00:00:00Z", "goal": "g", "phases": {}}]
    _write_state(tmp_path, st)
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    st2 = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    assert st2["execution"] == {}
    assert st2["historicalExecutions"][-1]["build_loop_id"] == "bl-test-001"


def test_already_recorded_nonterminal_keeps_identity(tmp_path):
    # A richer PARTIAL record must NOT release (run may resume).
    st = _base_state(phase="execute")
    st["runs"] = [{"run_id": "bl-test-001", "outcome": "partial",
                   "date": "2026-06-13T00:00:00Z", "goal": "g", "phases": {}}]
    _write_state(tmp_path, st)
    stop_closeout.run_stop(tmp_path, SESSION)
    st2 = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    assert st2["execution"].get("build_loop_id") == "bl-test-001"


def test_converge_then_release(tmp_path):
    # partial Stop keeps identity; the later Stop that converges to pass
    # re-records AND releases.
    _write_state(tmp_path, _base_state(phase="execute"))
    stop_closeout.run_stop(tmp_path, SESSION)
    sp = tmp_path / ".build-loop" / "state.json"
    st = json.loads(sp.read_text())
    assert st["execution"].get("build_loop_id") == "bl-test-001"
    st["phase"] = "done"
    sp.write_text(json.dumps(st))
    stop_closeout.run_stop(tmp_path, SESSION)
    st2 = json.loads(sp.read_text())
    assert st2["execution"] == {}
    runs = _runs(tmp_path)
    assert len(runs) == 1 and runs[0]["outcome"] == "pass"


def test_later_stop_converges_outcome_to_terminal_state(tmp_path):
    # Multi-turn inline run: first Stop mid-run (partial), final Stop done.
    _write_state(tmp_path, _base_state(phase="execute"))
    stop_closeout.run_stop(tmp_path, SESSION)
    assert _runs(tmp_path)[0]["outcome"] == "partial"
    # run completes; phase advances; final Stop re-records.
    st = json.loads((tmp_path / ".build-loop" / "state.json").read_text())
    st["phase"] = "done"
    (tmp_path / ".build-loop" / "state.json").write_text(json.dumps(st))
    stop_closeout.run_stop(tmp_path, SESSION)
    runs = _runs(tmp_path)
    assert len(runs) == 1 and runs[0]["outcome"] == "pass"   # converged, not frozen


def test_reviewg_replace_preserves_stakes_evidence(tmp_path):
    # Regression for f6 (introduced by the iohelpers replace fix): when Review-G's
    # write_run_entry replaces the thin Stop record, the run's stakes evidence must
    # survive so the gate still knows the run was stakes-gated.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import judgment_gate
    from write_run_entry.iohelpers import append_run_entry

    st = _base_state(stakes_trigger=False)
    st["synthesisDensity"] = {"count": 9, "escalated": True, "reason": "9 modules"}
    state_path = _write_state(tmp_path, st)
    stop_closeout.run_stop(tmp_path, SESSION)                      # thin record w/ stakes
    # Review-G writes its record (no stakes fields, real auditor verdict).
    append_run_entry(state_path, {
        "run_id": "bl-test-001", "date": "2026-06-12T01:00:00Z", "goal": "g",
        "outcome": "pass", "phases": {}, "auditor_status": "ran:dispatched-agent",
    })
    runs = _runs(tmp_path)
    assert len(runs) == 1                                          # replaced, not duplicated
    assert runs[0]["synthesisDensity"]["count"] == 9              # stakes evidence preserved
    assert runs[0]["auditor_status"] == "ran:dispatched-agent"    # judgment status owned by Review-G
    res = judgment_gate.evaluate(
        json.loads(state_path.read_text()),
        tmp_path / ".build-loop" / "agent-ledger.jsonl", "bl-test-001", agent_tool_available=True,
    )
    assert res["stakes_gated"] is True and res["verdict"] == "pass"  # gated + auditor ran → clean pass


def test_does_not_clobber_richer_orchestrator_record(tmp_path):
    rich = {"run_id": "bl-test-001", "date": "2026-06-12T00:00:00Z", "goal": "g",
            "outcome": "pass", "source": "review-g", "phases": {}}
    _write_state(tmp_path, _base_state(runs=[rich]))
    out = stop_closeout.run_stop(tmp_path, SESSION)
    runs = _runs(tmp_path)
    assert len(runs) == 1 and runs[0]["source"] == "review-g"   # untouched
    assert out == {}                                            # idempotent with Review-G


# --- self-gating -----------------------------------------------------------

def test_no_state_json_is_silent(tmp_path):
    (tmp_path / ".build-loop").mkdir()
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}


def test_no_build_loop_id_is_silent(tmp_path):
    st = _base_state()
    st["execution"].pop("build_loop_id")
    _write_state(tmp_path, st)
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    assert _runs(tmp_path) == []


def test_session_mismatch_is_silent(tmp_path):
    _write_state(tmp_path, _base_state(session="other-session"))
    assert stop_closeout.run_stop(tmp_path, SESSION) == {}
    assert _runs(tmp_path) == []


def test_no_session_id_fresh_heartbeat_records(tmp_path):
    st = _base_state()
    st["execution"]["current_session_id"] = ""
    st["execution"]["started_by_session_id"] = ""
    _write_state(tmp_path, st)
    stop_closeout.run_stop(tmp_path, "")          # no session id from host
    assert len(_runs(tmp_path)) == 1               # heartbeat fresh → recorded


def test_no_session_id_stale_heartbeat_is_silent(tmp_path):
    st = _base_state()
    st["execution"]["current_session_id"] = ""
    st["execution"]["started_by_session_id"] = ""
    st["execution"]["last_heartbeat_at"] = _iso(_now() - timedelta(minutes=stop_closeout._HEARTBEAT_FRESH_MINUTES + 30))
    _write_state(tmp_path, st)
    assert stop_closeout.run_stop(tmp_path, "") == {}
    assert _runs(tmp_path) == []


# --- crash-orphan sweep (session-start) ------------------------------------

def _orphan_state(minutes_stale=300, phase="execute"):
    st = _base_state(phase=phase)
    st["execution"]["last_heartbeat_at"] = _iso(_now() - timedelta(minutes=minutes_stale))
    return st


def test_sweep_records_crash_orphan_and_surfaces_marker(tmp_path):
    # SIGKILL/529: no Stop ever fired — run absent from runs[], heartbeat stale.
    _write_state(tmp_path, _orphan_state())
    out, _ = stop_closeout.run_session_start(tmp_path)
    runs = _runs(tmp_path)
    assert len(runs) == 1 and runs[0]["run_id"] == "bl-test-001"
    assert runs[0]["outcome"] == "partial"           # died mid-execute → honest
    assert "sessionstart-sweep" in runs[0]["manualInterventions"][0]["note"]
    # marker written by the sweep surfaces in the SAME session-start pass.
    assert "bl-test-001" in out["hookSpecificOutput"]["additionalContext"]


def test_sweep_skips_live_peer_run(tmp_path):
    # Fresh heartbeat = possibly a live concurrent session's run — never touch.
    _write_state(tmp_path, _orphan_state(minutes_stale=5))
    out, markers = stop_closeout.run_session_start(tmp_path)
    assert _runs(tmp_path) == [] and (out, markers) == ({}, [])


def test_sweep_skips_already_recorded_run(tmp_path):
    st = _orphan_state()
    st["runs"] = [{"run_id": "bl-test-001", "source": "review-g"}]
    _write_state(tmp_path, st)
    stop_closeout.run_session_start(tmp_path)
    assert len(_runs(tmp_path)) == 1                 # untouched


def test_sweep_idempotent_across_session_starts(tmp_path):
    _write_state(tmp_path, _orphan_state())
    out1, m1 = stop_closeout.run_session_start(tmp_path)
    stop_closeout._archive_markers(tmp_path, m1)
    out2, m2 = stop_closeout.run_session_start(tmp_path)
    assert len(_runs(tmp_path)) == 1                 # recorded once
    assert (out2, m2) == ({}, [])                    # surfaced once


def test_sweep_no_phase_records_blocked(tmp_path):
    # Run died before Phase 1 wrote phase (fresh-mint cleared it) → "blocked".
    st = _orphan_state()
    st.pop("phase", None)
    _write_state(tmp_path, st)
    stop_closeout.run_session_start(tmp_path)
    assert _runs(tmp_path)[0]["outcome"] == "fail"   # blocked → canonical fail


# --- session-start surfacing ----------------------------------------------

def test_session_start_surfaces_then_archives_after_emit(tmp_path):
    _write_state(tmp_path, _base_state())
    stop_closeout.run_stop(tmp_path, SESSION)          # writes the marker
    out, to_archive = stop_closeout.run_session_start(tmp_path)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "bl-test-001" in out["hookSpecificOutput"]["additionalContext"]
    # Emit-before-archive: the marker is still live until the caller archives it.
    assert (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").exists()
    stop_closeout._archive_markers(tmp_path, to_archive)
    assert not (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").exists()
    assert (tmp_path / ".build-loop" / "closeout-pending" / "surfaced" / "bl-test-001.md").exists()
    # second surface → nothing left.
    assert stop_closeout.run_session_start(tmp_path) == ({}, [])


def test_session_start_cli_archives_after_emit(tmp_path):
    _write_state(tmp_path, _base_state())
    stop_closeout.run_stop(tmp_path, SESSION)
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(tmp_path), "--mode", "session-start", "--hook"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert "bl-test-001" in payload["hookSpecificOutput"]["additionalContext"]
    assert (tmp_path / ".build-loop" / "closeout-pending" / "surfaced" / "bl-test-001.md").exists()


def test_session_start_no_markers_is_silent(tmp_path):
    assert stop_closeout.run_session_start(tmp_path) == ({}, [])


# --- f6: SessionStart surface of an OWED marker is explicitly actionable -----
def test_session_start_prompts_action_for_owed_marker(tmp_path):
    pending = tmp_path / ".build-loop" / "closeout-pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "bl-owed-1.md").write_text(
        "---\nrun_id: bl-owed-1\ncloseout_incomplete: true\nsource: stop_closeout\n---\n\n# owed\n"
    )
    out, to_archive = stop_closeout.run_session_start(tmp_path)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "bl-owed-1" in ctx
    assert "retrospective-synthesizer" in ctx           # names the owed agent
    assert "--run-id bl-owed-1" in ctx                  # actionable command
    assert "memory_writer.py" in ctx                    # names the memory-closeout step
    assert [p.name for p in to_archive] == ["bl-owed-1.md"]


def test_session_start_archives_complete_marker_without_nagging(tmp_path):
    """A closeout that already completed (closeout_incomplete: false) is archived
    silently — no owed-action prompt."""
    pending = tmp_path / ".build-loop" / "closeout-pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "bl-done-1.md").write_text(
        "---\nrun_id: bl-done-1\ncloseout_incomplete: false\nsource: stop_closeout\n---\n\n# done\n"
    )
    out, to_archive = stop_closeout.run_session_start(tmp_path)
    assert out == {}                                     # no prompt for a done closeout
    assert [p.name for p in to_archive] == ["bl-done-1.md"]  # still archived (no linger)


def test_marker_missing_flag_defaults_to_owed(tmp_path):
    """An older marker without the closeout_incomplete flag is treated as owed
    (safer than silently complete)."""
    pending = tmp_path / ".build-loop" / "closeout-pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "bl-legacy.md").write_text(
        "---\nrun_id: bl-legacy\nsource: stop_closeout\n---\n\n# legacy marker\n"
    )
    out, _ = stop_closeout.run_session_start(tmp_path)
    assert "bl-legacy" in out["hookSpecificOutput"]["additionalContext"]


# --- CLI smoke (exit 0 + valid JSON always) --------------------------------

def test_cli_stop_emits_valid_json_exit0(tmp_path):
    _write_state(tmp_path, _base_state())
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(tmp_path), "--mode", "stop",
         "--session-id", SESSION, "--hook"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    json.loads(res.stdout)            # parses


def test_cli_reads_session_id_from_stdin(tmp_path):
    _write_state(tmp_path, _base_state())
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(tmp_path), "--mode", "stop", "--hook"],
        input=json.dumps({"session_id": SESSION, "hook_event_name": "Stop"}),
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    assert len(_runs(tmp_path)) == 1   # session id taken from stdin → recorded


# --- C2: owed-judgment followup closes the WARN "parent owes it" loophole ---

def test_stakes_gated_run_writes_judgment_owed_followup(tmp_path):
    _write_state(tmp_path, _base_state(stakes_trigger=True))
    stop_closeout.run_stop(tmp_path, SESSION)
    fu = tmp_path / ".build-loop" / "followup" / "judgment-owed-bl-test-001.md"
    assert fu.exists(), "stakes-gated inline run must leave an owed-judgment followup"
    body = fu.read_text()
    assert "topic: judgment-owed" in body and "independent-auditor" in body


def test_no_stakes_run_writes_no_followup(tmp_path):
    _write_state(tmp_path, _base_state(stakes_trigger=False))
    stop_closeout.run_stop(tmp_path, SESSION)
    fu_dir = tmp_path / ".build-loop" / "followup"
    assert not fu_dir.exists() or not list(fu_dir.glob("judgment-owed-*.md"))


# --- EC-01 rca: marker verifies closeout artifacts + emits closeout_incomplete ---
def test_marker_flags_closeout_incomplete_when_artifacts_missing(tmp_path):
    _write_state(tmp_path, _base_state())
    stop_closeout.run_stop(tmp_path, SESSION)
    body = (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").read_text()
    assert "closeout_incomplete: true" in body
    assert "retro_present: false" in body
    assert "lessons_present: false" in body
    # both owed checkboxes are unchecked
    assert "- [ ] **retrospective-synthesizer**" in body
    assert "- [ ] **memory closeout**" in body


def test_marker_flags_closeout_complete_when_both_artifacts_present(tmp_path):
    _write_state(tmp_path, _base_state())
    bl = tmp_path / ".build-loop"
    today = _now().strftime("%Y-%m-%d")
    retro = bl / "retrospectives" / today
    retro.mkdir(parents=True, exist_ok=True)
    (retro / "bl-test-001.md").write_text("# retro\n")
    lessons = bl / "pending-lessons"
    lessons.mkdir(parents=True, exist_ok=True)
    (lessons / "lesson-1.md").write_text("# lesson\n")
    stop_closeout.run_stop(tmp_path, SESSION)
    body = (bl / "closeout-pending" / "bl-test-001.md").read_text()
    assert "closeout_incomplete: false" in body
    assert "retro_present: true" in body
    assert "lessons_present: true" in body
    assert "- [x] **retrospective-synthesizer**" in body
    assert "- [x] **memory closeout**" in body


def test_marker_partial_closeout_is_incomplete(tmp_path):
    """Only the retro present (lessons still owed) → still closeout_incomplete: true."""
    _write_state(tmp_path, _base_state())
    bl = tmp_path / ".build-loop"
    today = _now().strftime("%Y-%m-%d")
    retro = bl / "retrospectives" / today
    retro.mkdir(parents=True, exist_ok=True)
    (retro / "bl-test-001.md").write_text("# retro\n")
    stop_closeout.run_stop(tmp_path, SESSION)
    body = (bl / "closeout-pending" / "bl-test-001.md").read_text()
    assert "closeout_incomplete: true" in body
    assert "retro_present: true" in body
    assert "lessons_present: false" in body


# --- review f3: owed-judgment followup is removed once the debt clears ---
def test_judgment_followup_removed_when_debt_clears(tmp_path):
    (tmp_path / ".build-loop").mkdir(parents=True, exist_ok=True)
    decision = {"run_id": "bl-f3", "goal": "g", "outcome": "done"}
    warn = {"verdict": "warn", "stakes_gated": True, "stakes_reasons": ["riskSurfaceChange"],
            "findings": [{"layer": "independent-auditor"}], "missing_seats": []}
    p = stop_closeout._write_judgment_followup(tmp_path, decision, warn)
    assert p is not None and p.exists()
    # debt cleared → pass verdict must delete the stale followup (no phantom Phase-5 debt)
    ok = {"verdict": "pass", "stakes_gated": True, "stakes_reasons": ["riskSurfaceChange"],
          "findings": [], "missing_seats": []}
    assert stop_closeout._write_judgment_followup(tmp_path, decision, ok) is None
    assert not p.exists()


# --- Phase-6 Learn drafting (eager inline detector; fills the learn/pending
#     "not yet an automated detector pass" TODO) --------------------------------

def _pattern_runs(root_cause="widget-crash", n=3):
    """n prior runs sharing a root_cause → one cluster >= PATTERN_THRESHOLD (3)."""
    return [{"run_id": f"bl-seed-{i}", "root_cause": root_cause, "outcome": "done"} for i in range(n)]


def test_learn_drafting_owed_surfaces_recurring_pattern(tmp_path):
    # 3 prior runs share a root_cause → the deterministic detector clusters them,
    # so Learn drafting is owed (no experimental draft yet). This is the inline
    # path that previously left the learning loop dark until the user asked.
    _write_state(tmp_path, _base_state(runs=_pattern_runs()))
    stop_closeout.run_stop(tmp_path, SESSION)
    marker = (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").read_text()
    assert "learn_drafting_owed: true" in marker
    assert "[ ] **Phase-6 Learn drafting**" in marker
    assert "self-improve" in marker
    assert "closeout_incomplete: true" in marker


def test_learn_not_owed_below_run_floor(tmp_path):
    # Only the current run accrues (< 3) → Learn is still accruing, not owed.
    _write_state(tmp_path, _base_state(runs=[]))
    stop_closeout.run_stop(tmp_path, SESSION)
    marker = (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").read_text()
    assert "learn_drafting_owed: false" in marker
    assert "[x] **Phase-6 Learn drafting**" in marker


def test_learn_owed_clears_when_experimental_draft_present(tmp_path):
    # Same recurring pattern, but a draft already exists → owed clears (the
    # "handled" signal, mirroring the retro/lessons artifact checks).
    _write_state(tmp_path, _base_state(runs=_pattern_runs()))
    draft = tmp_path / ".build-loop" / "skills" / "experimental" / "some-draft"
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text("x")
    stop_closeout.run_stop(tmp_path, SESSION)
    marker = (tmp_path / ".build-loop" / "closeout-pending" / "bl-test-001.md").read_text()
    assert "learn_drafting_owed: false" in marker
    assert "draft(s) already present" in marker
