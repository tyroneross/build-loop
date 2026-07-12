#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SessionStart worktree report. This hook never mutates Git.
#
# Destructive finalization requires a direct collapse_run/reaper invocation with
# explicit owner release. BUILDLOOP_GC_ACT is intentionally ignored: Stop is a
# turn boundary, and SessionStart cannot prove the prior terminal process exited.

set -u

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

REPORT=".build-loop/worktree-gc-last.txt"
REPORT_DIR=".build-loop/worktree-gc"
mkdir -p "$(dirname "$REPORT")" "$REPORT_DIR"

HOOK_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$HOOK_DIR/.." 2>/dev/null && pwd)}"
REAPER="$PLUGIN_ROOT/scripts/worktree_reaper/__main__.py"
_py=""
if [ -f "$HOOK_DIR/_resolve_python.sh" ]; then
    # shellcheck source=hooks/_resolve_python.sh
    . "$HOOK_DIR/_resolve_python.sh"
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="${REPORT}.tmp.$$"
json_tmp="${REPORT_DIR}/${stamp}-$$.json"
err_tmp="${REPORT_DIR}/${stamp}-$$.stderr"

if [ -n "$_py" ] && [ -f "$REAPER" ]; then
    "$_py" "$REAPER" --workdir "$PWD" --dry-run --json >"$json_tmp" 2>"$err_tmp"
    rc=$?
else
    rc=127
    : >"$json_tmp"
    printf '%s\n' "worktree reporter unavailable (python or reaper missing)" >"$err_tmp"
fi

{
    printf '# Worktree report — %s\n\n' "$(date -u +%FT%TZ)"
    printf 'Mode: REPORT-ONLY (SessionStart never owns terminal release)\n'
    if [ "${BUILDLOOP_GC_ACT:-0}" = "1" ]; then
        printf 'Notice: BUILDLOOP_GC_ACT=1 ignored; use an explicit owner-released finalizer call.\n'
    fi
    printf 'Exit: %s\n\n' "$rc"
    printf '## Summary\n\n'
    cat "$err_tmp"
    printf '\n## JSON\n\n```json\n'
    cat "$json_tmp"
    printf '\n```\n'
} >"$tmp" 2>/dev/null

mv "$tmp" "$REPORT" 2>/dev/null || true
rm -f "$err_tmp"
exit 0
