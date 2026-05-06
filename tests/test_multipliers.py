"""Unit tests for scripts/recall_multipliers.py."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from recall_multipliers import (  # noqa: E402
    STANDARD_W_COS,
    STANDARD_W_LEX,
    STANDARD_W_PPR,
    STANDARD_W_RECENCY,
    TEMPORAL_W_COS,
    TEMPORAL_W_LEX,
    TEMPORAL_W_PPR,
    TEMPORAL_W_RECENCY,
    apply_multipliers,
    combined_rerank_score,
    is_temporal_query,
    normalize_scores,
    quality_multiplier,
    recency_score,
)


# ---------------------------------------------------------------------------
# is_temporal_query
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "q, expected",
    [
        ("latest pattern", True),
        ("recent decision", True),
        ("today's recall", True),
        ("just committed", True),
        ("yesterday's bug", True),
        ("current state", True),
        ("what did I just decide", True),
        ("LATEST", True),  # case-insensitive
        ("package-level dead detection", False),
        ("Phase 4 cosine dedup", False),
        ("", False),
        (None, False),
    ],
)
def test_is_temporal_query(q, expected):
    assert is_temporal_query(q) is expected


# ---------------------------------------------------------------------------
# normalize_scores
# ---------------------------------------------------------------------------

def test_normalize_min_max():
    assert normalize_scores({"a": 0.0, "b": 1.0}) == {"a": 0.0, "b": 1.0}
    assert normalize_scores({"a": 5.0, "b": 10.0, "c": 7.5}) == {
        "a": 0.0, "b": 1.0, "c": 0.5,
    }


def test_normalize_all_equal_collapses_to_one():
    assert normalize_scores({"a": 5.0, "b": 5.0}) == {"a": 1.0, "b": 1.0}


def test_normalize_empty():
    assert normalize_scores({}) == {}


# ---------------------------------------------------------------------------
# recency_score
# ---------------------------------------------------------------------------

def test_recency_at_known_ages():
    """0/30/90/365 days → 1.0 / ~0.79 / 0.5 / ~0.06 (per spec)."""
    now = datetime(2026, 5, 6, tzinfo=timezone.utc)
    cases = [(0, 1.0), (30, 0.794), (90, 0.5), (365, 0.060)]
    for days, expected in cases:
        actual = recency_score(now - timedelta(days=days), now=now)
        assert abs(actual - expected) < 0.005, (
            f"{days}d age → {actual:.4f}, expected ~{expected}"
        )


def test_recency_handles_iso_string():
    now = datetime(2026, 5, 6, tzinfo=timezone.utc)
    iso_30d = (now - timedelta(days=30)).isoformat()
    assert abs(recency_score(iso_30d, now=now) - 0.794) < 0.005


def test_recency_handles_zulu_string():
    now = datetime(2026, 5, 6, tzinfo=timezone.utc)
    zulu = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert abs(recency_score(zulu, now=now) - 0.794) < 0.005


def test_recency_unparseable_returns_neutral():
    assert recency_score("not a date") == 0.35
    assert recency_score(None) == 0.35
    assert recency_score(12345) == 0.35  # not str / datetime


def test_recency_naive_datetime_treated_as_utc():
    now = datetime(2026, 5, 6, tzinfo=timezone.utc)
    naive = datetime(2026, 4, 6)  # 30 days back, no tz
    assert abs(recency_score(naive, now=now) - 0.794) < 0.005


# ---------------------------------------------------------------------------
# combined_rerank_score
# ---------------------------------------------------------------------------

def test_standard_weights_match_constants():
    """Build-loop bumped lex from 0.40 → 0.50 vs vault_vector.py."""
    assert STANDARD_W_COS == 0.45
    assert STANDARD_W_LEX == 0.50
    assert STANDARD_W_PPR == 0.07
    assert STANDARD_W_RECENCY == 0.08


def test_temporal_weights_match_constants():
    assert TEMPORAL_W_COS == 0.42
    assert TEMPORAL_W_LEX == 0.35
    assert TEMPORAL_W_PPR == 0.07
    assert TEMPORAL_W_RECENCY == 0.16


def test_combined_standard_path():
    cos = {"A": 1.0}
    lex = {"A": 0.0}
    ppr = {"A": 0.0}
    s = combined_rerank_score(pid="A", query="generic query", cos_norm=cos, lex_norm=lex, ppr_norm=ppr, recency=0.0)
    assert abs(s - 0.45) < 1e-9


def test_combined_temporal_path():
    cos = {"A": 1.0}
    lex = {"A": 0.0}
    ppr = {"A": 0.0}
    s = combined_rerank_score(pid="A", query="latest decision", cos_norm=cos, lex_norm=lex, ppr_norm=ppr, recency=1.0)
    # 0.42 * 1 + 0.35 * 0 + 0.07 * 0 + 0.16 * 1 = 0.58
    assert abs(s - 0.58) < 1e-9


def test_combined_missing_pid_treated_as_zero():
    s = combined_rerank_score(pid="MISSING", query="q", cos_norm={}, lex_norm={}, ppr_norm={}, recency=0.5)
    assert abs(s - (STANDARD_W_RECENCY * 0.5)) < 1e-9


# ---------------------------------------------------------------------------
# quality_multiplier
# ---------------------------------------------------------------------------

def test_quality_active_explicit_is_neutral():
    assert quality_multiplier({"status": "active", "confidence": 1.0}) == 1.0


def test_quality_draft_status():
    assert quality_multiplier({"status": "draft", "confidence": 1.0}) == 0.7


def test_quality_low_numeric_confidence():
    assert quality_multiplier({"status": "active", "confidence": 0.25}) == 0.7


def test_quality_tentative_label_in_metadata():
    row = {"status": "active", "confidence": 1.0, "metadata": {"confidence": "tentative"}}
    assert abs(quality_multiplier(row) - 0.7) < 1e-9


def test_quality_compounding_tentative_draft():
    row = {"status": "draft", "confidence": 1.0, "metadata": {"confidence": "tentative"}}
    assert abs(quality_multiplier(row) - 0.49) < 1e-9


def test_quality_superseded():
    assert quality_multiplier({"status": "superseded", "confidence": 1.0}) == 0.3


def test_quality_no_double_count_numeric_and_label():
    """A row with numeric confidence < 0.5 AND label='tentative' must
    only get penalized once, not twice."""
    row = {"status": "active", "confidence": 0.25, "metadata": {"confidence": "tentative"}}
    assert abs(quality_multiplier(row) - 0.7) < 1e-9


def test_quality_string_metadata_jsonb_roundtrip():
    """JSONB sometimes round-trips as a string; tolerate."""
    row = {"status": "active", "confidence": 1.0, "metadata": '{"confidence": "tentative"}'}
    assert abs(quality_multiplier(row) - 0.7) < 1e-9


def test_quality_missing_fields_neutral():
    assert quality_multiplier({}) == 1.0


# ---------------------------------------------------------------------------
# apply_multipliers integration
# ---------------------------------------------------------------------------

def test_apply_multipliers_active_beats_draft_at_equal_score():
    now = datetime(2026, 5, 6, tzinfo=timezone.utc)
    rows = [
        {"id": "X", "score": 1.0, "status": "active", "confidence": 1.0, "valid_from": now.isoformat()},
        {"id": "Y", "score": 1.0, "status": "draft", "confidence": 1.0, "valid_from": now.isoformat()},
    ]
    out = apply_multipliers(rows, query="generic", now=now)
    assert out[0]["id"] == "X"
    assert out[1]["id"] == "Y"
    assert abs(out[0]["_quality_mult"] - 1.0) < 1e-9
    assert abs(out[1]["_quality_mult"] - 0.7) < 1e-9


def test_apply_multipliers_temporal_query_uses_temporal_recency():
    now = datetime(2026, 5, 6, tzinfo=timezone.utc)
    rows = [
        {"id": "X", "score": 0.0, "status": "active", "confidence": 1.0, "valid_from": now.isoformat()},
    ]
    out = apply_multipliers(rows, query="latest pattern", now=now)
    # base 0 + 0.16 * 1.0 (fresh) * 1.0 (quality) = 0.16
    assert abs(out[0]["score"] - 0.16) < 1e-9
    assert out[0]["_temporal_query"] is True


def test_apply_multipliers_returns_new_dicts():
    """Verify no mutation of input."""
    now = datetime(2026, 5, 6, tzinfo=timezone.utc)
    rows = [{"id": "X", "score": 1.0, "status": "active", "confidence": 1.0, "valid_from": now.isoformat()}]
    out = apply_multipliers(rows, query="q", now=now)
    assert "_quality_mult" not in rows[0]
    assert "_quality_mult" in out[0]


def test_apply_multipliers_empty_list():
    assert apply_multipliers([], "q") == []
