#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""decision_surface — the shared core behind every decision-surface variant.

One engine, several surfaces. A "decision surface" is any page that shows the
user a set of calls and captures a ruling on each. The KIND of call differs —
already made, blocking work, awaiting triage — and each kind wants a different
card shape, sort order, and default action. None of them wants a different
engine.

This module owns the variant registry and the spec projection. It deliberately
mirrors `dashboard_build.py`'s ARCHETYPES pattern: each variant is documented by
the QUESTION IT ANSWERS, because that is what an authoring LLM chooses on. A new
variant is a new dict entry, never a fork of the core.

The other two shared pieces are NOT reimplemented here; they are pointed at, so
there is exactly one copy of each:

  * Interactive page + save/self-publish plumbing
      skills/decision-queue/assets/template.html   (Claude-only; copy to
      scratchpad, edit the CONTENT ZONE only, never the plumbing)
  * Durable persistence
      scripts/write_decision/__main__.py           (file + INDEX + events.jsonl
      + DB; the same writer auto-decision-capture uses)

Stdlib only. No network.
"""

from __future__ import annotations

from typing import Any

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# The variant registry.
#
# `question` is the selection key: an authoring agent picks the variant whose
# question matches what the user is actually asking. `blocking` is the hard
# behavioural axis and must never be softened to make a surface feel tidier —
# it decides whether work stops.

VARIANTS: dict[str, dict[str, Any]] = {
    "silent-assumptions": {
        "question": "What did you decide without me?",
        "skill": "skills/silent-assumptions/SKILL.md",
        "blocking": False,
        # Rows carry a default that was ALREADY APPLIED. That is the structural
        # difference from every blocking variant: the work is done, and a
        # ruling reverses it rather than releasing it.
        "default_applied": True,
        "sort": "consequence if wrong, then area",
        "rank_field": "leverage",
        "priority_label": "High leverage, not yet ruled on",
        "priority_when": {"field": "needs_you", "equals": True},
        "priority_empty": "Every high-leverage call has your ruling.",
        "status_true": "Ruled",
        "status_false": "Not ruled",
        "dashboard_archetype": "queue",
        "detail": ["leverage", "what_i_did", "why_and_cost", "consequence",
                   "evidence", "options", "my_call", "your_call", "note"],
        "group_by": "area",
        "group_label": "Area",
    },
    "decision-queue": {
        "question": "What is waiting on me?",
        "skill": "skills/decision-queue/SKILL.md",
        "blocking": True,
        "default_applied": False,
        "sort": "urgency, then age",
        "rank_field": "priority",
        "priority_label": "Blocking work now",
        "priority_when": {"field": "blocked", "equals": True},
        "priority_empty": "Nothing is blocked on you.",
        "status_true": "Answered",
        "status_false": "Open",
        "dashboard_archetype": "queue",
        "detail": ["decision", "why", "impact", "options", "recommendation",
                   "selected", "comment"],
        "group_by": "repo",
        "group_label": "Repo",
        # decision-queue currently renders through its own tested template and
        # does NOT yet call this module. See "Adoption status" in the docstring
        # of `spec_for` and the follow-up backlog item.
        "adopted": False,
    },
    "findings-triage": {
        "question": "Which of these findings do you want acted on?",
        "skill": "skills/auto-finding-capture/SKILL.md",
        "blocking": False,
        "default_applied": False,
        "sort": "severity, then blast radius",
        "rank_field": "severity",
        "priority_label": "Critical and high, undispositioned",
        "priority_when": {"field": "needs_you", "equals": True},
        "priority_empty": "Every critical finding has a disposition.",
        "status_true": "Dispositioned",
        "status_false": "Open",
        "dashboard_archetype": "queue",
        "detail": ["severity", "what_happened", "impact", "recommendation",
                   "evidence", "disposition"],
        "group_by": "area",
        "group_label": "Area",
        "adopted": False,
    },
}


class VariantError(Exception):
    """The variant cannot produce a correct surface. Fail closed."""


def get(variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise VariantError(
            f"unknown variant {variant!r}; choose one of: {', '.join(VARIANTS)}")
    return VARIANTS[variant]


def describe() -> list[dict[str, str]]:
    """The selection table an authoring agent reads to choose a variant."""
    return [
        {
            "variant": k,
            "answers": v["question"],
            "blocking": "yes — work has stopped" if v["blocking"] else "no — work continued",
            "skill": v["skill"],
        }
        for k, v in VARIANTS.items()
    ]


def spec_for(
    variant: str,
    *,
    title: str,
    data_ref: dict[str, str] | None = None,
    footer: str = "",
    label_field: str = "label",
) -> dict[str, Any]:
    """Project a variant onto a `dashboard_build.py` spec.

    Adoption status, stated rather than implied: `silent-assumptions` renders
    through this path today. `decision-queue` and `findings-triage` are
    registered here so the family is one table with one vocabulary, but they
    still render through their own surfaces and carry `adopted: False`.
    Registering them costs nothing and makes the eventual adoption a call to
    this function rather than a fork of it.
    """
    v = get(variant)
    return {
        "schema": "ibr.dashboard.spec/v1",
        "title": title,
        "archetype": v["dashboard_archetype"],
        "asks": v["question"],
        "scope": "single",
        "binding": "replay",
        "data": data_ref or {"js": "./data.js", "json": "./data.json",
                             "var": "DASHBOARD_DATA"},
        "rows": {"path": "entities", "id": "entity_id", "label": label_field},
        "priority": {
            "label": v["priority_label"],
            "when": v["priority_when"],
            "empty": v["priority_empty"],
        },
        "columns": [{"key": label_field, "label": "Item"}],
        "status": {"field": "reviewed", "true": v["status_true"],
                   "false": v["status_false"]},
        "detail": list(v["detail"]),
        "group": {"by": v["group_by"], "label": v["group_label"]},
        "footer": footer,
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps(describe(), indent=2))
    else:
        print(f"{'variant':<20} {'answers':<44} blocking")
        for r in describe():
            print(f"  {r['variant']:<18} {r['answers']:<44} {r['blocking']}")
