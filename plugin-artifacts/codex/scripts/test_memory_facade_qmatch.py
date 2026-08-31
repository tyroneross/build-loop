#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_facade._q_match — the recall token matcher.

Regression: a full-phrase substring match silently dropped every canonical
decision/lesson for realistic multi-word goal queries (audit 2026-05-31,
canonical_memory.merged=0 on every real run). Token-OR matching fixes it.
"""
from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path
from unittest.mock import patch

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


def test_default_mode_is_byte_identical_to_legacy_token_or(monkeypatch):
    monkeypatch.delenv("BUILD_LOOP_MEMORY_MATCH", raising=False)
    text = "main domain explain chain"
    query = "ai unrelated"
    expected = any(t in text.lower() for t in query.lower().split() if t)

    assert _q_match(text, query) is expected


def test_boundary_mode_rejects_short_substring_accidents():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        assert _q_match("main domain explain chain", "ai") is False


def test_legacy_mode_keeps_short_substring_accidents():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "legacy"}):
        assert _q_match("main domain explain chain", "ai") is True


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("release migrations safely", "migration"),
        ("deployment checklist", "deploy"),
    ],
)
def test_boundary_mode_allows_prefix_morphology(text, query):
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        assert _q_match(text, query) is True


def test_boundary_stopword_only_query_matches_everything():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        assert _q_match("unrelated memory", "the of a") is True


@pytest.mark.parametrize("mode", ["legacy", "boundary"])
def test_empty_query_matches_everything_in_both_modes(mode):
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": mode}):
        assert _q_match("anything at all", "") is True


def test_unknown_mode_falls_back_to_legacy():
    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "experimental"}):
        assert _q_match("main domain explain chain", "ai") is True


def test_boundary_import_failure_falls_back_to_legacy():
    real_import = builtins.__import__

    def fail_memory_rank_import(name, *args, **kwargs):
        if name == "memory_rank":
            raise ImportError("simulated memory_rank import failure")
        return real_import(name, *args, **kwargs)

    with patch.dict(os.environ, {"BUILD_LOOP_MEMORY_MATCH": "boundary"}):
        with patch("builtins.__import__", side_effect=fail_memory_rank_import):
            assert _q_match("main domain explain chain", "ai") is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
