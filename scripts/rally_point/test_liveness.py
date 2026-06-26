# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the in-process adaptive multi-signal liveness math.

The cross-repo golden-fixture parity coupling (``liveness_vectors.json``, byte-
identical to the Rust fixture) was RETIRED in the Rust-rally facade migration:
liveness policy is Rust-only, and the Python liveness math is now an in-process
helper for window computation, not a behavioral mirror that must match a foreign
suite byte-for-byte. These inline-value tests cover the same window /
``is_live`` / ``reapable`` / self-exit logic the fixtures used to assert.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import liveness  # noqa: E402
from liveness import (  # noqa: E402
    DEFAULT_CADENCE_SECS,
    GRACE_SECS,
    MISS_MULTIPLIER,
    LivenessSignals,
    adaptive_window_secs,
    completion_self_exit_eligible,
    is_live,
    reapable,
)


def test_five_min_cadence_window_is_31_minutes():
    assert adaptive_window_secs(300) == 31 * 60


def test_five_hour_cadence_window_exceeds_30h():
    w = adaptive_window_secs(18000)
    assert w == 18000 * 6 + 60
    assert w > 30 * 3600


def test_nonpositive_interval_falls_back_to_default():
    assert adaptive_window_secs(0) == adaptive_window_secs(DEFAULT_CADENCE_SECS)
    # both interval and default non-positive -> pinned const
    assert adaptive_window_secs(-5, -5) == DEFAULT_CADENCE_SECS * MISS_MULTIPLIER + GRACE_SECS


def test_multiplier_and_grace_clamped():
    assert adaptive_window_secs(100, 300, 0, -10) == 100
    assert adaptive_window_secs(100, 300, -3, 50) == 150


def test_each_signal_independently_keeps_alive():
    window = 1860
    builders = [
        lambda a: LivenessSignals(heartbeat_age=a),
        lambda a: LivenessSignals(inject_age=a),
        lambda a: LivenessSignals(code_progress_age=a),
        lambda a: LivenessSignals(plan_age=a),
    ]
    for build in builders:
        assert is_live(build(10), window) == liveness.LIVE
        # the same single signal stale, others absent -> Unknown (fail-open)
        assert is_live(build(window + 1), window) == liveness.UNKNOWN


def test_all_present_and_stale_is_stale():
    window = 1860
    s = LivenessSignals(
        heartbeat_age=window + 1,
        inject_age=window + 1,
        code_progress_age=window + 1,
        plan_age=window + 1,
    )
    assert is_live(s, window) == liveness.STALE


def test_all_absent_is_unknown_failopen():
    assert is_live(LivenessSignals(), 1860) == liveness.UNKNOWN


def test_boundary_at_window_is_fresh():
    window = 1860
    assert is_live(LivenessSignals(heartbeat_age=window), window) == liveness.LIVE
    assert is_live(LivenessSignals(heartbeat_age=window + 1), window) == liveness.UNKNOWN


# --- Layer 1/2/3 reaper/self-exit decision policy (in-process helpers) ---


def test_reapable_never_reaps_live_or_unknown():
    for parent in (True, False, None):
        assert reapable(liveness.LIVE, parent) is False
        assert reapable(liveness.UNKNOWN, parent) is False


def test_reapable_stale_parent_dead_reaped_alive_kept():
    assert reapable(liveness.STALE, False) is True   # stale + dead parent → reap
    assert reapable(liveness.STALE, True) is False   # stale + live parent → keep
    assert reapable(liveness.STALE, None) is True    # stale + no info → window alone


def test_self_exit_requires_resolved_and_sustained_empty():
    assert completion_self_exit_eligible(True, 2, 2, False) is True
    assert completion_self_exit_eligible(False, 100, 2, False) is False  # mid-task
    assert completion_self_exit_eligible(True, 1, 2, False) is False     # streak short
    assert completion_self_exit_eligible(True, 100, 2, True) is False    # opt-out
    assert completion_self_exit_eligible(True, 1, 0, False) is True      # clamp to 1
    assert completion_self_exit_eligible(True, 0, 0, False) is False
