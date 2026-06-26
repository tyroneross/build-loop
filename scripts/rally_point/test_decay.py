# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the in-process decay/reclaim math helpers (``decay.py``).

The cross-repo golden-fixture parity coupling (``decay_vectors.json``, byte-
identical to the Rust fixture) was RETIRED in the Rust-rally facade migration:
coordination policy is Rust-only and the Python decay math is now just an
in-process helper for window/weight computation, not a behavioral mirror that
must match a foreign suite byte-for-byte. These tests pin the helper's own
contract with inline reference values.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import decay  # noqa: E402

_HL_SECS = int(decay.DEFAULT_HALF_LIFE_HOURS) * 3600  # 48h


# ---- recency_weight reference values ----
def test_weight_reference_values():
    # weight = 0.5 ** (age_hours / half_life_hours); half-life = 48h.
    assert abs(decay.recency_weight(0, _HL_SECS) - 1.0) < 1e-4
    assert abs(decay.recency_weight(_HL_SECS, _HL_SECS) - 0.5) < 1e-4       # 1 half-life
    assert abs(decay.recency_weight(2 * _HL_SECS, _HL_SECS) - 0.25) < 1e-4  # 2 half-lives


def test_archive_floor_reference_cases():
    floor = decay.DEFAULT_ARCHIVE_FLOOR  # 0.05
    assert decay.is_archivable(0.04, floor) is True
    assert decay.is_archivable(0.06, floor) is False
    assert decay.is_archivable(0.05, floor) is False  # strict <


def test_weight_monotonic_decreasing():
    fresh = decay.recency_weight(0, _HL_SECS)
    day3 = decay.recency_weight(3 * 24 * 3600, _HL_SECS)
    day7 = decay.recency_weight(7 * 24 * 3600, _HL_SECS)
    assert fresh > day3 > day7


def test_weight_clamps_negative_age():
    assert decay.recency_weight(-100, _HL_SECS) == 1.0


def test_weight_nonpositive_half_life_falls_back():
    w = decay.recency_weight(2 * 24 * 3600, 0)
    assert abs(w - 0.5) < 1e-4


def test_archive_floor_strict_boundary():
    floor = decay.DEFAULT_ARCHIVE_FLOOR
    assert decay.is_archivable(floor + 0.0001, floor) is False
    assert decay.is_archivable(floor, floor) is False  # strict <
    assert decay.is_archivable(floor - 0.0001, floor) is True


def test_fourteen_days_is_archivable():
    w = decay.recency_weight(14 * 24 * 3600, _HL_SECS)
    assert decay.is_archivable(w, decay.DEFAULT_ARCHIVE_FLOOR)


# ---- reclaim timeout: small/large, just-under/just-over ----
def test_reclaim_timeout_small_and_large():
    small = decay.reclaim_timeout_seconds(decay.SMALL)
    large = decay.reclaim_timeout_seconds(decay.LARGE)
    assert small == 30 * 60
    assert large == 120 * 60
    # single-file claim silent 31m reclaimable, 29m not.
    assert 31 * 60 > small
    assert 29 * 60 < small
    assert 121 * 60 > large
    assert 119 * 60 < large


def test_reclaim_timeout_custom_minutes():
    assert decay.reclaim_timeout_seconds(decay.SMALL, small_minutes=10) == 600
    assert decay.reclaim_timeout_seconds(decay.LARGE, large_minutes=60) == 3600


# ---- work-size classification ----
def test_classify_single_file_is_small():
    assert decay.classify_work_size(scope_paths=["src/a.py"]) == decay.SMALL


def test_classify_multi_file_is_large():
    assert decay.classify_work_size(scope_paths=["src/a.py", "src/b.py"]) == decay.LARGE


def test_classify_empty_scope_is_large():
    assert decay.classify_work_size(scope_paths=[]) == decay.LARGE


def test_classify_no_signal_is_large():
    assert decay.classify_work_size() == decay.LARGE


def test_classify_effort_grades():
    assert decay.classify_work_size(effort="XS") == decay.SMALL
    assert decay.classify_work_size(effort="S") == decay.SMALL
    assert decay.classify_work_size(effort="M") == decay.LARGE
    assert decay.classify_work_size(effort="L") == decay.LARGE
    assert decay.classify_work_size(effort="XL") == decay.LARGE


def test_effort_wins_over_scope_when_both_given():
    # effort XS (small) decisive even though 2 paths would say large.
    assert (
        decay.classify_work_size(effort="XS", scope_paths=["a", "b"]) == decay.SMALL
    )
