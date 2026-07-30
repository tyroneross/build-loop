#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_facade._q_match — the recall token matcher.

Regression: a full-phrase substring match silently dropped every canonical
decision/lesson for realistic multi-word goal queries (audit 2026-05-31,
canonical_memory.merged=0 on every real run). Token-OR matching fixes it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from memory_facade.common import _q_match  # noqa: E402


def test_empty_query_matches_everything():
    assert _q_match("anything at all", "") is True
    assert _q_match("anything", "   ") is True


def test_single_token_substring_still_matches():
    assert _q_match("decision: rally coordination", "rally") is True


def test_regression_multiword_goal_query_now_matches_on_any_token():
    # The exact failure class from the audit: a natural-language goal query whose
    # full phrase never appears verbatim, but individual tokens do.
    text = "decision-project-build-loop: background snapshot handoff; tags: context, rally"
    query = "background snapshot polished B handoff context"
    # Old behavior (full phrase) => False (dropped). New behavior (token-OR) => True.
    assert _q_match(text, query) is True


def test_no_token_matches_returns_false():
    assert _q_match("decision about telemetry and otel", "kubernetes helm istio") is False


def test_case_insensitive():
    assert _q_match("Rally Coordination Decision", "rally") is True
    assert _q_match("rally coordination", "RALLY") is True


def test_none_text_is_safe():
    assert _q_match(None, "rally") is False
    assert _q_match(None, "") is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
