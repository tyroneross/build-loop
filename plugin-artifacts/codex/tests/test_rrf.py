"""Unit tests for scripts/rrf.py — Reciprocal Rank Fusion."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rrf import DEFAULT_K, annotate_leg_membership, rrf_fuse  # noqa: E402


# ---------------------------------------------------------------------------
# Canonical RRF math
# ---------------------------------------------------------------------------

def test_canonical_two_legs():
    """Two legs, one item appears in both — score = sum of 1/(k+rank)."""
    leg_v = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
    leg_k = [{"id": "D"}, {"id": "E"}, {"id": "A"}]
    fused = rrf_fuse([leg_v, leg_k], k=60)

    expected_a = 1 / (60 + 1) + 1 / (60 + 3)
    a_row = next(r for r in fused if r["id"] == "A")
    assert abs(a_row["score"] - expected_a) < 1e-9
    # A beats every singleton because it has two contributions.
    assert fused[0]["id"] == "A"


def test_default_k_is_60():
    """Spec / Cormack 2009 / Example App all use k=60."""
    assert DEFAULT_K == 60


def test_singleton_legs():
    """One leg, one item — score is 1/(k+1)."""
    fused = rrf_fuse([[{"id": "A"}]], k=60)
    assert len(fused) == 1
    assert abs(fused[0]["score"] - 1 / 61) < 1e-12


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_legs_returns_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []
    assert rrf_fuse([[], []]) == []


def test_missing_id_dropped():
    """Items without an `id` field are silently skipped, not crashed."""
    fused = rrf_fuse([[{"id": "A"}, {"no_id_here": "x"}, {"id": "B"}]])
    ids = {r["id"] for r in fused}
    assert ids == {"A", "B"}


def test_first_seen_wins_on_duplicate_id():
    """When the same id appears with different metadata across legs,
    the first-seen record wins. Score still accumulates from both."""
    leg_v = [{"id": "A", "obj": "from-vector"}]
    leg_k = [{"id": "A", "obj": "from-keyword"}]
    fused = rrf_fuse([leg_v, leg_k])
    assert len(fused) == 1
    assert fused[0]["obj"] == "from-vector"
    # Score is sum of contributions from both legs.
    assert abs(fused[0]["score"] - (1 / 61 + 1 / 61)) < 1e-12


def test_ties_ordering_is_deterministic():
    """Two items with identical scores must produce a stable order
    (Python's sort is stable; we rely on that)."""
    fused1 = rrf_fuse([[{"id": "A"}, {"id": "B"}]])
    fused2 = rrf_fuse([[{"id": "A"}, {"id": "B"}]])
    assert [r["id"] for r in fused1] == [r["id"] for r in fused2]


def test_preserves_leg_score_under_underscore_key():
    """Pre-RRF score (e.g. cosine_sim) is preserved as `_leg_score`."""
    leg = [{"id": "A", "score": 0.95}]
    fused = rrf_fuse([leg])
    assert fused[0]["_leg_score"] == 0.95
    # New score is the RRF score, not the input score.
    assert fused[0]["score"] != 0.95


def test_limit_slices_correctly():
    leg = [{"id": str(i)} for i in range(20)]
    assert len(rrf_fuse([leg], limit=5)) == 5
    assert len(rrf_fuse([leg], limit=0)) == 0
    assert len(rrf_fuse([leg], limit=None)) == 20


def test_non_dict_items_skipped():
    """Defensive: the function accepts a Sequence[Sequence[dict]] but
    real-world payloads can carry None / str / int. Skip, don't crash."""
    fused = rrf_fuse([[None, "string", 42, {"id": "A"}]])
    assert len(fused) == 1
    assert fused[0]["id"] == "A"


# ---------------------------------------------------------------------------
# annotate_leg_membership
# ---------------------------------------------------------------------------

def test_annotate_membership_named_legs():
    leg_v = [{"id": "A"}, {"id": "B"}]
    leg_k = [{"id": "B"}, {"id": "C"}]
    fused = rrf_fuse([leg_v, leg_k])
    annotated = annotate_leg_membership(
        fused, [leg_v, leg_k], leg_names=["vector", "sparse"]
    )
    by_id = {r["id"]: r for r in annotated}
    assert by_id["A"]["legs"] == ["vector"]
    assert sorted(by_id["B"]["legs"]) == ["sparse", "vector"]
    assert by_id["C"]["legs"] == ["sparse"]


def test_annotate_default_leg_names():
    leg = [{"id": "A"}]
    annotated = annotate_leg_membership(rrf_fuse([leg]), [leg])
    assert annotated[0]["legs"] == ["leg_0"]


def test_annotate_mismatched_names_raises():
    with pytest.raises(ValueError):
        annotate_leg_membership([], [[]], leg_names=["a", "b"])
