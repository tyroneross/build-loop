# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""build-loop memory closeout — machine-readable status emit.

The closeout fires AFTER A PUSH (not on every Stop). It inspects the run's
durable-signal sources and emits exactly one machine-readable status:

    closeout_status: "wrote_memory" | "queued_pending_lesson" | "no_durable_lesson"

Public API
----------
- ``run(workdir, run_id, source) -> dict``
- ``detect_durable_signal(workdir) -> dict``

Triggers
--------
1. **post-push (build-loop run)** — orchestrator Phase 4G calls this after the
   closing push, alongside the retrospective-synthesizer dispatch.
2. **post-push (ad-hoc)** — ``hooks/git/pre-push`` ARMS a baton at
   ``.build-loop/closeout/armed.json``; the next session-start hook drains it.
3. **phase-6-learn** — orchestrator emits the status as part of the ``## Learn``
   block at Review-G.

Non-raising on internal errors (callers do not block on closeout); the only
detectable failure mode is "skipped/empty closeout with durable signal",
which the test suite enforces via :func:`detect_durable_signal`.
"""
from __future__ import annotations

from closeout.status import (
    CLOSEOUT_STATUSES,
    detect_durable_signal,
    run,
)

__all__ = ["CLOSEOUT_STATUSES", "detect_durable_signal", "run"]
