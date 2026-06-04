# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Scan ``.build-loop/worktrees/run-*`` for crashed-run leaks and reap them.

The companion of ``scripts/collapse_run.py``. Where ``collapse_run`` handles
the *normal-finish* closeout (a run reached its end, refs are listed in
state.json), the reaper handles the *crashed-run* case: the process died
before reaching closeout, the worktree folder remains, and nothing in
state.json knows about it.

Bundle-then-remove is non-negotiable — every reap creates a git bundle at
``.build-loop/bundles/reaped-<branch>-<TS>.bundle`` first so a mistaken reap
is recoverable via ``git bundle unbundle``.

Selection criteria (a worktree is reapable when ALL hold):
  1. It sits under ``.build-loop/worktrees/run-*/``.
  2. Its branch is NOT the ``state.execution.run_worktree_branch`` of an
     *active* run (i.e. the current state.execution block).
  3. The folder's mtime is older than ``--min-age-hours`` (default 2).
  4. Git can resolve the branch (or the worktree's HEAD) — orphaned folders
     that no longer have a backing branch are reaped without bundling
     because there is nothing to bundle.

Idempotent and fire-and-forget on each individual reap; aggregate result
reports each ref's disposition. Stdlib only.
"""
from __future__ import annotations

from .reaper import (  # noqa: F401  — re-exported for convenience
    ReapResult,
    reap_worktrees,
)

__all__ = ["ReapResult", "reap_worktrees"]
