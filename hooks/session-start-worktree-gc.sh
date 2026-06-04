#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Worktree GC: remove merged+clean stale worktrees/branches, AUTO-PRUNE orphans.
# ACT mode (bundle-first removal of merged worktrees + branches) is ON by default
# so cross-run residue self-heals (intent.md A5); set BUILDLOOP_GC_ACT=0 to opt out
# (report-only). Locked, dirty, or unmerged worktrees are never removed either way.
cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
R=".build-loop/worktree-gc-last.txt"
mkdir -p "$(dirname "$R")"
_bundle_done=0

# Rally exclusion (defense-in-depth, optional). If `rally` is on PATH and
# `rally sessions --json` returns a list of live sessions, collect their
# `cwd` paths so we can skip any worktree owned by an active session in
# addition to the path-prefix guard below. Rally owns its own worktree
# lifecycle; build-loop's GC must never reap a rally-owned worktree (force-
# removing it deletes the cwd of a live agent process and every subsequent
# hook fails with posix_spawn ENOENT). The path-prefix exclusion is the
# PRIMARY guard and works even when `rally` is not installed.
_rally_cwds=""
if command -v rally >/dev/null 2>&1; then
  _rally_cwds=$(rally sessions --json 2>/dev/null \
    | awk 'match($0, /"cwd"[[:space:]]*:[[:space:]]*"[^"]+"/){
             s=substr($0,RSTART,RLENGTH);
             sub(/^"cwd"[[:space:]]*:[[:space:]]*"/,"",s);
             sub(/"$/,"",s);
             print s
           }' 2>/dev/null || true)
fi

{
  printf '# Worktree GC report — %s\n\n## Stale candidates (REPORT-ONLY)\n' "$(date -u +%FT%TZ)"

  # Collect candidates; in ACT mode, acted-on items are echoed inline (ACT:removed)
  # and split into their own report section by the awk pass below.
  git worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{p=$2} /^branch /{b=$2; sub("refs/heads/","",b); if(p && b){print p" "b}; p=""; b=""}' \
    | while read -r path branch; do
        [ "$branch" = main ] && continue
        # Rally-owned worktrees are NEVER touched by build-loop's GC.
        # Primary guard: path-prefix exclusion (works without rally on PATH).
        case "$path" in
          */.rally/worktrees/*)
            echo "  SKIP:rally-owned  $path  branch=$branch"
            continue
            ;;
        esac
        # Defense-in-depth: live rally session cwd cross-check.
        if [ -n "$_rally_cwds" ]; then
          _skip=0
          while IFS= read -r _cwd; do
            [ -z "$_cwd" ] && continue
            if [ "$_cwd" = "$path" ]; then
              _skip=1
              break
            fi
          done <<EOF
$_rally_cwds
EOF
          if [ "$_skip" = "1" ]; then
            echo "  SKIP:rally-live-session  $path  branch=$branch"
            continue
          fi
        fi
        git merge-base --is-ancestor "$branch" main 2>/dev/null || continue
        dirty=$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
        locked=unlocked
        [ -f "$path/.git" ] && grep -q locked "$path/.git" 2>/dev/null && locked=locked
        echo "  $path  branch=$branch  merged=yes  dirty=$dirty  $locked"

        if [ "${BUILDLOOP_GC_ACT:-1}" = "1" ] && [ "$dirty" = "0" ] && [ "$locked" = "unlocked" ]; then
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
if [ "${BUILDLOOP_GC_ACT:-1}" = "1" ] && grep -q 'ACT:removed' "$R" 2>/dev/null; then
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
