# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the adaptive multi-signal liveness mirror.

The headline test asserts the SAME byte-identical golden fixture
(``liveness_vectors.json``) the Rust suite asserts — the cross-repo parity proof.
"""
from __future__ import annotations

import json
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
    is_live,
)

_FIXTURE = Path(__file__).resolve().parent / "liveness_vectors.json"


def _load() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_fixture_constants_match_module():
    v = _load()
    assert v["default_cadence_secs"] == DEFAULT_CADENCE_SECS
    assert v["miss_multiplier"] == MISS_MULTIPLIER
    assert v["grace_secs"] == GRACE_SECS


def test_window_cases_match_golden_vectors():
    v = _load()
    for case in v["window_cases"]:
        got = adaptive_window_secs(
            case["planned_interval_secs"],
            v["default_cadence_secs"],
            v["miss_multiplier"],
            v["grace_secs"],
        )
        assert got == case["expected_window_secs"], case["name"]


def test_liveness_cases_match_golden_vectors():
    v = _load()
    for case in v["liveness_cases"]:
        sig = case["signals"]
        signals = LivenessSignals(
            heartbeat_age=sig["heartbeat_age"],
            inject_age=sig["inject_age"],
            code_progress_age=sig["code_progress_age"],
            plan_age=sig["plan_age"],
        )
        window = adaptive_window_secs(
            case["planned_interval_secs"],
            v["default_cadence_secs"],
            v["miss_multiplier"],
            v["grace_secs"],
        )
        assert is_live(signals, window) == case["expected"], case["name"]


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
