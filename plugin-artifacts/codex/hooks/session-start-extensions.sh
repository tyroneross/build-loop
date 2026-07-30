#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -u
PY="$(command -v python3 || true)"; [ -z "$PY" ] && exit 0
ROOT="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}"
N="$("$PY" "$ROOT/scripts/extensions_pending_count.py" 2>/dev/null || echo 0)"
if [ "${N:-0}" -gt 0 ] 2>/dev/null; then
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"build-loop: %s pending extension draft(s) await review — run: python3 %s/scripts/extensions_approve.py --list"}}\n' "$N" "$ROOT"
fi
exit 0
