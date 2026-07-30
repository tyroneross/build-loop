#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Constants, vocab enums, the writer lock, and shared atomic primitives.

Vocabulary sets and the ``LockedFile`` lock are split out of the historical
flat ``write_decision.py`` so each responsibility lives in its own module
while staying byte-for-byte identical in behaviour.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Local resolver imports — must precede any default-arg evaluation.  When run
# as ``python3 scripts/write_decision/__main__.py`` the package dir is
# sys.path[0]; when imported via ``from write_decision import ...`` __init__
# has already inserted both the package dir and scripts/.
HERE = Path(__file__).resolve().parent
_SCRIPTS = HERE.parent
for _p in (str(HERE), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from atomic_io import LockedFile as _LockedFile  # type: ignore  # noqa: E402
from atomic_io import atomic_write_bytes  # type: ignore  # noqa: E402,F401

LOCK_TIMEOUT_S = 15
CONFIDENCE_ORDER = {"assumed": 0, "inferred": 1, "confirmed": 2, "explicit": 3}
VALID_CONFIDENCES = set(CONFIDENCE_ORDER)
VALID_STATUSES = {"proposed", "accepted", "superseded", "rejected"}
VALID_SOURCES = {
    "manual",
    "auto-explicit",
    "auto-confirmed",
    "auto-inferred",
    "auto-assumed",
    "migration",
    "orchestrator",
}
VALID_TYPES = {"decision", "issue", "research"}
# v2 metadata enums (added 2026-05-04, design §15).
# `tool` and `task_category` are closed; `model`, `author`, `project` are free-form.
VALID_TOOLS = {
    "claude-code",
    "codex",
    "cursor",
    "aider",
    "goose",
    "manual",
    "migration",
    "unknown",
}
VALID_TASK_CATEGORIES = {
    "feature",
    "bugfix",
    "refactor",
    "research",
    "docs",
    "migration",
    "experiment",
    "config",
    "unknown",
}

# v3 metadata enums (added 2026-05-04, design §16).
# `confidence_source` decouples *who asserted* from *how it was captured*
# (the pre-existing `source` field still records the capture mechanism).
VALID_CONFIDENCE_SOURCES = {
    "user_statement",
    "ai_inference",
    "tool_extraction",
    "external_import",
    "unknown",
}
# `domain` is a stricter MECE axis than `primary_tag` (which stays as a
# legacy alias). Keep this enum closed; expand only with a TAXONOMY bump.
VALID_DOMAINS = {
    "ui",
    "api",
    "data",
    "search",
    "auth",
    "build",
    "infra",
    "tooling",
    "docs",
    "test",
    "meta",
    "unknown",
}
# `goal` captures *why* the work was done — orthogonal to domain.
VALID_GOALS = {
    "user-value",
    "reliability",
    "performance",
    "security",
    "dev-velocity",
    "maintainability",
    "compliance",
    "learning",
    "unknown",
}

# Default embedding model version. Re-embed is required when the active
# backend's model changes; the version stamp is what makes that detectable.
#
# Static fallback only — call sites that need accuracy should call
# `embed_backend.active_model()` after at least one embed has run, which
# returns the actual model the active backend is configured to use.
# The literal here covers two cases: (1) tests that don't exercise the
# embedder at all, and (2) doc-only writes that pre-date the bge-m3
# Phase A migration. Phase A's Ollama default is bge-m3; the MLX default
# remains mxbai-embed-large-v1.
DEFAULT_EMBEDDING_MODEL_VERSION = "mxbai-embed-large-v1"

VALID_EVENT_KINDS = {
    "run_completed",
    "run_failed",
    "decision_proposed",
    "decision_accepted",
    "decision_superseded",
    "decision_revoked",
    "issue_opened",
    "issue_closed",
    "library_added",
    "library_bumped",
    "library_removed",
    "architecture_component_added",
    "architecture_component_removed",
    "manual_intervention",
    "escalation",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------- atomic primitives ----------
# LockedFile + atomic_write_bytes are shared via scripts/atomic_io.py. This
# module (and its importers revoke_decision / scan_transcript_for_decisions /
# regenerate_knowledge_index / migrate_playbooks_to_procedural) historically
# used a 15s lock timeout, so bind that default here while reusing the shared
# implementation. atomic_write_bytes is re-exported above for those importers.


class LockedFile(_LockedFile):
    """Decision-writer lock with this module's historical 15s default."""

    def __init__(self, target: Path, timeout_s: float = LOCK_TIMEOUT_S) -> None:
        super().__init__(target, timeout_s)
