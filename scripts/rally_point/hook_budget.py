#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Single source of truth for the rally coordination-hook wall-clock budget.

The `rally` binary runs the pre-action coordination check under a wall-clock
budget (default 3000ms) and *fails open* — "no coordination check applied" —
when the check overruns. The defect this module fixes: inner subprocess
timeouts inside the check (e.g. `session_probe` ran coordination_status with
`timeout=5`, which itself shelled `git status` with `timeout=2`) were LARGER
than the outer budget, so any slow probe (stale tmux sessions, `index.lock`
contention) guaranteed an overrun.

Rule: a subprocess invoked under the hook budget MUST time out *before* the
budget does. Derive every inner timeout from the one budget here, leaving a
margin for process spawn + parse, and nest child margins larger than parent
margins so parent_timeout > child_timeout > child_work.

Env: ``RALLY_HOOK_BUDGET_MS`` (default 3000) — a FORWARD CONTRACT for the binary
that enforces the outer budget to export so Python tracks a non-default budget.
As of this writing the binary is NOT known to set it (TAG:UNVERIFIED — no Rust
source in-repo), so the default applies: the hardcoded 2.5s/1.8s hierarchy is
what actually fixes the inversion today, independent of the env var. If the
binary's real budget ever differs from 3000ms, exporting this keeps us in sync.
"""
from __future__ import annotations

import os
import math

DEFAULT_BUDGET_MS = 3000

# Margin tiers (seconds) reserved below the budget. Larger margin = tighter
# inner timeout. Nest these so a parent process (smaller margin) always
# out-waits its child (larger margin): PARENT > CHILD > GRANDCHILD work.
MARGIN_PARENT = 0.5    # e.g. session_probe waiting on coordination_status
MARGIN_CHILD = 1.2     # e.g. coordination_status waiting on `git status`


def budget_ms(env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("RALLY_HOOK_BUDGET_MS")
    if raw is None:
        return DEFAULT_BUDGET_MS
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_BUDGET_MS
    except (TypeError, ValueError):
        return DEFAULT_BUDGET_MS


def inner_timeout_seconds(margin: float = MARGIN_PARENT, env: dict[str, str] | None = None) -> float:
    """Largest safe timeout for a subprocess invoked under the hook budget.

    Preserve the nominal reserve when it fits. For short parent budgets, scale
    reserves continuously instead of forcing a child to outlive its parent.
    The child-margin tier anchors the provisional scale; default 3s budgets
    retain the existing 2.5s/1.8s hierarchy. This orders numeric deadlines, not
    startup or sequential work; callers still need their whole-call deadline.
    """
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError("timeout reserve must be finite and positive")
    outer = budget_ms(env) / 1000.0
    reserve = min(margin, outer * (margin / (margin + MARGIN_CHILD)))
    # Keep the invariant at floating-point extremes too; these are representable
    # neighbors, not another policy floor or duration ceiling.
    return max(math.nextafter(0.0, 1.0), min(outer - reserve, math.nextafter(outer, 0.0)))
