#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Raw-candidate intake + consolidation + async distill/promote/lifecycle/backlinks
for build-loop-memory.

Hot-path (P2 — submit/place; called from agent return / Stop):
  * ``intake`` — queue contract; agents drop candidates here.
  * ``classify`` — builds a host-LLM consolidation packet.
  * ``place`` — writes the candidate through the writer guard.

Async / off-hot-path (P3 — distill/promote/lifecycle/backlinks):
  * ``distill`` — group similar placed entries via P1 hybrid recall.
  * ``promote`` — recurrence-gated project→global promotion.
  * ``lifecycle`` — Karpathy states (draft/active/stale/contradicted/archived).
  * ``backlinks`` — surgical ``[[name]]`` link generation.
  * ``async_runner`` — chain the four arms; invoked by cron / a manual
    ``memory_consolidate async``.

All P3 calls reuse the P1 hybrid recall (no second similarity engine) and
the existing rot/supersede/revoke primitives where relevant — KISS+DRY.

Per "host agent is the LLM" — every classifier/distiller/promoter builds
structured data; vendor APIs are never called. ``deterministic-only``
modes drive CI / headless.
"""
from __future__ import annotations

from .intake import (  # noqa: F401
    Candidate,
    PENDING_DIR,
    PLACED_DIR,
    REJECTED_DIR,
    list_pending,
    load_candidate,
    submit,
)
from .classify import (  # noqa: F401
    ConsolidationPacket,
    heuristic_decision,
    prepare,
)
from .place import place as place_candidate
from .place import reject  # noqa: F401

__all__ = [
    "Candidate",
    "ConsolidationPacket",
    "PENDING_DIR",
    "PLACED_DIR",
    "REJECTED_DIR",
    "heuristic_decision",
    "list_pending",
    "load_candidate",
    "place_candidate",
    "reject",
    "prepare",
    "submit",
]

# NOTE: distill / promote / lifecycle / backlinks / async_runner are NOT
# re-exported from the package root. They are intentionally lazy-loaded
# (imported via ``from memory_consolidate import distill`` only by the
# async_runner + CLI subcommands), so importing ``memory_consolidate``
# does not pull the four arms into ``sys.modules``. That guarantee is
# enforced by ``test_async_runner.test_intake_module_imports_without_loading_arms``.
