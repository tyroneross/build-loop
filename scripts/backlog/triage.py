# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""triage.py — deterministic product-impact classifier for descoped/deferred work.

Pure stdlib regex over the deferral text. NO LLM. Falsy-when-uncertain
(``product_impacting: False``) — the orchestrator may upgrade on judgment.

Public API:
    classify(deferral_text, context=None) ->
        {"product_impacting": bool, "impact": str | None, "rationale": str}

Heuristic shape:
    A deferral is product-impacting when its text mentions a user-facing surface
    (UI, page, button, error message, performance, cost, security, accessibility,
    data integrity, user, account, login, signup, dashboard, navigation, etc.)
    AND is NOT a pure-internal refactor/rename/test-only/doc-only signal.

    The internal-only suppression list ("rename internal helper", "doc typo",
    "test coverage", etc.) keeps the False rate honest. When both signals fire,
    the surface signal wins (a refactor of a user-facing component IS
    product-impacting).

The ``context`` dict is reserved for future expansion (e.g. file paths the
deferral references, screenshot detection). Unused today; kept on the
signature so future callers don't break.
"""
from __future__ import annotations

import re
from typing import Any

# Surface keywords — presence of any signals user-facing impact.
# Each tuple is (regex, one-line impact phrase). The first match wins for the
# impact phrase; the rationale lists every keyword that matched for transparency.
_SURFACE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(?:ui|interface|screen|view|component)\b", re.I),
     "ui",
     "user-facing UI surface"),
    (re.compile(r"\b(?:page|route|nav(?:igation)?|menu|sidebar|tab)\b", re.I),
     "navigation",
     "user navigation / page surface"),
    (re.compile(r"\b(?:button|link|control|toggle|switch|input|form|field)\b", re.I),
     "control",
     "user-facing control / form"),
    (re.compile(r"\b(?:error|error\s*message|warning|toast|notification|alert)\b", re.I),
     "error-message",
     "user-visible error / warning"),
    (re.compile(r"\b(?:performance|latency|slow|speed|lag|jank|fps)\b", re.I),
     "performance",
     "user-perceived performance"),
    (re.compile(r"\b(?:cost|pricing|billing|charge|spend)\b", re.I),
     "cost",
     "user-visible cost / billing"),
    (re.compile(r"\b(?:security|auth(?:entication)?|authoriz(?:e|ation)|permission|csrf|xss)\b", re.I),
     "security",
     "security / auth surface"),
    (re.compile(r"\b(?:accessibility|a11y|aria|screen[\s-]?reader|contrast|keyboard\s+nav)\b", re.I),
     "accessibility",
     "accessibility / a11y"),
    (re.compile(r"\b(?:data\s+integrity|lost\s+data|corrupt(?:ion)?|stale\s+data|wrong\s+(?:value|number|amount))\b", re.I),
     "data-integrity",
     "data integrity / correctness"),
    (re.compile(r"\b(?:user|account|profile|signin|signup|sign-in|sign-up|login|logout|onboarding)\b", re.I),
     "user-flow",
     "user account / sign-in flow"),
    (re.compile(r"\b(?:dashboard|chart|graph|table|list|grid)\b", re.I),
     "data-view",
     "user-facing data display"),
    (re.compile(r"\b(?:checkout|cart|payment|purchase|order)\b", re.I),
     "transactional",
     "transactional / checkout surface"),
    (re.compile(r"\b(?:save|delete|create|edit|update)\s+(?:button|action|flow|fails?|broken)\b", re.I),
     "primary-action",
     "primary user action"),
    (re.compile(r"\b(?:broken|doesn['’]?t\s+work|not\s+working|fails?\s+to|hangs?|crashes?)\b", re.I),
     "broken-behavior",
     "user-observed broken behavior"),
    (re.compile(r"\b(?:loads?|loading|fetch(?:ing)?|render(?:ing)?)\s+(?:incorrectly|wrong|too\s+slow|fails?)\b", re.I),
     "render-failure",
     "user-visible render / load failure"),
]

# Internal-only suppression — when the entire deferral reads as pure-internal,
# treat as NOT product-impacting even if a surface keyword appears in passing.
_INTERNAL_ONLY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*(?:rename|refactor)\s+(?:internal|private|helper|util(?:ity)?|module)\b", re.I),
    re.compile(r"^\s*(?:doc(?:ument|umentation)?|comment|typo|wording|prose)\b", re.I),
    re.compile(r"^\s*(?:test\s+coverage|add\s+(?:a\s+)?test|unit\s+test|fixture)\b", re.I),
    re.compile(r"^\s*(?:lint|format(?:ting)?|tidy(?:ing)?|cleanup|whitespace|imports?)\b", re.I),
    re.compile(r"^\s*(?:rebuild|regenerate)\s+(?:cache|lockfile|node_modules)\b", re.I),
]


def _is_internal_only(text: str) -> bool:
    """True when the deferral reads as pure-internal and no real user-facing
    keyword overrides it. Used as a tiebreaker."""
    return any(p.search(text) for p in _INTERNAL_ONLY_PATTERNS)


def classify(deferral_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify a deferral for product impact.

    Args:
        deferral_text: the descoped/follow-up item text (a sentence or two).
        context:       reserved for future use (file paths, screenshot flags).

    Returns:
        {
            "product_impacting": bool,
            "impact":            str | None,  # one-line user-facing phrase
            "rationale":         str,         # which signals matched
        }

    Behavior:
        - Empty / whitespace-only text → product_impacting=False.
        - Pure-internal text with no surface keyword → False.
        - Any surface keyword match → True, impact set to the first-match phrase,
          rationale lists every keyword that matched.
        - Pure-internal text that ALSO contains a surface keyword → True (the
          surface keyword wins; renaming a user-facing component IS impacting).
    """
    text = (deferral_text or "").strip()
    if not text:
        return {"product_impacting": False, "impact": None,
                "rationale": "empty text"}

    matches: list[tuple[str, str]] = []
    first_phrase: str | None = None
    for pat, key, phrase in _SURFACE_PATTERNS:
        if pat.search(text):
            matches.append((key, phrase))
            if first_phrase is None:
                first_phrase = phrase

    if not matches:
        # No surface keyword anywhere → not product-impacting.
        return {"product_impacting": False, "impact": None,
                "rationale": "no user-facing surface keywords matched"}

    # If the leading verb is pure-internal AND no broken-behavior signal fired,
    # respect the internal framing (e.g. "rename internal helper that handles UI").
    if _is_internal_only(text) and not any(
        k in ("broken-behavior", "render-failure", "primary-action", "data-integrity")
        for k, _ in matches
    ):
        return {"product_impacting": False, "impact": None,
                "rationale": f"internal-only framing despite surface mention: {matches[0][0]}"}

    keys = ", ".join(sorted({k for k, _ in matches}))
    return {
        "product_impacting": True,
        "impact": first_phrase,
        "rationale": f"surface signals matched: {keys}",
    }
