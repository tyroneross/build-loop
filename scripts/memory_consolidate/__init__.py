#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Raw-candidate intake + consolidation for build-loop-memory.

P2 ingestion arm. Agents NEVER pick a memory path directly. They drop a
raw candidate via ``intake.submit()`` (or ``python3 -m memory_consolidate
submit ...``); a consolidator then:

  1. Loads the candidate.
  2. Queries the P1 dense recall (``semantic_index.query_facts(mode='hybrid')``)
     for similar existing entries to surface dedup + backlink suggestions.
  3. Builds a structured packet the host agent (Claude Code, Codex, ...)
     reads — per "host agent is the LLM" rule: NO vendor API calls here.
     The host reads the packet, picks lane + type + backlinks, returns a
     decision JSON.
  4. ``place.execute()`` files the candidate via ``memory_writer.write()``
     through the P2 writer guard, so even a misformed lane path is corrected
     before disk.

A ``--deterministic-only`` mode skips step 3 and uses heuristic defaults so
headless tests (and end-to-end CI) run without a host agent in the loop.

Public surface:
    submit(content, hint=None, *, project=None, workdir=".", run_id, host)
    list_pending() -> list[Candidate]
    prepare(candidate_id) -> ConsolidationPacket  # the host agent reads this
    place(candidate_id, decision) -> dict          # final frontmatter dict

All routes call ``memory_writer.write()`` — the guard is the single source of
truth for path safety. The consolidator is a thin assembly of three units:
``intake.py`` (queue), ``classify.py`` (packet prep + heuristic decision),
``place.py`` (guarded write).
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
