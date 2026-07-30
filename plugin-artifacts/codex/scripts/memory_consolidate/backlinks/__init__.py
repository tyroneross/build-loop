#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backlinks: generate ``[[name]]`` links between semantically-related entries.

Reuses the P1 hybrid recall tier to find related siblings and writes a
surgical ``## Related`` footer to each entry — append-only, idempotent.
Single source of truth for the backlink format is ``[[<name>]]`` (the
Karpathy LLM-Wiki convention).
"""
from __future__ import annotations

from .backlinks import (  # noqa: F401
    BacklinkPair,
    BacklinkSuggestion,
    extract_existing_backlinks,
    find_related_entries,
    propose_backlinks,
    write_backlinks_footer,
)

__all__ = [
    "BacklinkPair",
    "BacklinkSuggestion",
    "extract_existing_backlinks",
    "find_related_entries",
    "propose_backlinks",
    "write_backlinks_footer",
]
