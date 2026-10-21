#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for hook_budget — inner timeouts must always fit under the outer budget."""
from __future__ import annotations

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rally_point import hook_budget as hb  # noqa: E402


def test_default_budget():
    assert hb.budget_ms({}) == hb.DEFAULT_BUDGET_MS


def test_env_override():
    assert hb.budget_ms({"RALLY_HOOK_BUDGET_MS": "5000"}) == 5000


def test_bad_env_falls_back():
    assert hb.budget_ms({"RALLY_HOOK_BUDGET_MS": "abc"}) == hb.DEFAULT_BUDGET_MS
    assert hb.budget_ms({"RALLY_HOOK_BUDGET_MS": "-1"}) == hb.DEFAULT_BUDGET_MS


def test_inner_timeout_strictly_under_budget():
    # The core invariant: parent and child inner timeouts both fit under 3s.
    parent = hb.inner_timeout_seconds(hb.MARGIN_PARENT, {})
    child = hb.inner_timeout_seconds(hb.MARGIN_CHILD, {})
    assert parent < hb.DEFAULT_BUDGET_MS / 1000.0      # < 3.0
    assert child < parent                               # nesting: parent out-waits child
    assert parent == 2.5 and child == 1.8               # 3.0-0.5, 3.0-1.2


@pytest.mark.parametrize("budget", [1, 100, 200, 1000, 1200, 1700, 2400, 3000, 6000])
def test_short_budgets_cannot_invert_nested_timeouts(budget):
    env = {"RALLY_HOOK_BUDGET_MS": str(budget)}
    parent = hb.inner_timeout_seconds(hb.MARGIN_PARENT, env)
    child = hb.inner_timeout_seconds(hb.MARGIN_CHILD, env)
    assert 0 < child < parent < budget / 1000


@pytest.mark.parametrize("margin", [0, -1, float("nan"), float("inf")])
def test_reserve_must_be_a_usable_duration(margin):
    with pytest.raises(ValueError):
        hb.inner_timeout_seconds(margin)


@pytest.mark.parametrize("margin", [1e-320, 1e308])
def test_representable_extreme_reserves_stay_inside_parent(margin):
    assert 0 < hb.inner_timeout_seconds(margin, {"RALLY_HOOK_BUDGET_MS": "200"}) < 0.2


def test_scales_with_larger_budget():
    parent = hb.inner_timeout_seconds(hb.MARGIN_PARENT, {"RALLY_HOOK_BUDGET_MS": "6000"})
    assert parent == 5.5  # 6.0 - 0.5
