#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle: Karpathy LLM-Wiki states for memory entries.

States (per the research lesson):
    draft → active → stale → contradicted → archived

Transitions are wired to the EXISTING primitives where they apply —
``detect_decision_rot``, ``supersede_decision``, ``revoke_decision`` —
this module does NOT reinvent rot/supersede/revoke. For lessons (and
other non-decision types) the transition is determined by structural
rules: clean lint + non-empty body → active; source hash mismatch →
stale; explicit supersede/revoke event → contradicted/archived.

Public surface:
    classify_state(path, *, threshold_days, prev_source_hash) -> StateClassification
    list_lifecycle_transitions(workdir, *, memory_root) -> list[StateTransition]
    apply_state_to_frontmatter(path, state, *, reason, dry_run) -> dict
"""
from __future__ import annotations

from .lifecycle import (  # noqa: F401
    LIFECYCLE_STATES,
    StateClassification,
    StateTransition,
    apply_state_to_frontmatter,
    classify_state,
    list_lifecycle_transitions,
    compute_source_hash,
)

__all__ = [
    "LIFECYCLE_STATES",
    "StateClassification",
    "StateTransition",
    "apply_state_to_frontmatter",
    "classify_state",
    "compute_source_hash",
    "list_lifecycle_transitions",
]
