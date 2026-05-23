#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Worktree GC: REPORT stale-merged worktrees, AUTO-PRUNE Git-proven orphans.
# Locked/dirty worktrees never removed (Codex 2026-05-19 correction).
cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
R=".build-loop/worktree-gc-last.txt"
mkdir -p "$(dirname "$R")"
{
  printf '# Worktree GC report — %s\n\n## Stale candidates (REPORT-ONLY)\n' "$(date -u +%FT%TZ)"
  git worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{p=$2} /^branch /{b=$2; sub("refs/heads/","",b); if(p && b){print p" "b}; p=""; b=""}' \
    | while read -r path branch; do
        [ "$branch" = main ] && continue
        git merge-base --is-ancestor "$branch" main 2>/dev/null || continue
        dirty=$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
        locked=unlocked
        [ -f "$path/.git" ] && grep -q locked "$path/.git" 2>/dev/null && locked=locked
        echo "  $path  branch=$branch  merged=yes  dirty=$dirty  $locked"
      done
  printf '\n## Auto-prune (Git-proven orphans only)\n'
  git worktree prune -v 2>/dev/null
} > "$R" 2>&1
exit 0
