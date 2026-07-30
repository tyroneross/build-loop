#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""File a consolidated candidate through the P2 writer guard.

Given a classification decision (from the host LLM or ``heuristic_decision``),
``place()`` resolves the right memory_dir, picks a filename (canonical when
omitted), prepends a `## Backlinks` footer when backlinks are present, and
calls ``memory_writer.write()``. The writer guard normalises any residual
lane-prefix slop — single source of truth for path safety.

After a successful write the candidate transitions from ``pending/`` to
``placed/`` with the on-disk placement metadata attached.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path

import memory_writer as mw  # noqa: E402
import _paths  # type: ignore  # noqa: E402

from .intake import (  # noqa: E402
    PLACED_DIR,
    REJECTED_DIR,
    Candidate,
    load_candidate,
    queue_dir,
    transition,
)


def _resolve_memory_dir(decision: dict) -> Path:
    """Resolve the lane's default memory_dir BEFORE the writer guard runs.

    The guard re-points memory_dir based on the file path; here we set the
    *default* the guard starts from. For scope=project + lane=lessons that's
    ``project_lessons_dir(project)``. For other sublanes we still start at
    ``project_lessons_dir(project)`` because the guard will move us to the
    right sublane when it sees ``<sublane>/<filename>`` — keeps the resolver
    DRY (one entrypoint per scope, the guard handles sublanes).
    """
    scope = decision.get("scope") or "top-level"
    if scope == "project":
        project = decision.get("project")
        if not project:
            raise ValueError("decision.scope='project' requires a project")
        # Always hand the writer the lessons dir as the scope default; the
        # guard re-points to the lane when the file path encodes one.
        return _paths.project_lessons_dir(project)
    return _paths.top_level_lessons_dir()


def _decision_filename(candidate: Candidate, decision: dict) -> str:
    """Pick a filename from the decision, falling back to canonical."""
    fn = decision.get("filename")
    if fn:
        return fn
    type_ = decision.get("type") or candidate.type or "lesson"
    name = decision.get("name") or candidate.name or candidate.id
    return mw.canonical_filename(type_=type_, name=name)


def _body_with_backlinks(content: str, backlinks: list) -> str:
    if not backlinks:
        return content
    lines = [content.rstrip(), "", "## Backlinks", ""]
    for bl in backlinks:
        lines.append(f"- {bl}")
    return "\n".join(lines) + "\n"


def place(
    candidate_id: str,
    decision: dict,
    *,
    workdir: str | Path = ".",
    run_id: str | None = None,
    host: str | None = None,
) -> dict:
    """File ``candidate_id`` per ``decision``. Returns the writer's frontmatter dict.

    Decision shape (heuristic_decision()'s output is a valid value):
        {
          "scope": "project" | "top-level",
          "project": "<slug>" | null,
          "lane": "<sublane>",
          "type": "<one of VALID_TYPES>",
          "name": "<slug>" | null,
          "filename": "<rel>" | null,
          "backlinks": ["<file>", ...],
        }

    ``run_id`` and ``host`` default to the candidate's original values; pass
    explicitly when consolidation runs in a different run than the submission.
    """
    c = load_candidate(candidate_id, workdir=workdir)
    if c._state != "pending":
        raise ValueError(
            f"candidate {candidate_id!r} is in state {c._state!r}, expected 'pending'"
        )

    scope = decision.get("scope") or ("project" if c.project else "top-level")
    project = decision.get("project") or c.project
    lane = decision.get("lane") or "lessons"
    type_ = decision.get("type") or c.type or "lesson"
    backlinks = decision.get("backlinks") or []
    name = decision.get("name") or c.name or c.id
    fn = _decision_filename(c, decision)

    # Build the file_rel the writer guard will normalise:
    # ``<lane>/<filename>`` — guard re-points memory_dir to the lane.
    file_rel = f"{lane}/{fn}" if lane != "lessons" else fn

    memory_dir = _resolve_memory_dir({"scope": scope, "project": project})

    # Normalize once here so placement metadata uses the same resolved path
    # as the writer — eliminates a second _normalize_file_rel call after write().
    file_rel_normalised, normalised_dir = mw._normalize_file_rel(
        file_rel, scope=scope, project=project, memory_dir=memory_dir,
    )

    body = _body_with_backlinks(c.content, backlinks)

    # Build a short, traceable description for provenance frontmatter.
    description = (c.hint or c.content.split("\n", 1)[0])[:200]

    fm = mw.write(
        normalised_dir,
        file_rel=file_rel_normalised,
        body=body,
        name=name,
        description=description,
        type_=type_,
        run_id=run_id or c.source_run_id or "unknown",
        workdir=c.source_workdir or str(workdir),
        host=host or c.source_host or "other",
        scope=scope,
        project=project,
        extra_frontmatter={
            "consolidated_from": c.id,
            "consolidated_via": "memory_consolidate.place",
        },
    )

    placement = {
        "memory_dir": str(normalised_dir),
        "file_rel": file_rel_normalised,
        "absolute_path": str(normalised_dir / file_rel_normalised),
        "lane": lane,
        "scope": scope,
        "project": project,
        "type": type_,
        "filename": fn,
        "backlinks": backlinks,
        "placed_at": fm.get("last_updated_at"),
    }
    transition(c, PLACED_DIR, placement=placement, workdir=workdir)
    return fm


def reject(
    candidate_id: str,
    reason: str,
    *,
    workdir: str | Path = ".",
) -> Candidate:
    """Move a candidate to ``rejected/`` with a reason. No writer call."""
    c = load_candidate(candidate_id, workdir=workdir)
    if c._state != "pending":
        raise ValueError(f"candidate {candidate_id!r} is not pending")
    return transition(c, REJECTED_DIR, placement={"rejected_reason": reason}, workdir=workdir)
