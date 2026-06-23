# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the coordination decay/reclaim core (Python mirror of decay.rs).

The golden vectors in ``decay_vectors.json`` are an IDENTICAL copy of the
fixture used by the Rust suite, so a divergence between the two implementations
fails one of the two suites.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import decay  # noqa: E402

_HL_SECS = int(decay.DEFAULT_HALF_LIFE_HOURS) * 3600  # 48h


def _vectors() -> dict:
    return json.loads(
        (Path(__file__).parent / "decay_vectors.json").read_text(encoding="utf-8")
    )


# ---- recency_weight against the shared golden vectors ----
def test_weight_matches_golden_vectors():
    v = _vectors()
    hl = v["half_life_secs"]
    for row in v["weights"]:
        w = decay.recency_weight(row["age_secs"], hl)
        assert abs(w - row["expected"]) < 1e-4, (
            f"age {row['age_secs']}s: got {w}, expected {row['expected']}"
        )


def test_archive_floor_against_golden_vectors():
    v = _vectors()
    floor = v["archive_floor"]
    for row in v["archive_floor_cases"]:
        assert decay.is_archivable(row["weight"], floor) is row["archivable"], (
            f"weight {row['weight']} vs floor {floor}"
        )


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
