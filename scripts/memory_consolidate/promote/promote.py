#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Recurrence-gated project→global promotion.

A promotion is only accepted when a lesson has demonstrably recurred
across ``min_projects`` distinct projects. Sourced from cross-project
P1 dense recall (the same hybrid tier dedup uses); the gate is purely
structural — it counts distinct project tags among similar siblings.

Public surface:
    find_promotion_candidates(workdir, *, min_projects, threshold) -> list[PromotionCandidate]
    promotion_gate(candidate, *, min_projects) -> PromotionDecision
    prepare_promotion_packet(candidate) -> PromotionPacket
    heuristic_promotion_decision(candidate) -> dict
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # scripts/
sys.path.insert(0, str(HERE.parent))         # memory_consolidate/

DEFAULT_MIN_PROJECTS = 2
DEFAULT_SIMILARITY_THRESHOLD = 0.55

# Module-level sentinel: flips False on first ImportError from semantic_index.
# Emitted ONCE to stderr so callers can surface the degraded state.
_recall_available: bool = True
_recall_warn_emitted: bool = False

# Project sublane → global lane mapping. Architecture maps to global
# architecture lane, debugging to global debugging, design to global
# design, etc. Anything else falls back to global lessons.
PROJECT_TO_GLOBAL_LANE: dict[str, str] = {
    "architecture": "architecture",
    "debugging": "debugging",
    "design": "design",
    "product": "product",
    "lessons": "lessons",
}


@dataclass
class PromotionCandidate:
    """A project lesson with its cross-project sibling cohort."""
    source_path: str            # absolute path on disk
    name: str | None
    type_: str
    project: str
    lane: str
    body_excerpt: str
    siblings: list[dict] = field(default_factory=list)
    distinct_projects: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "name": self.name,
            "type": self.type_,
            "project": self.project,
            "lane": self.lane,
            "body_excerpt": self.body_excerpt,
            "siblings": self.siblings,
            "distinct_projects": sorted(self.distinct_projects),
            "distinct_project_count": len(self.distinct_projects),
        }


@dataclass
class PromotionDecision:
    """Outcome of the promotion gate."""
    accepted: bool
    reason: str
    distinct_project_count: int
    min_projects_required: int

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "distinct_project_count": self.distinct_project_count,
            "min_projects_required": self.min_projects_required,
        }


@dataclass
class PromotionPacket:
    """Structured data the host LLM reads to refine the promotion target."""
    candidate: PromotionCandidate
    gate: PromotionDecision
    suggested_decision: dict
    instructions: str

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate.to_dict(),
            "gate": self.gate.to_dict(),
            "suggested_decision": self.suggested_decision,
            "instructions": self.instructions,
        }


# ---------------------------------------------------------------------------
# Frontmatter helpers (parser-lite — same shape memory_consolidate uses).
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def _walk_project_lessons(root: Path, projects: Iterable[str] | None = None) -> list[Path]:
    """Yield every project lesson file under ``<root>/projects/*/lessons/``."""
    projects_root = root / "projects"
    if not projects_root.exists():
        return []
    out: list[Path] = []
    target_projects = set(projects) if projects is not None else None
    for project_dir in sorted(projects_root.iterdir()):
        if not project_dir.is_dir():
            continue
        if target_projects is not None and project_dir.name not in target_projects:
            continue
        for sublane in ("lessons", "debugging", "architecture", "design", "product"):
            laned = project_dir / sublane
            if not laned.exists():
                continue
            for f in sorted(laned.glob("*.md")):
                if f.name.startswith("INDEX") or f.name.startswith("TELEMETRY"):
                    continue
                out.append(f)
    return out


def _query_cross_project_siblings(
    body: str,
    *,
    own_project: str,
    limit: int = 20,
    embed_fn: Any = None,
) -> list[dict]:
    """P1 hybrid recall scoped across ALL projects (no project filter)."""
    global _recall_available, _recall_warn_emitted
    if not body or not body.strip():
        return []
    try:
        from semantic_index import query_facts  # type: ignore  # noqa: PLC0415
    except (ImportError, ModuleNotFoundError):
        if not _recall_warn_emitted:
            print(
                "WARN: semantic_index unavailable; promotion gate will reject "
                "all candidates as single-project",
                file=sys.stderr,
            )
            _recall_warn_emitted = True
        _recall_available = False
        return []
    try:
        # NOTE: no project filter — we want siblings from OTHER projects.
        kwargs = {"query": body, "limit": limit, "mode": "hybrid"}
        if embed_fn is not None:
            kwargs["embed_fn"] = embed_fn
        rows = query_facts(**kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: promote recall failed: {exc}", file=sys.stderr)
        return []
    out: list[dict] = []
    for row in rows:
        proj = row.get("project")
        if not proj or proj == own_project:
            continue
        out.append({
            "subject": row.get("subject"),
            "predicate": row.get("predicate"),
            "object": row.get("object"),
            "project": proj,
            "file_hint": row.get("file_hint") or row.get("subject"),
        })
    return out


def find_promotion_candidates(
    workdir: str | Path = ".",
    *,
    min_projects: int = DEFAULT_MIN_PROJECTS,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    memory_root: str | Path | None = None,
    siblings_fn: Any = None,
    embed_fn: Any = None,
    projects: Iterable[str] | None = None,
) -> list[PromotionCandidate]:
    """Walk project lessons; gather cross-project siblings; return one
    PromotionCandidate per source lesson (regardless of whether it
    eventually clears the gate — the gate runs separately).

    ``siblings_fn`` is injectable for tests: ``(body, own_project) -> list[dict]``.
    When provided, replaces the P1 recall path entirely. Each returned dict
    must include at least ``project`` and ``file_hint``.
    """
    if memory_root is None:
        # Lazy import; avoids hard dep on _paths in unit tests.
        try:
            from _paths import memory_store_root  # type: ignore  # noqa: PLC0415
            memory_root = memory_store_root()
        except Exception:
            memory_root = Path(workdir) / "build-loop-memory"
    root = Path(memory_root)
    candidates: list[PromotionCandidate] = []
    for path in _walk_project_lessons(root, projects=projects):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        body = _FM_RE.sub("", text, count=1).strip()
        # Project = the second segment of the path under root.
        try:
            rel = path.relative_to(root)
            parts = rel.parts
            project = parts[1] if len(parts) >= 3 and parts[0] == "projects" else None
            lane = parts[2] if len(parts) >= 4 else "lessons"
        except ValueError:
            project = None
            lane = "lessons"
        if not project:
            continue

        excerpt = body[:600]
        if siblings_fn is not None:
            siblings = list(siblings_fn(excerpt, project) or [])
        else:
            siblings = _query_cross_project_siblings(
                excerpt, own_project=project, embed_fn=embed_fn,
            )

        distinct = {s["project"] for s in siblings if s.get("project")}
        candidates.append(PromotionCandidate(
            source_path=str(path),
            name=fm.get("name"),
            type_=fm.get("type", "lesson"),
            project=project,
            lane=lane,
            body_excerpt=excerpt,
            siblings=siblings,
            distinct_projects=distinct,
        ))
    return candidates


def promotion_gate(
    candidate: PromotionCandidate,
    *,
    min_projects: int = DEFAULT_MIN_PROJECTS,
) -> PromotionDecision:
    """Recurrence-gated promotion decision.

    Accept ONLY when ``distinct_project_count >= min_projects`` (the
    source project counts as 1 — siblings must come from at least
    ``min_projects - 1`` OTHER projects). A one-off lesson is rejected
    with ``reason='single-project'``.
    """
    # Self + every sibling's distinct project — but the gate counts the
    # cross-project recurrence: source + every distinct sibling project.
    total_projects = {candidate.project} | candidate.distinct_projects
    distinct = len(total_projects)
    if distinct < min_projects:
        return PromotionDecision(
            accepted=False,
            reason="single-project" if distinct == 1 else "not-enough-projects",
            distinct_project_count=distinct,
            min_projects_required=min_projects,
        )
    return PromotionDecision(
        accepted=True,
        reason="recurrence-earned",
        distinct_project_count=distinct,
        min_projects_required=min_projects,
    )


def heuristic_promotion_decision(candidate: PromotionCandidate) -> dict:
    """Deterministic suggested decision: lane mapped from project sublane.

    Lane mapping ensures a project-architecture lesson promotes to the
    global architecture lane, etc. The host LLM may override.
    """
    global_lane = PROJECT_TO_GLOBAL_LANE.get(candidate.lane, "lessons")
    backlinks = [candidate.source_path]
    for s in candidate.siblings[:5]:
        hint = s.get("file_hint") or s.get("subject")
        if hint:
            backlinks.append(str(hint))
    name = candidate.name or "cross-project-pattern"
    return {
        "scope": "top-level",
        "project": None,
        "lane": global_lane,
        "type": candidate.type_ or "lesson",
        "name": name,
        "filename": None,
        "backlinks": backlinks,
        "promoted_from_project": candidate.project,
        "promoted_from_path": candidate.source_path,
        "recurrence_projects": sorted(
            {candidate.project} | candidate.distinct_projects
        ),
    }


def prepare_promotion_packet(
    candidate: PromotionCandidate,
    *,
    min_projects: int = DEFAULT_MIN_PROJECTS,
) -> PromotionPacket:
    gate = promotion_gate(candidate, min_projects=min_projects)
    suggested = heuristic_promotion_decision(candidate)
    instructions = (
        "If gate.accepted is true, refine the promotion target (lane/name/"
        "summary). If false, do not promote; the gate is recurrence-earned "
        "(no single-project bubble-up). Backlinks point to the source + "
        "cross-project siblings; preserve every sibling in backlinks so the "
        "global entry is traceable."
    )
    return PromotionPacket(
        candidate=candidate, gate=gate,
        suggested_decision=suggested, instructions=instructions,
    )
