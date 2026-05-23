#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
# SessionStart hook — worktree GC (R3 from 2026-05-19 iOS retro).
#
# Contract:
#   - command-type only; no stdout, no stderr; exit 0 always
#     (per feedback_hook_design.md / feedback_hooks_decision_framework.md).
#   - REPORTS stale worktrees whose branch is merged into main; never removes them.
#     Locked + dirty worktrees may hold real in-progress work — Codex correction.
#   - AUTO-PRUNES only Git-proven orphan administrative records via `git worktree
#     prune -v` (the worktree dir was already deleted out-of-band).
#   - Report written to .build-loop/worktree-gc-last.txt; Phase 1 Assess may cat
#     it as part of R1 peer-detection context. Silent bail when there's no repo.

cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

REPORT=".build-loop/worktree-gc-last.txt"
mkdir -p "$(dirname "$REPORT")"

{
  printf '# Worktree GC report — %s\n\n' "$(date -u +%FT%TZ)"
  echo "## Stale candidates (REPORT-ONLY — operator decides removal)"
  git worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{p=$2} /^branch /{b=$2; sub("refs/heads/","",b); if(p && b){print p" "b}; p=""; b=""}' \
    | while read -r path branch; do
        [ "$branch" = main ] && continue
        if git merge-base --is-ancestor "$branch" main 2>/dev/null; then
          dirty=$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
          if [ -f "$path/.git" ] && grep -q locked "$path/.git" 2>/dev/null; then
            locked=locked
          else
            locked=unlocked
          fi
          echo "  $path  branch=$branch  merged=yes  dirty=$dirty  $locked"
        fi
      done
  echo
  echo "## Auto-prune (Git-proven orphans only — admin records of already-removed dirs)"
  git worktree prune -v 2>/dev/null
} > "$REPORT" 2>&1

exit 0
