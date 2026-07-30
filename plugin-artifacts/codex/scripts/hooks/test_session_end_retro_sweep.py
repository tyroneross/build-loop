"""Tests for the deterministic core of session_end_retro_sweep.

Covers the two gates that decide whether the auto-retro fires and what it
surfaces: the non-trivial session gate and the skill/lesson split (skill only
when a workflow REPEATS enough to save time+tokens; lessons only when n>=2).
"""
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sweep", str(Path(__file__).with_name("session_end_retro_sweep.py")))
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)


def _transcript(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return p


def _tool_use_line(cmd=""):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}


def test_trivial_session_is_gated(tmp_path):
    t = _transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
    assert sweep.session_is_trivial(t) is True


def test_many_tool_uses_is_non_trivial(tmp_path):
    t = _transcript(tmp_path, [_tool_use_line() for _ in range(sweep.MIN_TOOL_USES + 1)])
    assert sweep.session_is_trivial(t) is False


def test_a_commit_makes_session_non_trivial(tmp_path):
    t = _transcript(tmp_path, [_tool_use_line("git commit -m x")])
    assert sweep.session_is_trivial(t) is False


def test_unreadable_transcript_is_treated_trivial(tmp_path):
    assert sweep.session_is_trivial(tmp_path / "nope.jsonl") is True


def test_repeated_workflow_becomes_skill_proposal():
    # non-generic: includes a step outside the core toolset (a Skill invocation)
    cands = [{"kind": "skill_or_workflow_candidate", "shape": "repeated_tool_sequence",
              "session_count": sweep.SKILL_MIN_SESSIONS,
              "sequence": ["Skill:build-loop", "Bash:command"]}]
    skills, lessons = sweep.split_candidates(cands)
    assert len(skills) == 1 and not lessons


def test_generic_core_tool_sequence_is_dropped():
    # the universal edit-test loop (core tools only) is noise at ANY count —
    # this exact shape re-marked 6 consecutive sessions before the filter
    cands = [{"kind": "skill_or_workflow_candidate", "shape": "repeated_tool_sequence",
              "session_count": 24,
              "sequence": ["Edit:replace_all", "Bash:command", "Bash:command"]}]
    skills, lessons = sweep.split_candidates(cands)
    assert not skills and not lessons


def test_manual_command_ritual_is_not_gated_by_generic_filter():
    # rituals carry real command content in other fields; the generic-sequence
    # filter must not touch them even when a sequence-ish field is absent
    cands = [{"kind": "skill_or_workflow_candidate", "shape": "manual_command_ritual",
              "session_count": sweep.SKILL_MIN_SESSIONS}]
    skills, lessons = sweep.split_candidates(cands)
    assert len(skills) == 1 and not lessons


def test_workflow_below_skill_threshold_is_not_a_skill():
    # recurs, but not enough sessions to justify a skill (no token/time payoff)
    cands = [{"kind": "skill_or_workflow_candidate", "shape": "repeated_tool_sequence",
              "session_count": sweep.SKILL_MIN_SESSIONS - 1}]
    skills, lessons = sweep.split_candidates(cands)
    assert not skills  # lands as a lesson only if it still clears LESSON_MIN_SESSIONS


def test_n1_candidate_is_dropped():
    cands = [{"kind": "user_correction", "shape": "correction", "session_count": 1}]
    skills, lessons = sweep.split_candidates(cands)
    assert not skills and not lessons  # never n=1


def test_recurring_lesson_is_kept():
    cands = [{"kind": "user_correction", "shape": "correction",
              "session_count": sweep.LESSON_MIN_SESSIONS}]
    skills, lessons = sweep.split_candidates(cands)
    assert not skills and len(lessons) == 1


# --- session-retro auto-fire helpers (rich retro on non-run sessions) -------

def test_resolve_project_cwd_reads_cwd_field(tmp_path):
    t = _transcript(tmp_path, [
        {"type": "user", "cwd": "/Users/x/dev/myrepo",
         "message": {"role": "user", "content": "hi"}}])
    assert sweep.resolve_project_cwd(t) == Path("/Users/x/dev/myrepo")


def test_resolve_project_cwd_none_when_absent(tmp_path):
    t = _transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
    assert sweep.resolve_project_cwd(t) is None


def test_is_project_dir_true_for_build_loop_dir(tmp_path):
    (tmp_path / ".build-loop").mkdir()
    assert sweep.is_project_dir(tmp_path) is True


def test_is_project_dir_true_for_git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    assert sweep.is_project_dir(tmp_path) is True


def test_is_project_dir_false_for_bare_dir(tmp_path):
    assert sweep.is_project_dir(tmp_path) is False


# --- EC-01 coord: miner digest ALSO lands in the workdir learn lane ----------
def test_write_workdir_digest_writes_to_learn_pending(tmp_path):
    (tmp_path / ".build-loop").mkdir()
    payload = {"skills": [], "lessons": [{"rationale": "x"}], "window": "1d"}
    out = sweep.write_workdir_digest(tmp_path, payload, "20260708-120000")
    assert out is not None
    expected = tmp_path / ".build-loop" / "learn" / "pending" / "20260708-120000-digest.json"
    assert out == expected and expected.is_file()
    assert json.loads(expected.read_text())["lessons"][0]["rationale"] == "x"


def test_write_workdir_digest_skips_non_project_dir(tmp_path):
    # A bare (non-project) cwd → no learn lane written (fail-open, no scatter).
    assert sweep.write_workdir_digest(tmp_path, {"lessons": []}, "20260708-120000") is None
    assert not (tmp_path / ".build-loop" / "learn").exists()


def test_write_workdir_digest_none_cwd_is_noop(tmp_path):
    assert sweep.write_workdir_digest(None, {"lessons": []}, "20260708-120000") is None


def test_run_session_retro_shells_to_retrospective(tmp_path, monkeypatch):
    calls = {}
    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        calls["cwd"] = kw.get("cwd")
        class R: pass
        return R()
    monkeypatch.setattr(sweep.subprocess, "run", fake_run)
    t = tmp_path / "abc123.jsonl"
    t.write_text("{}", encoding="utf-8")
    sweep.run_session_retro(tmp_path, t, tmp_path / "proj")
    assert "retrospective" in calls["cmd"]
    assert "--run-id" in calls["cmd"]
    assert "session-abc123" in calls["cmd"]


def test_run_session_retro_fail_open_on_subprocess_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(sweep.subprocess, "run", boom)
    t = tmp_path / "x.jsonl"; t.write_text("{}", encoding="utf-8")
    # must not raise
    sweep.run_session_retro(tmp_path, t, tmp_path)


# --- Fix 1: don't duplicate a formal run's retro (fire only on non-run sessions)

def test_formal_run_retro_exists_true_when_run_retro_written_today(tmp_path):
    from datetime import datetime, timezone
    bl = tmp_path / ".build-loop"; bl.mkdir()
    (bl / "state.json").write_text(json.dumps(
        {"execution": {"build_loop_id": "bl-run-123"}}), encoding="utf-8")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rdir = bl / "retrospectives" / today; rdir.mkdir(parents=True)
    (rdir / "bl-run-123.md").write_text("# retro", encoding="utf-8")
    assert sweep.formal_run_retro_exists(tmp_path) is True


def test_formal_run_retro_exists_false_for_runless_session(tmp_path):
    (tmp_path / ".build-loop").mkdir()
    # no state.json → run-less session → fire
    assert sweep.formal_run_retro_exists(tmp_path) is False


def test_formal_run_retro_exists_false_when_run_retro_is_stale(tmp_path):
    bl = tmp_path / ".build-loop"; bl.mkdir()
    (bl / "state.json").write_text(json.dumps(
        {"runs": [{"run_id": "bl-old"}]}), encoding="utf-8")
    # retro exists but under an OLD date dir → today's session still fires
    old = bl / "retrospectives" / "2020-01-01"; old.mkdir(parents=True)
    (old / "bl-old.md").write_text("# retro", encoding="utf-8")
    assert sweep.formal_run_retro_exists(tmp_path) is False


# --- Fix 3: the plugin ships the SessionEnd wiring (portability, not host-only)

def test_plugin_hooks_json_wires_sessionend_sweep():
    from pathlib import Path as _P
    hooks = json.loads((_P(__file__).parents[2] / "hooks" / "hooks.json").read_text())
    se = hooks.get("hooks", {}).get("SessionEnd", [])
    cmds = " ".join(h.get("command", "") for g in se for h in g.get("hooks", []))
    assert "session_end_retro_sweep.py" in cmds, "SessionEnd must invoke the sweep"
    assert "transcript_path" in cmds, "must pass transcript_path from stdin"
