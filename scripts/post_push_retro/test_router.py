# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Router routes each tier + fallback correctly with injected side-effects
(acceptance #3, #4). No LLM, no real push, no real retro subprocess."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import config, router  # noqa: E402

CFG = config.DEFAULTS


def _ckpt_spy():
    calls = []
    return calls, (lambda repo, cov: calls.append(cov))


def _cov(commit_count=3, repos=1, files=None, rng="aaa..bbb"):
    return {"commit_count": commit_count, "repos_touched": repos,
            "files_changed": files or ["src/a.py"], "range_label": rng,
            "commits": ["a", "b"], "refs": []}


def _retro_ok(recs=0, issues=0, autos=0):
    return {"ok": True, "error": None, "output": {
        "enforce_candidates": ["e"] * recs,
        "meta": {"automation_candidate_count": autos, "issue_signal_count": issues},
    }}


# --- parse_retro_signals -----------------------------------------------------
def test_parse_signals_counts_enforce_and_autos():
    sig = router.parse_retro_signals(_retro_ok(recs=2, autos=1))
    assert sig["recommendation_count"] == 3


def test_parse_signals_failure_cluster_at_two_issues():
    assert router.parse_retro_signals(_retro_ok(issues=2))["failure_cluster"] is True
    assert router.parse_retro_signals(_retro_ok(issues=1))["failure_cluster"] is False


def test_parse_signals_none_on_failed_retro():
    assert router.parse_retro_signals({"ok": False}) is None


# --- trivial -----------------------------------------------------------------
def test_trivial_is_deterministic_only_and_advances_checkpoint(tmp_path):
    calls, ckpt = _ckpt_spy()
    d = router.route(tmp_path, _cov(commit_count=1), CFG, llm_available=False,
                     retro_result=_retro_ok(), checkpoint_fn=ckpt)
    assert d["tier"] == "trivial"
    assert d["action"] == "deterministic_only"
    assert len(calls) == 1  # checkpoint advanced


# --- medium ------------------------------------------------------------------
def test_medium_no_llm_arms_upgrade(tmp_path):
    calls, ckpt = _ckpt_spy()
    armed = {}
    d = router.route(tmp_path, _cov(commit_count=4), CFG, llm_available=False,
                     retro_result=_retro_ok(recs=2),
                     arm_fn=lambda repo, tier, cov: armed.setdefault("tier", tier) or Path("/x/upgrade.json"),
                     checkpoint_fn=ckpt)
    assert d["tier"] == "medium"
    assert d["action"] == "armed_upgrade"
    assert armed["tier"] == "medium"
    assert len(calls) == 1


def test_medium_with_llm_dispatches_judge(tmp_path):
    calls, ckpt = _ckpt_spy()
    d = router.route(tmp_path, _cov(commit_count=4), CFG, llm_available=True,
                     retro_result=_retro_ok(recs=2), checkpoint_fn=ckpt)
    assert d["tier"] == "medium"
    assert d["action"] == "dispatch"
    assert d["agents"] == ["judge"]


# --- substantial -------------------------------------------------------------
def test_substantial_with_llm_dispatches_full_pipeline(tmp_path):
    calls, ckpt = _ckpt_spy()
    d = router.route(tmp_path, _cov(commit_count=15), CFG, llm_available=True,
                     retro_result=_retro_ok(), checkpoint_fn=ckpt)
    assert d["tier"] == "substantial"
    assert d["agents"] == ["stage1", "stage2", "judge"]


def test_risk_surface_forces_substantial(tmp_path):
    calls, ckpt = _ckpt_spy()
    d = router.route(tmp_path, _cov(commit_count=1, files=["hooks/git/pre-push"]),
                     CFG, llm_available=True, retro_result=_retro_ok(), checkpoint_fn=ckpt)
    assert d["tier"] == "substantial"


# --- fallback paths (never silently skip) ------------------------------------
def test_retro_failure_routes_to_fallback(tmp_path):
    # THE falsifier: a failed retro must produce a fallback entry, never nothing.
    fb_calls = {}

    def fake_fb(repo, rng, tier, reason, **kw):
        fb_calls.update(dict(rng=rng, tier=tier, reason=reason))
        return {"filed": True, "witness": None}

    d = router.route(tmp_path, _cov(commit_count=5), CFG, llm_available=False,
                     retro_result={"ok": False, "error": "Fable unavailable", "output": None},
                     fallback_fn=fake_fb)
    assert d["action"] == "fallback"
    assert d["ran_deterministic"] is False
    assert d["filed"] is True
    assert "Fable unavailable" in fb_calls["reason"]


def _init_repo(repo):
    import subprocess
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "-C", str(repo), "init", "-q", "-b", "main"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.email", "t@e.com"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "T"])
    (repo / "x").write_text("1")
    subprocess.check_call(["git", "-C", str(repo), "add", "x"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", "c"])


def test_arm_upgrade_merges_not_overwrites(tmp_path):
    # auditor f1 (high): a second queued upgrade must UNION with the first, never
    # silently drop it (the checkpoint has already advanced past the first range).
    import json
    from post_push_retro import coverage as cov
    repo = tmp_path / "r"
    _init_repo(repo)
    router.arm_upgrade(repo, "medium",
                       {"commits": ["a", "b"], "range_label": "a..b"})
    router.arm_upgrade(repo, "substantial",
                       {"commits": ["b", "c", "d"], "range_label": "c..d"})
    up = json.loads((cov.retro_state_dir(repo) / "upgrade.json").read_text())
    assert set(up["commits"]) == {"a", "b", "c", "d"}     # union, nothing dropped
    assert up["tier"] == "substantial"                     # widened to strongest
    assert "a..b" in up["covered_ranges"] and "c..d" in up["covered_ranges"]


def test_arm_upgrade_preserves_earliest_armed_at(tmp_path):
    import json
    from post_push_retro import coverage as cov
    repo = tmp_path / "r"
    _init_repo(repo)
    router.arm_upgrade(repo, "medium", {"commits": ["a"], "range_label": "a..a"})
    first = json.loads((cov.retro_state_dir(repo) / "upgrade.json").read_text())["armed_at"]
    router.arm_upgrade(repo, "medium", {"commits": ["b"], "range_label": "b..b"})
    second = json.loads((cov.retro_state_dir(repo) / "upgrade.json").read_text())["armed_at"]
    assert first == second  # oldest wins => 24h staleness clock runs from oldest work


def test_budget_exceeded_routes_to_fallback_but_keeps_capture(tmp_path):
    calls, ckpt = _ckpt_spy()
    fb = {}

    def fake_fb(repo, rng, tier, reason, **kw):
        fb.update(dict(tier=tier, reason=reason))
        return {"filed": True, "witness": None}

    d = router.route(tmp_path, _cov(commit_count=12), CFG, llm_available=True,
                     retro_result=_retro_ok(), budget_exceeded=True,
                     fallback_fn=fake_fb, checkpoint_fn=ckpt)
    assert d["action"] == "fallback_budget"
    assert d["ran_deterministic"] is True  # deterministic capture retained
    assert len(calls) == 1                  # checkpoint still advanced
    assert "budget" in fb["reason"].lower()
