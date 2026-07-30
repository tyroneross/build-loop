# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""assess.py — render a product-impacting deferral as a backlog-item markdown.

Wraps the existing ``templates/backlog-item.md`` schema with two new
frontmatter fields (``product_impacting``, ``impact``) and a ``## Why it
matters (causal tree)`` body section authored from the triage rationale +
deferral context. Pure-stdlib string assembly — no YAML dependency.

Public API:
    build_item(deferral, *, repo, branch, run_id) -> str  (markdown body)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any


# Slug = lowercased, alphanumerics + hyphens only, max 60 chars.
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, *, maxlen: int = 60) -> str:
    s = _SLUG_STRIP_RE.sub("-", (text or "").lower()).strip("-")
    return s[:maxlen] or "untitled"


def _today_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _causal_tree(triage_rationale: str, deferral_text: str, impact: str | None) -> str:
    """Compose a tiny causal-tree narrative from the triage rationale.

    Not a full LLM-driven causal-tree analysis (that runs at end-of-run when
    root-cause-investigator is invoked). At capture time we record the
    surface signal + why it matters; the orchestrator may upgrade later.
    """
    lines = []
    if impact:
        lines.append(f"- Surface signal: {impact}")
    lines.append(f"- Triage rationale: {triage_rationale}")
    lines.append(f"- Why deferred (not blocking this run): captured at descope time; "
                 f"upgrade priority on user-impact severity in the next planning pass.")
    return "\n".join(lines)


def build_item(
    deferral: dict[str, Any],
    *,
    repo: str,
    branch: str = "main",
    run_id: str = "unknown",
) -> str:
    """Render a product-impacting deferral as a backlog-item markdown body.

    Args:
        deferral: dict shape
            {
                "title":       str,                # one-line imperative
                "text":        str,                # full descope context
                "triage":      dict from triage.classify (must carry product_impacting=True),
                "classify":    "SAFE"|"RISKY"|"DECISION"|"PRODUCTION"  (optional; default SAFE),
                "effort":      "XS"|"S"|"M"|"L"|"XL" (optional; default M),
            }
        repo:   repo slug (e.g. "build-loop")
        branch: branch name (default "main")
        run_id: originating run id for the `source:` field

    Returns:
        Complete markdown body (frontmatter + Problem + Proposed fix +
        Acceptance + Why it matters), ready to write to
        ``.build-loop/backlog/<repo>/<id>-<slug>.md``.

    Raises:
        ValueError if the triage indicates product_impacting=False (build_item
        is product-impacting only). Use the non-product-impacting path
        (write to ``.build-loop/followup/``) for those.
    """
    triage = deferral.get("triage") or {}
    if not triage.get("product_impacting"):
        raise ValueError(
            "build_item called with product_impacting=False; "
            "non-product-impacting deferrals belong in .build-loop/followup/, "
            "not the backlog."
        )

    title = (deferral.get("title") or "").strip() or (deferral.get("text") or "").strip()[:80]
    text = (deferral.get("text") or "").strip() or title
    impact = triage.get("impact")
    classify = (deferral.get("classify") or "SAFE").upper()
    effort = (deferral.get("effort") or "M").upper()

    # Escape any literal '\n' or quote chars that would break frontmatter.
    title_oneline = " ".join(title.splitlines()).strip()
    impact_oneline = (impact or "").replace("\n", " ").strip()

    frontmatter = [
        "---",
        f"title: {title_oneline}",
        f"repo: {repo}",
        f"branch: {branch}",
        f"created: {_today_iso()}",
        f"source: run/{run_id}",
        f"classify: {classify}",
        f"effort: {effort}",
        "status: open",
        "product_impacting: true",
        f"impact: {impact_oneline}",
        "---",
    ]

    body = [
        "",
        "## Problem",
        text,
        "",
        "## Proposed fix",
        "<smallest mechanism that addresses the root cause; prefer extend/delete over add>",
        "",
        "## Acceptance",
        f"- {impact_oneline}: resolved and verified by user-facing test or screenshot",
        "- no regression on adjacent flows (verified at Phase 4 Review-B)",
        "",
        "## Why it matters (causal tree)",
        _causal_tree(triage.get("rationale", ""), text, impact),
        "",
    ]

    return "\n".join(frontmatter) + "\n" + "\n".join(body)
