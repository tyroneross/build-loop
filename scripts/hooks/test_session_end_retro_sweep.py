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
