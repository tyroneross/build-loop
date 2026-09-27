#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Classify when build-loop should run/persist Research plugin work.
#   application: planning
#   status: active
"""Research trigger and depth classifier for build-loop Phase 1/2."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402

DEPTH_ORDER = {"none": 0, "light": 1, "standard": 2, "deep": 3}
DEPTH_MODE = {
    "none": "none",
    "light": "quick",
    "standard": "balanced",
    "deep": "max_accuracy",
}
EFFORT_MIN_DEPTH = {
    "XS": "light",
    "S": "light",
    "M": "standard",
    "L": "standard",
    "XL": "standard",
}
EFFORT_MEMORY_DEPTH = {
    "XS": "compact",
    "S": "focused",
    "M": "standard",
    "L": "deep",
    "XL": "deep",
}

EXPLICIT_RESEARCH_RE = re.compile(
    r"\b(research|investigate|evaluate|compare|look\s+up|recommend|"
    r"recommendation|should\s+i|which\s+.+\s+better)\b",
    re.IGNORECASE,
)
CURRENT_EXTERNAL_RE = re.compile(
    r"\b(latest|current|today|pricing|version|release|changelog|"
    r"deprecat(?:e|ed|ion)|official\s+docs?|standard|regulation)\b",
    re.IGNORECASE,
)
NEW_DEP_RE = re.compile(
    r"\b(api|sdk|provider|package|library|framework|model|mcp|oauth|"
    r"deployment|deploy|database|queue|webhook|stripe|openai|anthropic|"
    r"vercel|github|google|aws|azure|postgres|pgvector)\b",
    re.IGNORECASE,
)
INTEGRATION_VERB_RE = re.compile(
    r"\b(add|integrate|wire|connect|migrate|install|adopt|replace|upgrade)\b",
    re.IGNORECASE,
)
ARCH_BOUNDARY_RE = re.compile(
    r"\b(architecture|boundary|cross-layer|database|schema|migration|"
    r"deployment|auth|security|compliance|legal|persistence|protocol|"
    r"plugin|hook|mcp|memory|retrieval|rally)\b",
    re.IGNORECASE,
)
REUSABLE_RE = re.compile(
    r"\b(persist|reusable|recurring|repeat|lesson|decision|packet|"
    r"build-loop-memory|future\s+use)\b",
    re.IGNORECASE,
)
DEEP_RE = re.compile(
    r"\b(deep|thorough|comprehensive|decision-grade|strategy|north\s+star|"
    r"prd|high-risk|large-corpus)\b",
    re.IGNORECASE,
)
LIGHT_RE = re.compile(r"\b(quick|light|brief|narrow|simple)\b", re.IGNORECASE)
HIGH_RISK_RE = re.compile(
    r"\b(security|auth|compliance|legal|finance|medical|privacy|secrets?|"
    r"production|payment|billing)\b",
    re.IGNORECASE,
)

# Local reconciliation uses code/diff review. "Compare" and "latest main"
# alone do not create an external research task. Keep explicit research and
# concrete external/risk signals even when maintenance is the primary owner.
MAINTENANCE_RE = re.compile(
    r"\b(?:compare|review|reconcile|merge|prune|clean(?:\s+up)?|consolidate)\b"
    r"\s+(?:(?:these|the|all|open|stale|local|current|latest|our|my|recent|active|"
    r"completed|code|diffs|and|between|both)\s+){0,6}"
    r"(?:worktrees?|branches|branch|stashes|main|repo(?:sitory)?)\b",
    re.IGNORECASE,
)
DIRECT_RESEARCH_RE = re.compile(r"\b(?:research|look\s+up|search\s+(?:for|the\s+web))\b", re.I)
EXTERNAL_SUBJECT_RE = re.compile(
    r"\b(?:api|sdk|provider|package|library|libraries|framework|model|pricing|"
    r"regulation|official\s+docs?|external|internet|web|upstream\s+(?:docs?|release))\b", re.I,
)
CONTINUE_RE = re.compile(
    r"\b(?:then|and)\s+(?:implement|build|apply|integrate|merge|fix)\b", re.I,
)
RECOMMEND_ONLY_RE = re.compile(
    r"\b(?:do\s+not|don['’]t|never)\s+(?:implement|build|apply|integrate|merge|change|edit)\b"
    r"|\b(?:read.only|research\s+only|recommend(?:ations?)?\s+only|before\s+I\s+decide|"
    r"without\s+(?:implementing|building|merging|changing)|no\s+implementation|"
    r"(?:implement|build|merge|apply)\s+nothing|until\s+(?:I\s+approve|approved|approval))\b",
    re.I,
)


def _max_depth(*depths: str) -> str:
    return max(depths, key=lambda value: DEPTH_ORDER[value])


def _slugify(text: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", text.lower())
    slug = "-".join(tokens[:8]).strip("-")
    return slug or "research"


def classify_research(
    *,
    task: str,
    workdir: Path,
    effort: str | None = None,
    today: date | None = None,
    context: str = "auto",
) -> dict[str, Any]:
    text = task or ""
    triggers: list[str] = []

    explicit = bool(EXPLICIT_RESEARCH_RE.search(text))
    current_external = bool(CURRENT_EXTERNAL_RE.search(text))
    new_dependency = bool(NEW_DEP_RE.search(text) and INTEGRATION_VERB_RE.search(text))
    architecture_boundary = bool(ARCH_BOUNDARY_RE.search(text))
    reusable_packet = bool(REUSABLE_RE.search(text))
    high_risk = bool(HIGH_RISK_RE.search(text))
    deep_requested = bool(DEEP_RE.search(text))
    light_requested = bool(LIGHT_RE.search(text))
    maintenance = context == "maintenance" or (context == "auto" and bool(MAINTENANCE_RE.search(text)))
    if maintenance and not EXTERNAL_SUBJECT_RE.search(text):
        explicit = bool(DIRECT_RESEARCH_RE.search(text))
        if not explicit:
            current_external = bool(re.search(
                r"\b(?:pricing|version|release|changelog|deprecat(?:e|ed|ion)|regulation)\b", text, re.I,
            ))
        # Local review remains available; deep/recurring comparison alone is
        # not a research deliverable. Risk/architecture signals stay intact.
        if not explicit:
            reusable_packet = False
            deep_requested = False

    if explicit:
        triggers.append("explicit_research")
    if current_external:
        triggers.append("current_external")
    if new_dependency:
        triggers.append("new_dependency")
    if architecture_boundary:
        triggers.append("architecture_boundary")
    if reusable_packet:
        triggers.append("reusable_packet")
    if high_risk:
        triggers.append("high_risk")
    if deep_requested:
        triggers.append("deep_requested")

    research_required = bool(triggers)
    effort_key = (effort or "").upper() or None
    effort_min_depth = EFFORT_MIN_DEPTH.get(effort_key or "", "none")

    depth = "none"
    if research_required:
        depth = "light"
        if current_external or new_dependency:
            depth = _max_depth(depth, "standard")
        if explicit and re.search(r"\b(evaluate|compare|recommend|should\s+i)\b", text, re.I):
            depth = _max_depth(depth, "standard")
        if architecture_boundary and effort_key in {"M", "L", "XL"}:
            depth = _max_depth(depth, "standard")
        if deep_requested or high_risk:
            depth = _max_depth(depth, "deep")
        if architecture_boundary and effort_key in {"L", "XL"} and explicit:
            depth = _max_depth(depth, "deep")
        if effort_key:
            depth = _max_depth(depth, effort_min_depth)
        if light_requested and not high_risk and not current_external and not new_dependency:
            depth = "light"

    day = (today or date.today()).isoformat()
    packet_path = None
    if research_required:
        packet_path = str(
            Path(".build-loop") / "research" / f"{day}-{_slugify(text)}.md"
        )

    blocks_final_claims = bool(current_external or new_dependency or high_risk)
    requires_citations = bool(research_required and (current_external or new_dependency or high_risk))
    memory_depth = EFFORT_MEMORY_DEPTH.get(effort_key or "", "compact")
    if depth == "deep":
        memory_depth = "deep"
    elif depth == "standard" and memory_depth in {"compact", "focused"}:
        memory_depth = "standard"

    return {
        "request_context": "maintenance" if maintenance else context,
        "continuation": (
            "resume_requested_workflow"
            if context != "research" and not RECOMMEND_ONLY_RE.search(text) and (
                context == "build" or maintenance or bool(CONTINUE_RE.search(text))
            ) else "return_recommendation"
        ),
        "continuation_is_authorization": False,
        "research_required": research_required,
        "depth": depth,
        "mode": DEPTH_MODE[depth],
        "triggers": triggers,
        "effort": effort_key,
        "effort_min_depth": effort_min_depth,
        "memory_recall_depth": memory_depth,
        "packet_path": packet_path,
        "blocks_final_claims": blocks_final_claims,
        "requires_citations_or_unavailable_note": requires_citations,
        "source_policy": (
            "local-first, then Research plugin, then web/official docs for current or external claims"
            if research_required
            else "local-only unless a later trigger fires"
        ),
        "workdir": str(workdir.expanduser().resolve()),
    }


def cache_into_state(workdir: Path, payload: dict[str, Any]) -> Path:
    state_path = workdir.expanduser().resolve() / ".build-loop" / "state.json"
    with LockedFile(state_path):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
        state["researchGate"] = payload
        history = state.get("researchGateHistory")
        if not isinstance(history, list):
            history = []
        history.append(payload)
        state["researchGateHistory"] = history[-10:]
        atomic_write_bytes(
            state_path,
            (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
    return state_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Goal/request text to classify.")
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--effort", choices=["XS", "S", "M", "L", "XL", "xs", "s", "m", "l", "xl"])
    parser.add_argument("--context", choices=["auto", "maintenance", "build", "research"], default="auto",
                        help="Primary workflow selected from the user's request; research stays an assist to maintenance/build.")
    parser.add_argument("--cache-into-state", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir)
    payload = classify_research(task=args.task, workdir=workdir, effort=args.effort, context=args.context)
    if args.cache_into_state:
        payload["state_path"] = str(cache_into_state(workdir, payload))

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        required = "required" if payload["research_required"] else "not-required"
        print(f"{required} depth={payload['depth']} packet={payload['packet_path'] or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
