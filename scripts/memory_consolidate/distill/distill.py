#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Distillation: group similar placed candidates, build a host-LLM packet.

Reads ``.build-loop/pending-lessons/placed/*.json`` (the consolidation
provenance + on-disk file pointers) and groups semantically-similar
entries via the P1 hybrid recall (``semantic_index.query_facts``).

Public surface:
    find_distill_candidates(workdir) -> list[PlacedRef]
    cluster_similar(refs, *, threshold, project=None) -> list[DistillCluster]
    prepare_distill_packet(cluster) -> DistillPacket
    heuristic_distill(packet) -> dict  # the distilled-entry decision JSON

Each cluster has ≥2 members; single-member "clusters" are NEVER
distilled (a one-off doesn't need a distilled rollup).

ZERO vendor API calls. Recall is the P1 hybrid tier (keyword candidates
→ embedding rerank) — reuses the existing embed/cosine machinery.

CONTRACT: this module is NEVER called from intake.py or place.py. Tests
assert no transitive import path from those modules into this one.
That's how we keep the consolidation step OFF the Stop / Phase 6 hot path.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # scripts/ on path

# Backlinks-friendly: lazy import P1 recall to keep this importable in
# environments without the recall stack (CI tests use injection).
_DEFAULT_SIMILARITY_THRESHOLD = 0.55  # cosine rerank score lower bound


@dataclass
class PlacedRef:
    """A placed consolidation candidate's on-disk reference."""
    candidate_id: str
    name: str | None
    type_: str | None
    project: str | None
    scope: str
    lane: str
    absolute_path: str
    body_excerpt: str  # first ~600 chars of original content (for query)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "type": self.type_,
            "project": self.project,
            "scope": self.scope,
            "lane": self.lane,
            "absolute_path": self.absolute_path,
            "body_excerpt": self.body_excerpt,
        }


@dataclass
class DistillCluster:
    """Two or more semantically-similar placed entries."""
    cluster_id: str
    members: list[PlacedRef]
    similarity_scores: list[float] = field(default_factory=list)
    project: str | None = None  # all members share scope+project for project-distill

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "members": [m.to_dict() for m in self.members],
            "similarity_scores": self.similarity_scores,
            "project": self.project,
        }


@dataclass
class DistillPacket:
    """The structured data a host LLM reads to emit a distilled-entry decision."""
    cluster: DistillCluster
    suggested_decision: dict
    instructions: str

    def to_dict(self) -> dict:
        return {
            "cluster": self.cluster.to_dict(),
            "suggested_decision": self.suggested_decision,
            "instructions": self.instructions,
        }


def _placed_dir(workdir: str | Path) -> Path:
    return Path(workdir) / ".build-loop" / "pending-lessons" / "placed"


def find_distill_candidates(workdir: str | Path = ".") -> list[PlacedRef]:
    """Load all placed consolidation candidates as PlacedRefs.

    Returns ``[]`` when the queue is empty or missing. Never raises.
    """
    pdir = _placed_dir(workdir)
    if not pdir.exists():
        return []
    out: list[PlacedRef] = []
    for f in sorted(pdir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        placement = d.get("placement") or {}
        if not placement:
            continue
        # Skip already-distilled entries (marked by async_runner on success).
        if placement.get("distilled_into"):
            continue
        out.append(PlacedRef(
            candidate_id=d.get("id", f.stem),
            name=d.get("name"),
            type_=placement.get("type") or d.get("type"),
            project=placement.get("project") or d.get("project"),
            scope=placement.get("scope") or "top-level",
            lane=placement.get("lane") or "lessons",
            absolute_path=placement.get("absolute_path") or "",
            body_excerpt=(d.get("content") or "")[:600],
        ))
    return out


def _query_recall(
    body_excerpt: str,
    *,
    project: str | None,
    limit: int = 10,
    embed_fn: Any = None,
) -> list[dict]:
    """Query the P1 hybrid recall tier. Absence-tolerant (recall down → [])."""
    if not body_excerpt or not body_excerpt.strip():
        return []
    try:
        from semantic_index import query_facts  # type: ignore  # noqa: PLC0415
    except (ImportError, ModuleNotFoundError):
        return []
    try:
        if embed_fn is not None:
            rows = query_facts(
                query=body_excerpt, limit=limit, project=project,
                mode="hybrid", embed_fn=embed_fn,
            )
        else:
            rows = query_facts(
                query=body_excerpt, limit=limit, project=project, mode="hybrid",
            )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: distill recall failed: {exc}", file=sys.stderr)
        return []
    return list(rows)


def cluster_similar(
    refs: list[PlacedRef],
    *,
    threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    embed_fn: Any = None,
    similarity_fn: Any = None,
) -> list[DistillCluster]:
    """Group PlacedRefs into clusters of size ≥2 via P1 hybrid recall.

    Strategy:
      For each ref, query P1 recall scoped to its project. Refs whose
      body_excerpt surface another ref in the top-K results form an edge.
      Connected components of size ≥2 become clusters. Single nodes
      (no similar peer) are dropped — distillation needs duplicates.

    ``similarity_fn`` is injectable for tests: ``(ref_a, ref_b) -> float``.
    When provided, replaces the P1 recall path entirely (deterministic
    test mode). Returns clusters sorted by size desc.
    """
    if len(refs) < 2:
        return []

    # Build adjacency by either injected similarity (tests) or P1 recall.
    edges: dict[str, set[str]] = {r.candidate_id: set() for r in refs}
    scores: dict[tuple[str, str], float] = {}

    if similarity_fn is not None:
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                s = float(similarity_fn(a, b))
                if s >= threshold:
                    edges[a.candidate_id].add(b.candidate_id)
                    edges[b.candidate_id].add(a.candidate_id)
                    scores[(a.candidate_id, b.candidate_id)] = s
                    scores[(b.candidate_id, a.candidate_id)] = s
    else:
        # P1 recall path: query each ref against its project scope; match
        # the returned rows back to other refs by name/subject text.
        by_name = {(r.name or "").lower(): r for r in refs if r.name}
        for r in refs:
            rows = _query_recall(
                r.body_excerpt, project=r.project, limit=10, embed_fn=embed_fn,
            )
            for row in rows:
                subj = str(row.get("subject") or "").lower()
                obj = str(row.get("object") or "").lower()
                for other_name, other in by_name.items():
                    if other.candidate_id == r.candidate_id:
                        continue
                    if other_name and (other_name in subj or other_name in obj):
                        edges[r.candidate_id].add(other.candidate_id)
                        edges[other.candidate_id].add(r.candidate_id)

    # Connected components.
    by_id = {r.candidate_id: r for r in refs}
    seen: set[str] = set()
    clusters: list[DistillCluster] = []
    for r in refs:
        if r.candidate_id in seen:
            continue
        # BFS.
        component: list[str] = []
        frontier = [r.candidate_id]
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            seen.add(cur)
            component.append(cur)
            for nbr in edges[cur]:
                if nbr not in seen:
                    frontier.append(nbr)
        if len(component) < 2:
            continue
        members = [by_id[cid] for cid in component]
        # All members must share project for a project-distill cluster.
        # Mixed-project clusters become "cross-project" — promotion arm
        # handles those (P3 promote).
        projects = {m.project for m in members}
        cluster_project = members[0].project if len(projects) == 1 else None
        component.sort()
        cid_seed = "+".join(component)
        cluster_id = re.sub(r"[^A-Za-z0-9]+", "-", cid_seed).strip("-")[:96]
        sim_scores = [
            scores[(a, b)]
            for a in component for b in component
            if a < b and (a, b) in scores
        ]
        clusters.append(DistillCluster(
            cluster_id=cluster_id,
            members=members,
            similarity_scores=sim_scores,
            project=cluster_project,
        ))

    clusters.sort(key=lambda c: len(c.members), reverse=True)
    return clusters


def prepare_distill_packet(cluster: DistillCluster) -> DistillPacket:
    """Build a structured packet the host LLM reads to draft a distilled entry."""
    suggested = heuristic_distill_decision(cluster)
    instructions = (
        "Draft a single distilled lesson summarising the shared insight from "
        "the cluster members. Return JSON with the same shape as "
        "suggested_decision; missing fields fall back to the suggestion. "
        "Backlink fields point to the original entry filenames so the "
        "distilled entry remains traceable. NEVER promote to a global lane "
        "here — promotion is a separate recurrence-gated step."
    )
    return DistillPacket(
        cluster=cluster, suggested_decision=suggested, instructions=instructions,
    )


def heuristic_distill_decision(cluster: DistillCluster) -> dict:
    """Deterministic fallback distilled-entry decision (host-LLM-free)."""
    # Project clusters → project-scope distill; mixed → top-level (rare).
    if cluster.project:
        scope = "project"
        project = cluster.project
    else:
        scope = "top-level"
        project = None

    # Take the most common type among members; default 'lesson'.
    type_counts: dict[str, int] = {}
    for m in cluster.members:
        if m.type_:
            type_counts[m.type_] = type_counts.get(m.type_, 0) + 1
    if type_counts:
        type_ = max(type_counts.items(), key=lambda kv: kv[1])[0]
    else:
        type_ = "lesson"

    # Name: shared-prefix of member names, or first member's name.
    names = [m.name for m in cluster.members if m.name]
    if names:
        name = _common_slug_prefix(names) or names[0]
    else:
        name = f"distilled-{cluster.cluster_id[:20]}"

    # Backlinks: every member's absolute path (the original entries).
    backlinks = [m.absolute_path for m in cluster.members if m.absolute_path]

    return {
        "scope": scope,
        "project": project,
        "lane": "lessons",  # distilled rollups live in lessons/ by default
        "type": type_,
        "name": f"{name}-distilled",
        "filename": None,
        "backlinks": backlinks,
        "distilled_from": [m.candidate_id for m in cluster.members],
    }


def heuristic_distill(packet: DistillPacket) -> dict:
    """Convenience: heuristic decision JSON straight from a packet."""
    return packet.suggested_decision


def _common_slug_prefix(strings: list[str]) -> str:
    """Return the longest common prefix between strings, slug-only chars."""
    if not strings:
        return ""
    s = strings[0]
    for other in strings[1:]:
        i = 0
        while i < len(s) and i < len(other) and s[i] == other[i]:
            i += 1
        s = s[:i]
    # Trim trailing separators.
    return re.sub(r"[^A-Za-z0-9]+$", "", s)
