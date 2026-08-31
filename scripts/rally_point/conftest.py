#!/usr/bin/env python3
"""Give rally tests a subprocess timeout sized for a test suite, not a Stop hook.

kind_capability.supported_kinds() and discovery_bridge's rally shell-outs take
their subprocess timeout from hook_budget.inner_timeout_seconds(MARGIN_CHILD).
With the production default that is 1.8s, which is correct for a Stop hook that
must not stall a session -- and marginal inside a 6000-test run, where spawning
even a two-line /bin/sh stub can exceed it under load.

When the probe times out, supported_kinds() returns None ("could not
determine"), the caller falls back, and the assertion fails on a value that has
nothing to do with what the test is checking. It passes alone and fails in the
full suite, which is the failure shape that trains people to ignore a suite.

None of these tests is testing the timeout. test_hook_budget.py, which IS, passes
explicit env dicts to budget_ms/inner_timeout_seconds and never reads os.environ,
so it is unaffected by this fixture.
"""

from __future__ import annotations

import os

import pytest

#: 30s budget -> a 28.8s inner timeout. Generous enough that only a genuinely
#: hung subprocess trips it, so a real hang still fails rather than hanging the
#: suite (pytest-timeout remains the outer backstop).
TEST_BUDGET_MS = "30000"


@pytest.fixture(autouse=True)
def _rally_test_hook_budget():
    previous = os.environ.get("RALLY_HOOK_BUDGET_MS")
    os.environ["RALLY_HOOK_BUDGET_MS"] = TEST_BUDGET_MS
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("RALLY_HOOK_BUDGET_MS", None)
        else:
            os.environ["RALLY_HOOK_BUDGET_MS"] = previous
