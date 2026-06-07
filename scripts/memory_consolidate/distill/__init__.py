#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Distill: async fact extraction + cosine dedup for placed candidates.

P3 consolidation arm — MemTier Phase 2a. Reads ``placed/`` consolidation
candidates and the on-disk semantic facts they produced; groups
semantically-similar entries via the P1 hybrid recall tier; builds a
**distillation packet** the host LLM reads to emit a distilled
project-semantic entry. NEVER called from the Stop / Phase 6 hot path;
invoked by ``memory_consolidate async`` (cron-style).

Per "host agent is the LLM" — no vendor API calls. Deterministic
fallback (``heuristic_distill``) merges duplicates by structural rules
when no host is in the loop.
"""
from __future__ import annotations

from .distill import (  # noqa: F401
    DistillCluster,
    DistillPacket,
    cluster_similar,
    find_distill_candidates,
    heuristic_distill,
    prepare_distill_packet,
)

__all__ = [
    "DistillCluster",
    "DistillPacket",
    "cluster_similar",
    "find_distill_candidates",
    "heuristic_distill",
    "prepare_distill_packet",
]
