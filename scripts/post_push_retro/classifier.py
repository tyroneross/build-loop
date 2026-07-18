# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Scope classifier: map a covered delta to a retro tier.

``classify()`` is a PURE function over a signals dict so it is unit-tested on
fixture deltas with no git and no LLM.

Ordering (retro-FIRST, classify-second): the deterministic retro runs BEFORE
classification, so the semantic signals (recommendation_count / p0 /
failure_cluster) are populated from its output when available; delta signals
(commits / repos / files / risk-surface) come from git and are always present.
This ordering fix (plan-critic, adopted) is what makes the MEDIUM tier reachable
— classifying before the retro ran would leave recommendation_count at 0 and
collapse every mid-size push to trivial.

Tiers:
  * trivial     — single-commit / docs-only / config-only with no recommendations
  * medium      — emits actionable recommendations but is not large/risky
  * substantial — >= substantial_repos repos, OR >= substantial_commits commits,
                  OR a risk-surface change, OR a P0 recommendation, OR a
                  >= 2-instance failure cluster
"""
from __future__ import annotations

import fnmatch
from typing import Any

TIER_TRIVIAL = "trivial"
TIER_MEDIUM = "medium"
TIER_SUBSTANTIAL = "substantial"


def _matches_any(path: str, globs: list[str]) -> bool:
    p = path.lstrip("./")
    for g in globs:
        if fnmatch.fnmatch(p, g) or fnmatch.fnmatch(path, g):
            return True
        # allow bare-name globs like "CHANGELOG*" to match a basename anywhere
        base = p.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(base, g):
            return True
    return False


def extract_signals(
    coverage: dict[str, Any],
    retro_output: dict[str, Any] | None,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    """Build the signals dict the pure classifier consumes.

    ``retro_output`` is the parsed output of the deterministic retro (or None
    when unavailable, e.g. an ad-hoc push with no transcript). Semantic signals
    default to 0/False when absent — a robust degrade to delta-driven tiers."""
    files = coverage.get("files_changed", []) or []
    risk_globs = thresholds.get("risk_surface_globs", [])
    docs_globs = thresholds.get("docs_config_globs", [])

    risk_hit = any(_matches_any(f, risk_globs) for f in files)
    docs_only = bool(files) and all(_matches_any(f, docs_globs) for f in files)

    ro = retro_output or {}
    rec_count = int(ro.get("recommendation_count", 0) or 0)
    p0 = bool(ro.get("p0_recommendation", False))
    failure_cluster = bool(ro.get("failure_cluster", False))

    return {
        "commit_count": int(coverage.get("commit_count", 0) or 0),
        "repos_touched": int(coverage.get("repos_touched", 1) or 1),
        "files_changed": files,
        "docs_config_only": docs_only,
        "risk_surface_hit": risk_hit,
        "recommendation_count": rec_count,
        "p0_recommendation": p0,
        "failure_cluster": failure_cluster,
    }


def classify(signals: dict[str, Any], thresholds: dict[str, Any]) -> str:
    """Pure tier decision. Substantial (never-miss) is checked first, then the
    cheap trivial default, then medium; anything unclassified defaults to the
    cheapest tier (trivial) so cost is bounded."""
    sub_commits = int(thresholds.get("substantial_commits", 10))
    sub_repos = int(thresholds.get("substantial_repos", 2))

    commit_count = int(signals.get("commit_count", 0) or 0)
    repos = int(signals.get("repos_touched", 1) or 1)

    # 1. SUBSTANTIAL — the "must not miss important work" guarantees.
    if (
        repos >= sub_repos
        or commit_count >= sub_commits
        or signals.get("risk_surface_hit")
        or signals.get("p0_recommendation")
        or signals.get("failure_cluster")
    ):
        return TIER_SUBSTANTIAL

    # 2. TRIVIAL — cheapest path; never spends Fable.
    if commit_count <= 1:
        return TIER_TRIVIAL
    if (
        signals.get("docs_config_only")
        and not signals.get("risk_surface_hit")
        and int(signals.get("recommendation_count", 0) or 0) == 0
    ):
        return TIER_TRIVIAL

    # 3. MEDIUM — emits actionable recommendations but is not large/risky.
    if int(signals.get("recommendation_count", 0) or 0) > 0:
        return TIER_MEDIUM

    # 4. Default: cheapest.
    return TIER_TRIVIAL
