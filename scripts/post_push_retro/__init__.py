# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""post_push_retro — scope-gated recursive-retrospective auto-trigger.

Auto-fires the recursive retrospective after a push, scope-gated by tier so
Fable is spent only where it pays. REUSES the three existing retro tiers:

  * trivial     → the zero-LLM deterministic sweep (``python3 -m retrospective``)
  * medium      → Stage-1-light + the independent Stage-3 judge (~1 Fable agent)
  * substantial → the full 3-stage recursive-retrospective pipeline

This package is the net-new glue ONLY: the post-push trigger, multi-branch/
worktree coverage against a per-repo checkpoint, the scope→tier classifier, the
tier router, and the never-silently-skip fallback. It does NOT contain a retro
engine — it wires the existing ones.

Every entry point is FAIL-OPEN: a broken retro trigger must never block a push
or wedge a session. The falsifier the whole design guards against: a push that
produces NEITHER a retro NOR a fallback entry (a silent skip).
"""
