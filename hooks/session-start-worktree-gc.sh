#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Worktree GC: REPORT stale-merged worktrees, AUTO-PRUNE Git-proven orphans.
# Set BUILDLOOP_GC_ACT=1 to also remove merged worktrees + branches (conservative).
# Locked/dirty worktrees are never removed in either mode.
cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
R=".build-loop/worktree-gc-last.txt"
mkdir -p "$(dirname "$R")"
_bundle_done=0
{
  printf '# Worktree GC report — %s\n\n## Stale candidates (REPORT-ONLY)\n' "$(date -u +%FT%TZ)"

  # Collect candidates; in ACT mode, acted-on items are echoed inline (ACT:removed)
  # and split into their own report section by the awk pass below.
  git worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{p=$2} /^branch /{b=$2; sub("refs/heads/","",b); if(p && b){print p" "b}; p=""; b=""}' \
    | while read -r path branch; do
        [ "$branch" = main ] && continue
        git merge-base --is-ancestor "$branch" main 2>/dev/null || continue
        dirty=$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
        locked=unlocked
        [ -f "$path/.git" ] && grep -q locked "$path/.git" 2>/dev/null && locked=locked
        echo "  $path  branch=$branch  merged=yes  dirty=$dirty  $locked"

        if [ "${BUILDLOOP_GC_ACT:-0}" = "1" ] && [ "$dirty" = "0" ] && [ "$locked" = "unlocked" ]; then
          # Reversibility: one bundle per run, created before first removal.
          if [ "$_bundle_done" = "0" ]; then
            mkdir -p ".build-loop/bundles"
            git bundle create ".build-loop/bundles/gc-$(date -u +%Y%m%dT%H%M%SZ).bundle" --all >/dev/null 2>&1
            _bundle_done=1
          fi
          git worktree remove -f -f "$path" >/dev/null 2>&1
          git branch -D "$branch" >/dev/null 2>&1
          echo "  ACT:removed  $path  branch=$branch"
        fi
      done

  printf '\n## Auto-prune (Git-proven orphans only)\n'
  git worktree prune -v 2>/dev/null
} > "$R" 2>&1

# Rewrite report to split REPORT-ONLY from Auto-removed sections cleanly.
if [ "${BUILDLOOP_GC_ACT:-0}" = "1" ] && grep -q 'ACT:removed' "$R" 2>/dev/null; then
  tmp=$(mktemp)
  awk '
    /^## Stale candidates/ { in_stale=1 }
    in_stale && /  ACT:removed / {
      sub("  ACT:removed  ", "  ")
      if (!printed_hdr) { removed=removed "\n## Auto-removed (merged, bundled)\n"; printed_hdr=1 }
      removed=removed $0 "\n"
      next
    }
    { print }
    END { printf "%s", removed }
  ' "$R" > "$tmp" && mv "$tmp" "$R"
fi

exit 0
