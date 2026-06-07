#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Build a host-LLM-ready classification packet for a raw candidate.

Per "host agent is the LLM" rule: this module NEVER calls a vendor API.
It assembles deterministic structured data; the host (Claude Code / Codex /
others) reads the packet via their normal tool surface and emits a decision
JSON. ``heuristic_decision()`` is the deterministic fallback used when no
host is in the loop (CI / tests) — mirrors the three-tier
``deterministic-always + host-LLM-refine`` pattern from correction-capture.

Reuses P1 dense recall (``semantic_index.query_facts(mode='hybrid')``) for
dedup + backlink suggestion. We do NOT reinvent similarity here.

ConsolidationPacket shape (the data the host LLM reads):

    {
      "candidate": { ...intake.Candidate.to_dict() },
      "lane_options": {
        "project": ["lessons", "issues", "decisions", "debugging", ...],
        "top-level": ["lessons", "debugging", "design", "product", "architecture"],
      },
      "type_options": [ ...VALID_TYPES sorted ],
      "similar_existing": [   # top-K from P1 dense recall
        {
          "rank": 1,
          "score": 1.23,
          "subject": "...",
          "predicate": "...",
          "object": "...",
          "project": "...",
          "file_hint": "lessons/2026-06-07-...-something.md",
        },
        ...
      ],
      "suggested_decision": {  # heuristic_decision() — host MAY override
        "scope": "project" | "top-level",
        "project": "<slug>" | null,
        "lane": "lessons" | "issues" | ...,
        "type": "lesson" | "gotcha" | ...,
        "name": "<slug>",
        "filename": null,            # let the writer derive
        "backlinks": ["<file>", ...] # from similar_existing
      },
      "instructions": "Return a decision JSON with the same shape as
        suggested_decision. The writer guard normalises paths — you cannot
        cause a double-nest by picking a wrong-looking lane prefix."
    }

Decision JSON (the host LLM's reply) accepts the same fields. Missing fields
fall back to suggested_decision's value.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path

from .intake import Candidate, load_candidate  # noqa: E402

# Import lazily inside functions: avoids hard dep at module import.


VALID_TYPES_FALLBACK = (
    "tool", "deployment", "library-choice", "user-preference", "pattern",
    "feedback", "reference", "design", "convention", "gotcha", "decision",
    "contract", "lesson", "run-summary", "debug-incident", "debug-fix",
    "procedure", "architecture", "api-contract", "design-guidance",
    "product-idea", "product-backlog", "product-opportunity",
    "product-use-case", "product-ruled-out", "source-summary", "agent",
    "plugin", "skill",
)

PROJECT_SUBLANES = (
    "lessons", "issues", "decisions", "debugging", "design", "product",
    "architecture", "raw",
)

TOP_LEVEL_LANES = (
    "lessons", "debugging", "design", "product", "architecture",
)

# Heuristic: hint keywords → (lane, type) suggestion. Ordered list because
# the first match wins; deliberately small so the host LLM does most of
# the lift on ambiguous cases.
HINT_HEURISTICS: tuple[tuple[re.Pattern[str], tuple[str, str]], ...] = (
    (re.compile(r"\b(bug|crash|stack ?trace|exception|error|broken)\b", re.I), ("debugging", "debug-incident")),
    (re.compile(r"\b(decided|decision|chose|picked)\b", re.I), ("decisions", "decision")),
    (re.compile(r"\b(architecture|module|component|boundary|layer)\b", re.I), ("architecture", "architecture")),
    (re.compile(r"\b(api|contract|endpoint|schema)\b", re.I), ("architecture", "api-contract")),
    (re.compile(r"\b(issue|bug-report|problem|todo)\b", re.I), ("issues", "gotcha")),
    (re.compile(r"\b(gotcha|footgun|trap|pitfall|surprise)\b", re.I), ("lessons", "gotcha")),
    (re.compile(r"\b(pattern|convention|practice|rule)\b", re.I), ("lessons", "pattern")),
    (re.compile(r"\b(preference|prefer|want|like)\b", re.I), ("lessons", "user-preference")),
    (re.compile(r"\b(design|ui|ux|component)\b", re.I), ("design", "design-guidance")),
)


@dataclass
class ConsolidationPacket:
    candidate: dict
    lane_options: dict
    type_options: list
    similar_existing: list
    suggested_decision: dict
    instructions: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate,
            "lane_options": self.lane_options,
            "type_options": self.type_options,
            "similar_existing": self.similar_existing,
            "suggested_decision": self.suggested_decision,
            "instructions": self.instructions,
        }


def _query_similar(
    query: str,
    project: str | None,
    limit: int = 5,
) -> list[dict]:
    """Query the P1 dense recall tier for similar existing entries.

    Returns ``[]`` on any failure — recall must never block consolidation.
    """
    if not query or not query.strip():
        return []
    try:
        from semantic_index import query_facts  # type: ignore  # noqa: PLC0415
    except (ImportError, ModuleNotFoundError):
        # Recall stack not installed — silent absence-tolerant fallback.
        return []
    try:
        rows = query_facts(query=query, limit=limit, project=project, mode="hybrid")
    except Exception as exc:  # noqa: BLE001
        # Backend present but broken — warn so the operator knows, then degrade.
        print(f"WARN: semantic_index.query_facts failed: {exc}", file=sys.stderr)
        return []
    out: list[dict] = []
    for i, row in enumerate(rows, start=1):
        out.append({
            "rank": i,
            "subject": row.get("subject"),
            "predicate": row.get("predicate"),
            "object": row.get("object"),
            "project": row.get("project"),
            # file_hint: best available path-like reference for backlinks.
            # query_facts returns no dedicated 'path' column; subject carries
            # the fact's file path or name when indexed from a memory file.
            "file_hint": row.get("file_hint") or row.get("path") or row.get("subject"),
        })
    return out


def heuristic_decision(candidate: Candidate, similar: list[dict]) -> dict:
    """Deterministic-only fallback when no host LLM is in the loop.

    Strategy: hint keyword → (lane, type) lookup; project tag if the
    candidate carries one, else top-level. Backlinks pulled from the top
    similar entries. The writer guard catches anything bad on disk.
    """
    lane = "lessons"
    type_ = candidate.type or "lesson"
    scope = "project" if candidate.project else "top-level"

    haystack = " ".join(filter(None, [candidate.hint, candidate.content[:400]]))
    for pattern, (lane_hint, type_hint) in HINT_HEURISTICS:
        if pattern.search(haystack):
            if scope == "project":
                if lane_hint in PROJECT_SUBLANES:
                    lane = lane_hint
            else:
                if lane_hint in TOP_LEVEL_LANES:
                    lane = lane_hint
            if not candidate.type:
                type_ = type_hint
            break

    backlinks = []
    for s in similar[:3]:
        subj = s.get("subject") or ""
        if subj:
            backlinks.append(subj)

    return {
        "scope": scope,
        "project": candidate.project,
        "lane": lane,
        "type": type_,
        "name": candidate.name or candidate.id,
        "filename": None,  # let the writer derive
        "backlinks": backlinks,
    }


def prepare(
    candidate_id: str,
    workdir: str | Path = ".",
) -> ConsolidationPacket:
    """Build the consolidation packet the host LLM reads.

    Deterministic — same input → same packet. No vendor API calls.
    """
    c = load_candidate(candidate_id, workdir=workdir)
    # Query body is "name + first paragraph of content" — keeps the dense
    # query short enough for the embed backend on resource-constrained boxes
    # while still surfacing semantic matches.
    first_para = c.content.split("\n\n", 1)[0]
    qbody = " ".join(filter(None, [c.name, c.hint, first_para]))[:800]
    similar = _query_similar(qbody, c.project, limit=5)
    suggested = heuristic_decision(c, similar)
    return ConsolidationPacket(
        candidate=c.to_dict(),
        lane_options={
            "project": list(PROJECT_SUBLANES),
            "top-level": list(TOP_LEVEL_LANES),
        },
        type_options=list(VALID_TYPES_FALLBACK),
        similar_existing=similar,
        suggested_decision=suggested,
        instructions=(
            "Pick the lane/type/backlinks that best fit the candidate. The "
            "writer guard normalises paths (a wrong-looking lane prefix is "
            "auto-corrected). Return JSON with the same shape as "
            "suggested_decision; missing fields fall back to the suggestion."
        ),
    )
