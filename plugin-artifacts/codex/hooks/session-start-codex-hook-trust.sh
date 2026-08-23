#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Report Codex hooks that are registered but never trusted, so they cannot
# silently never run. Advisory: emits context only when there IS a gap, and
# always exits 0.
#
# WHY: Codex records hook trust in ~/.codex/config.toml keyed by ORDINAL
# POSITION. Nothing compared it to .codex/hooks.json, so on 2026-08-23 a sweep
# found 28 of 46 registered hooks across 10 repos had never been trusted --
# including one that had shipped in git 9 days earlier.
set +e
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}"
WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
CHECK="${PLUGIN_ROOT}/scripts/codex_hook_trust_check.py"

[ -f "$CHECK" ] || { printf '{}'; exit 0; }
[ -f "${WORKDIR}/.codex/hooks.json" ] || { printf '{}'; exit 0; }
command -v python3 >/dev/null 2>&1 || { printf '{}'; exit 0; }

REPORT="$(python3 "$CHECK" --workdir "$WORKDIR" 2>/dev/null)"
[ -n "$REPORT" ] || { printf '{}'; exit 0; }

REPORT="$REPORT" python3 -c '
import json, os
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": os.environ.get("REPORT", ""),
}}))
' 2>/dev/null || printf '{}'
exit 0
