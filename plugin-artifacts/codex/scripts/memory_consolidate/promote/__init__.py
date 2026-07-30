#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Promote: recurrence-gated project→global promotion.

P3 promotion arm. A lesson is promoted from a project lane to a global
lane ONLY when **EARNED** — it recurs across ``≥N`` distinct projects
(configurable, default 2) OR demonstrably generalizes. Promotion is
NEVER self-declared; a one-off single-project lesson is REJECTED by
the gate (``rejected: "single-project"``).

Strategy: walk on-disk project lessons, for each query the P1 dense
recall to find semantically-similar lessons in OTHER projects. A
PromotionCandidate gathers every cross-project sibling found above
``threshold``. The ``promotion_gate`` returns ``accepted`` only when
``distinct_projects >= min_projects``.

Per "host agent is the LLM": no vendor calls. The host LLM reads a
promotion packet to optionally refine lane + name + summary; the
deterministic decision is enough for CI/headless.
"""
from __future__ import annotations

from .promote import (  # noqa: F401
    PromotionCandidate,
    PromotionDecision,
    PromotionPacket,
    find_promotion_candidates,
    promotion_gate,
    prepare_promotion_packet,
    heuristic_promotion_decision,
)

__all__ = [
    "PromotionCandidate",
    "PromotionDecision",
    "PromotionPacket",
    "find_promotion_candidates",
    "heuristic_promotion_decision",
    "prepare_promotion_packet",
    "promotion_gate",
]
