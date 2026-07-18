# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Classifier routes each tier correctly on fixture deltas (acceptance #3)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import config, classifier  # noqa: E402

TH = config.DEFAULTS["thresholds"]


def _sig(**over):
    base = {
        "commit_count": 3,
        "repos_touched": 1,
        "files_changed": ["src/a.py"],
        "docs_config_only": False,
        "risk_surface_hit": False,
        "recommendation_count": 0,
        "p0_recommendation": False,
        "failure_cluster": False,
    }
    base.update(over)
    return base


# --- trivial -----------------------------------------------------------------
def test_single_commit_is_trivial():
    assert classifier.classify(_sig(commit_count=1), TH) == classifier.TIER_TRIVIAL


def test_docs_only_no_recs_is_trivial():
    sig = _sig(commit_count=4, files_changed=["docs/x.md", "CHANGELOG.md"],
               docs_config_only=True, recommendation_count=0)
    assert classifier.classify(sig, TH) == classifier.TIER_TRIVIAL


def test_midsize_no_signal_defaults_trivial():
    # 3 commits, code, but no recommendations and no risk => cheapest default.
    assert classifier.classify(_sig(commit_count=3), TH) == classifier.TIER_TRIVIAL


# --- medium ------------------------------------------------------------------
def test_recommendations_make_it_medium():
    assert classifier.classify(_sig(commit_count=4, recommendation_count=2), TH) \
        == classifier.TIER_MEDIUM


def test_docs_only_but_with_recs_is_medium():
    sig = _sig(commit_count=4, files_changed=["docs/x.md"], docs_config_only=True,
               recommendation_count=1)
    assert classifier.classify(sig, TH) == classifier.TIER_MEDIUM


# --- substantial -------------------------------------------------------------
def test_many_commits_is_substantial():
    assert classifier.classify(_sig(commit_count=12), TH) == classifier.TIER_SUBSTANTIAL


def test_two_repos_is_substantial():
    assert classifier.classify(_sig(repos_touched=2), TH) == classifier.TIER_SUBSTANTIAL


def test_risk_surface_forces_substantial_even_when_small():
    # single-commit risk-surface change still escalates (never-miss guarantee).
    assert classifier.classify(_sig(commit_count=1, risk_surface_hit=True), TH) \
        == classifier.TIER_SUBSTANTIAL


def test_p0_recommendation_is_substantial():
    assert classifier.classify(_sig(commit_count=2, p0_recommendation=True), TH) \
        == classifier.TIER_SUBSTANTIAL


def test_failure_cluster_is_substantial():
    assert classifier.classify(_sig(commit_count=2, failure_cluster=True), TH) \
        == classifier.TIER_SUBSTANTIAL


# --- signal extraction (risk-glob + docs-glob wiring) ------------------------
def test_extract_signals_detects_risk_surface():
    cov = {"commit_count": 2, "repos_touched": 1,
           "files_changed": ["scripts/auth_login.py"]}
    sig = classifier.extract_signals(cov, None, TH)
    assert sig["risk_surface_hit"] is True
    assert classifier.classify(sig, TH) == classifier.TIER_SUBSTANTIAL


def test_extract_signals_hooks_glob_is_risk():
    cov = {"commit_count": 1, "repos_touched": 1, "files_changed": ["hooks/git/pre-push"]}
    sig = classifier.extract_signals(cov, None, TH)
    assert sig["risk_surface_hit"] is True


def test_extract_signals_sql_migration_is_risk():
    cov = {"commit_count": 3, "repos_touched": 1,
           "files_changed": ["db/0007_add_col.sql"]}
    sig = classifier.extract_signals(cov, None, TH)
    assert sig["risk_surface_hit"] is True


def test_extract_signals_docs_only_true_when_all_docs():
    cov = {"commit_count": 3, "repos_touched": 1,
           "files_changed": ["README.md", "docs/a.md", "config.yaml"]}
    sig = classifier.extract_signals(cov, None, TH)
    assert sig["docs_config_only"] is True
    assert sig["risk_surface_hit"] is False


def test_extract_signals_docs_only_false_when_mixed():
    cov = {"commit_count": 3, "repos_touched": 1,
           "files_changed": ["README.md", "src/app.py"]}
    sig = classifier.extract_signals(cov, None, TH)
    assert sig["docs_config_only"] is False


def test_extract_signals_semantic_from_retro_output():
    cov = {"commit_count": 4, "repos_touched": 1, "files_changed": ["src/a.py"]}
    ro = {"recommendation_count": 3, "p0_recommendation": False, "failure_cluster": False}
    sig = classifier.extract_signals(cov, ro, TH)
    assert sig["recommendation_count"] == 3
    assert classifier.classify(sig, TH) == classifier.TIER_MEDIUM


def test_extract_signals_none_retro_degrades_to_zero():
    cov = {"commit_count": 4, "repos_touched": 1, "files_changed": ["src/a.py"]}
    sig = classifier.extract_signals(cov, None, TH)
    assert sig["recommendation_count"] == 0
    assert sig["p0_recommendation"] is False
