#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""v2/v3 metadata defaults + validators + MADR rendering.

This is the schema-of-record for the written decision frontmatter. The default
derivations, validation rules, and the MADR body layout are byte-for-byte
identical to the historical flat module — the v2 (design §15) and v3
(design §16) field sets and their order are what decision-file readers depend
on.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from constants import (
    DEFAULT_EMBEDDING_MODEL_VERSION,
    VALID_CONFIDENCE_SOURCES,
    VALID_DOMAINS,
    VALID_GOALS,
    VALID_TASK_CATEGORIES,
    VALID_TOOLS,
)
from frontmatter import emit_frontmatter


# ---------- MADR rendering ----------


def render_madr(fm: dict[str, Any], body: dict[str, str]) -> str:
    fm_text = emit_frontmatter(fm)
    parts = [fm_text, f"# {fm.get('title','')}", ""]
    for heading, key in (
        ("## Context\n", "context"),
        ("## Decision\n", "decision"),
        ("## Alternatives considered\n", "alternatives"),
        ("## Consequences\n", "consequences"),
        ("## Notes\n", "notes"),
    ):
        if body.get(key):
            parts.append(heading)
            parts.append(body[key])
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# ---------- v2 metadata defaults (design §15) ----------


def _derive_project(entity: str | None, workdir: Path) -> str:
    """Default project: prefix of entity before ':' if present, else
    $CLAUDE_PROJECT_DIR basename, else workdir basename, else 'unknown'.
    """
    if entity and ":" in entity:
        prefix = entity.split(":", 1)[0].strip()
        if prefix:
            return prefix
    cpd = os.environ.get("CLAUDE_PROJECT_DIR")
    if cpd:
        name = Path(cpd).name
        if name:
            return name
    name = workdir.name
    return name or "unknown"


def _git_diff_files(workdir: Path) -> list[str]:
    """Return repo-relative paths from `git diff --name-only HEAD~1 HEAD`.
    Returns [] on any error (no commits yet, not a repo, missing git, etc.)."""
    try:
        cp = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if cp.returncode != 0:
        return []
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def _default_tool_for_source(source: str) -> str:
    if source == "manual":
        return "manual"
    if source == "migration":
        return "migration"
    return "claude-code"


def apply_v2_defaults(
    *,
    project: str | None,
    tool: str | None,
    model: str | None,
    task_category: str | None,
    author: str | None,
    files_touched: list[str] | None,
    closing_commit: str | None,
    last_validated: str | None,
    last_accessed: str | None,
    source: str,
    entity: str,
    workdir: Path,
    infer_files: bool = False,
) -> dict[str, Any]:
    """Apply schema-v2 defaults. Returns a dict of the 9 v2 fields.

    Defaults follow design §15 / TAXONOMY.md §9. The `source` value
    influences the `tool` default ('manual' source → 'manual' tool;
    'migration' source → 'migration'; everything else → 'claude-code').
    """
    if tool is None:
        tool = _default_tool_for_source(source)
    if model is None:
        model = "claude-opus-4-7"
    if task_category is None:
        task_category = "unknown"
    if author is None:
        author = os.environ.get("USER") or "unknown"
    if project is None:
        project = _derive_project(entity, workdir)
    if files_touched is None:
        files_touched = _git_diff_files(workdir) if infer_files else []
    return {
        "project": project,
        "tool": tool,
        "model": model,
        "task_category": task_category,
        "author": author,
        "last_validated": last_validated,
        "last_accessed": last_accessed,
        "files_touched": files_touched,
        "closing_commit": closing_commit,
    }


# ---------- v3 metadata defaults & validator (design §16) ----------


def _confidence_source_default_for_source(source: str) -> str:
    """Map the existing `source` field to a sensible `confidence_source`
    default. The two fields are orthogonal but correlate at write time."""
    if source == "manual":
        return "user_statement"
    if source == "migration":
        return "external_import"
    if isinstance(source, str) and source.startswith("auto-"):
        return "ai_inference"
    if source == "orchestrator":
        return "ai_inference"
    return "unknown"


def _default_embedding_model_version() -> str:
    """Read $EMBED_MODEL env var (set by embed_backend's deployment config)
    or fall back to the canonical default. The env var convention matches
    `embed_backend._select_backend()`, so v3 entries written during a
    process where MLX/Ollama selected a non-default model will record that
    model id verbatim."""
    return os.environ.get("EMBED_MODEL") or DEFAULT_EMBEDDING_MODEL_VERSION


def apply_v3_defaults(
    *,
    confidence_source: str | None,
    confirmation_count: str | int | None,
    valid_until: str | None,
    causal_parent_id: str | None,
    embedding_model_version: str | None,
    domain: str | None,
    goal: str | None,
    source: str,
) -> dict[str, Any]:
    """Apply schema-v3 defaults. Returns a dict of the 7 v3 fields.

    Defaults follow design §16. CLI args arrive as strings; this helper
    coerces `confirmation_count` to int and leaves the rest as-is.
    """
    if confidence_source is None:
        confidence_source = _confidence_source_default_for_source(source)
    if confirmation_count is None:
        cc_int: int = 0
    else:
        try:
            cc_int = int(confirmation_count)
        except (TypeError, ValueError) as e:
            raise ValueError(f"confirmation_count must be int, got {confirmation_count!r}") from e
    if embedding_model_version is None:
        embedding_model_version = _default_embedding_model_version()
    if domain is None:
        domain = "unknown"
    if goal is None:
        goal = "unknown"
    return {
        "confidence_source": confidence_source,
        "confirmation_count": cc_int,
        "valid_until": valid_until,
        "causal_parent_id": causal_parent_id,
        "embedding_model_version": embedding_model_version,
        "domain": domain,
        "goal": goal,
    }


_ISO_DATE_VALIDATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})?)?$"
)


def _valid_iso_date(s: str) -> bool:
    return bool(_ISO_DATE_VALIDATE_RE.match(s))


def _validate_v3_confirmation_count(cc: Any) -> None:
    if not isinstance(cc, int) or isinstance(cc, bool):
        raise ValueError(f"confirmation_count must be int, got {type(cc).__name__}")
    if cc < 0:
        raise ValueError(f"confirmation_count must be >= 0, got {cc}")


def _validate_v3_valid_until(vu: Any) -> None:
    if vu in (None, "null", ""):
        return
    if not isinstance(vu, str) or not _valid_iso_date(vu):
        raise ValueError(f"valid_until must be ISO date or null, got {vu!r}")


def validate_v3(v3: dict[str, Any]) -> None:
    """Raise ValueError on any v3 field violation."""
    cs = v3.get("confidence_source")
    if cs not in VALID_CONFIDENCE_SOURCES:
        raise ValueError(
            f"confidence_source {cs!r} not in {sorted(VALID_CONFIDENCE_SOURCES)}"
        )
    _validate_v3_confirmation_count(v3.get("confirmation_count"))
    _validate_v3_valid_until(v3.get("valid_until"))
    emv = v3.get("embedding_model_version")
    if not isinstance(emv, str) or not emv.strip():
        raise ValueError(f"embedding_model_version must be a non-empty string, got {emv!r}")
    d = v3.get("domain")
    if d not in VALID_DOMAINS:
        raise ValueError(f"domain {d!r} not in {sorted(VALID_DOMAINS)}")
    g = v3.get("goal")
    if g not in VALID_GOALS:
        raise ValueError(f"goal {g!r} not in {sorted(VALID_GOALS)}")
    cp = v3.get("causal_parent_id")
    if cp is not None and (not isinstance(cp, str) or not cp.strip()):
        raise ValueError(f"causal_parent_id must be a non-empty string or null, got {cp!r}")


def validate_v2(v2: dict[str, Any]) -> None:
    """Raise ValueError on any v2 field violation."""
    if v2["tool"] not in VALID_TOOLS:
        raise ValueError(f"tool {v2['tool']!r} not in {sorted(VALID_TOOLS)}")
    if v2["task_category"] not in VALID_TASK_CATEGORIES:
        raise ValueError(
            f"task_category {v2['task_category']!r} not in {sorted(VALID_TASK_CATEGORIES)}"
        )
    if not isinstance(v2["files_touched"], list):
        raise ValueError("files_touched must be a list")
    for p in v2["files_touched"]:
        if not isinstance(p, str):
            raise ValueError(f"files_touched item must be string, got {type(p).__name__}")
    for f in ("project", "model", "author"):
        if not v2[f] or not isinstance(v2[f], str):
            raise ValueError(f"{f} must be a non-empty string")
