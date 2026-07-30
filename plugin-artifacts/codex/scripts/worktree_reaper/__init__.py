# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Report stale ``.build-loop/worktrees/run-*`` candidates.

Default behavior is read-only. Explicit act mode requires positive owner
release and delegates to ``scripts.collapse_run``; this package never mutates
Git or removes orphan folders directly.
"""
from __future__ import annotations

from .reaper import (  # noqa: F401  — re-exported for convenience
    ReapResult,
    reap_worktrees,
)

__all__ = ["ReapResult", "reap_worktrees"]
