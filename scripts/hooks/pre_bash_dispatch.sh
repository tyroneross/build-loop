#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# PreToolUse:Bash dispatcher — single entry that replaces the 3-hook chain.
#
# WHY: the old hooks.json chained THREE PreToolUse Bash hooks. Every Bash
# command paid for:
#   - pre_bash_autonomy.sh        (spawns python3 ×2 to parse the event)
#   - pre_bash_dependency_cooldown.sh (spawns python3 ×2 + an npx tsx lookup)
#   - audit_before_commit.py      (spawns python3, imports sqlite3/subprocess,
#                                   then self-filters to commits AFTER startup)
# That is up to 5 python interpreter spawns per Bash call, including a 515-LOC
# commit auditor that only does work on `git commit`.
#
# This dispatcher extracts CMD/CWD ONCE in shell, applies the build-loop scope
# guard ONCE, then a `case "$CMD"` pre-filter spawns each sub-gate ONLY when its
# command class is present:
#   - autonomy gate: always (it is the policy classifier)
#   - dependency cooldown: only when CMD installs/adds packages
#   - commit auditor: only when CMD contains `commit`
#
# Envelopes are merged by permissionDecision precedence: deny > ask > allow.
#
# ALWAYS exits 0 (Claude Code contract: non-zero = hook failure, not deny).
# Fail-open: any sub-gate error degrades to allow. Minimal-PATH safe: python3
# is in the safe set; sub-scripts are absolute-pathed.

set -euo pipefail

INPUT=$(cat)

# Honor the global kill switch before doing any work.
if [ "${BUILD_LOOP_HOOKS:-}" = "off" ]; then
    printf '{}'
    exit 0
fi

# Extract command + cwd ONCE (was: 2 python spawns per sub-hook).
read -r -d '' _EXTRACT <<'PY' || true
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("command", ""))
    print(d.get("cwd", ""))
except Exception:
    print("")
    print("")
PY
_PARSED=$(printf '%s' "$INPUT" | python3 -c "$_EXTRACT" 2>/dev/null) || _PARSED=$'\n'
CMD=$(printf '%s' "$_PARSED" | sed -n '1p')
CWD=$(printf '%s' "$_PARSED" | sed -n '2p')

# No command — pass through silently.
if [ -z "$CMD" ]; then
    printf '{}'
    exit 0
fi

# Scope guard (mirrors the sub-hooks): only police Bash in build-loop projects.
# Empty/root/HOME cwd never enforces.
if [ -z "$CWD" ] || [ "$CWD" = "/" ] || [ "$CWD" = "$HOME" ]; then
    printf '{}'
    exit 0
fi
if [ ! -f "$CWD/.build-loop/state.json" ] && [ ! -f "$CWD/.build-loop/config.json" ]; then
    printf '{}'
    exit 0
fi

# Resolve plugin root for locating sub-scripts.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PLUGIN_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
fi

# Run a sub-gate, feeding it the original event on stdin; echo its stdout.
# Any failure yields '{}' (fail-open). Never aborts the dispatcher.
#
# stderr is PASSED THROUGH, not suppressed: the commit auditor
# (audit_before_commit.py) writes its audit packet to stderr for the running
# session to read — swallowing it would silently defeat the auditor. Sub-gates
# that have nothing to say write nothing to stderr, so pass-through is quiet on
# the common path.
_run_gate() {
    local gate="$1"
    local out=""
    if [ -x "$gate" ]; then
        out=$(printf '%s' "$INPUT" | "$gate") || out=""
    elif [ -f "$gate" ]; then
        out=$(printf '%s' "$INPUT" | python3 "$gate") || out=""
    fi
    [ -z "$out" ] && out='{}'
    printf '%s' "$out"
}

# Collect envelopes only from the gates whose command class is present.
ENVELOPES=()

# Autonomy gate: always the policy classifier.
ENVELOPES+=("$(_run_gate "$PLUGIN_ROOT/scripts/hooks/pre_bash_autonomy.sh")")

# Dependency cooldown: only on package installs/adds.
case "$CMD" in
    *"npm install"*|*"npm i "*|*"npm ci"*|*"npm add"*|\
    *"pnpm add"*|*"pnpm install"*|*"yarn add"*|*"yarn install"*|\
    *"bun add"*|*"bun install"*)
        ENVELOPES+=("$(_run_gate "$PLUGIN_ROOT/scripts/hooks/pre_bash_dependency_cooldown.sh")")
        ;;
esac

# Commit auditor: only when the command commits. This is the big win — the
# 515-LOC auditor no longer spawns on every non-commit Bash call.
case "$CMD" in
    *commit*)
        ENVELOPES+=("$(_run_gate "$PLUGIN_ROOT/scripts/audit_before_commit.py")")
        ;;
esac

# Merge by precedence: deny > ask > allow. First matching decision wins.
# Pass the envelopes via argv to a tiny python merge (no shell JSON parsing).
python3 - "${ENVELOPES[@]}" <<'PY'
import sys, json

PRECEDENCE = {"deny": 3, "ask": 2, "allow": 1}
best = None
best_rank = 0
for raw in sys.argv[1:]:
    try:
        d = json.loads(raw)
    except Exception:
        continue
    hso = d.get("hookSpecificOutput") if isinstance(d, dict) else None
    if not isinstance(hso, dict):
        continue
    decision = hso.get("permissionDecision")
    rank = PRECEDENCE.get(decision, 0)
    if rank > best_rank:
        best_rank = rank
        best = d

print(json.dumps(best) if best else "{}")
PY

exit 0
