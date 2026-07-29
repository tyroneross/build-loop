"""Mode-resolution + plan-schema contract tests for per-commit dispatch.

Pure stdlib. The dispatcher (skill body, run by the LLM) resolves explicit
plain-language per-commit or single-orchestrator intent, reads
`selfRecursive.enabled` from `.build-loop/state.json`, and decides which
dispatch shape to use. This file locks the resolution table and the
`per-commit-plan.json` schema down so either side of the contract (dispatcher
or per-commit orchestrator) can be refactored without silently drifting.

Covers: explicit-intent × selfRecursive matrix, conflicting-intent user error,
absent / empty selfRecursive state, JSON round-trip, missing required fields,
unknown `depends_on` references.
"""
from __future__ import annotations

import json

import pytest


# ---------- helpers under test ----------------------------------------------

REQUIRED_PLAN_KEYS = ("run_id", "commits", "branch", "from_branch")
REQUIRED_COMMIT_KEYS = ("id", "subject", "scope", "files_planned", "spec", "depends_on")


def resolve_mode(per_commit_intent, single_orchestrator_intent, self_recursive_state):
    """Return the dispatch mode envelope for a build invocation.

    Args:
      per_commit_intent: True when the goal asks for commit-by-commit execution.
      single_orchestrator_intent: True when the goal asks for one orchestrator.
      self_recursive_state: dict from state.json's `selfRecursive` block, or None.

    Returns:
      dict with keys `mode`, `mode_source`, `error`. `mode` is None on user error.
    """
    if per_commit_intent and single_orchestrator_intent:
        return {
            "mode": None,
            "mode_source": "user_error",
            "error": "conflicting execution requests: per-commit and single-orchestrator",
        }
    if per_commit_intent:
        return {"mode": "per-commit", "mode_source": "explicit_intent", "error": None}
    if single_orchestrator_intent:
        return {"mode": "single-orchestrator", "mode_source": "opt_out", "error": None}
    if isinstance(self_recursive_state, dict) and self_recursive_state.get("enabled") is True:
        return {"mode": "per-commit", "mode_source": "self_recursive_default", "error": None}
    return {"mode": "single-orchestrator", "mode_source": "default", "error": None}


def _validate_commit_shape(commit, seen_ids):
    if not isinstance(commit, dict):
        raise ValueError("each commit must be a dict")
    missing = next((key for key in REQUIRED_COMMIT_KEYS if key not in commit), None)
    if missing:
        raise ValueError(f"commit missing required field: {missing}")
    if commit["id"] in seen_ids:
        raise ValueError(f"duplicate commit id: {commit['id']}")
    seen_ids.add(commit["id"])


def validate_plan(plan):
    """Raise ValueError if `plan` is not a well-formed per-commit-plan.json dict."""
    if not isinstance(plan, dict):
        raise ValueError("plan must be a dict")
    missing = next((key for key in REQUIRED_PLAN_KEYS if key not in plan), None)
    if missing:
        raise ValueError(f"plan missing required field: {missing}")
    if not isinstance(plan["commits"], list):
        raise ValueError("plan.commits must be a list")
    seen_ids = set()
    for commit in plan["commits"]:
        _validate_commit_shape(commit, seen_ids)
    for commit in plan["commits"]:
        unknown = next((dep for dep in commit["depends_on"] if dep not in seen_ids), None)
        if unknown:
            raise ValueError(f"commit {commit['id']} depends_on unknown id: {unknown}")


# ---------- mode-resolution matrix ------------------------------------------

@pytest.mark.parametrize(
    "per_commit,no_per_commit,self_rec,expected_mode,expected_source",
    [
        # explicit per-commit intent wins regardless of selfRecursive
        (True, False, {"enabled": True}, "per-commit", "explicit_intent"),
        (True, False, {"enabled": False}, "per-commit", "explicit_intent"),
        # explicit single-orchestrator intent wins regardless of selfRecursive
        (False, True, {"enabled": True}, "single-orchestrator", "opt_out"),
        (False, True, {"enabled": False}, "single-orchestrator", "opt_out"),
        # neither flag — selfRecursive decides
        (False, False, {"enabled": True}, "per-commit", "self_recursive_default"),
        (False, False, {"enabled": False}, "single-orchestrator", "default"),
        # neither flag, selfRecursive entirely absent (state.json had no field)
        (False, False, None, "single-orchestrator", "default"),
        # neither flag, selfRecursive present but missing the `enabled` key
        (False, False, {}, "single-orchestrator", "default"),
    ],
)
def test_mode_resolution_matrix(per_commit, no_per_commit, self_rec, expected_mode, expected_source):
    result = resolve_mode(per_commit, no_per_commit, self_rec)
    assert result["mode"] == expected_mode
    assert result["mode_source"] == expected_source
    assert result["error"] is None


def test_conflicting_intents_return_user_error():
    result = resolve_mode(True, True, {"enabled": True})
    assert result["mode"] is None
    assert result["mode_source"] == "user_error"
    assert "conflicting execution requests" in result["error"]
    assert "per-commit" in result["error"]
    assert "single-orchestrator" in result["error"]


# ---------- per-commit-plan.json schema -------------------------------------

def _sample_plan():
    return {
        "run_id": "run_20260506T090000Z_abc123",
        "branch": "feat/per-commit-example",
        "from_branch": "main",
        "commits": [
            {
                "id": "c1",
                "subject": "feat(scripts): add foo helper",
                "scope": "scripts",
                "files_planned": ["scripts/foo.py", "tests/test_foo.py"],
                "spec": "Implement foo().",
                "depends_on": [],
            },
            {
                "id": "c2",
                "subject": "feat(scripts): wire foo into bar",
                "scope": "scripts",
                "files_planned": ["scripts/bar.py"],
                "spec": "Call foo() from bar()'s entrypoint.",
                "depends_on": ["c1"],
            },
        ],
    }


def test_plan_round_trips_through_json():
    plan = _sample_plan()
    serialized = json.dumps(plan)
    restored = json.loads(serialized)
    validate_plan(restored)
    for key in REQUIRED_PLAN_KEYS:
        assert key in restored
    for commit in restored["commits"]:
        for key in REQUIRED_COMMIT_KEYS:
            assert key in commit


def test_plan_missing_run_id_raises_clear_error():
    plan = _sample_plan()
    del plan["run_id"]
    with pytest.raises(ValueError) as excinfo:
        validate_plan(plan)
    assert "run_id" in str(excinfo.value)


def test_plan_unknown_depends_on_id_flagged():
    plan = _sample_plan()
    plan["commits"][1]["depends_on"] = ["c-does-not-exist"]
    with pytest.raises(ValueError) as excinfo:
        validate_plan(plan)
    msg = str(excinfo.value)
    assert "c2" in msg
    assert "c-does-not-exist" in msg
