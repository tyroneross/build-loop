#!/usr/bin/env bash
# PreToolUse hook: Bash autonomy gate
# Reads stdin JSON (Claude Code PreToolUse event), invokes autonomy_gate.py,
# maps verdict to permissionDecision, outputs single-line JSON envelope.
# Always exits 0 — Claude Code hook contract: non-zero = hook failure, not deny.

set -euo pipefail

INPUT=$(cat)

# Extract command and cwd from event JSON
CMD=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null) || CMD=""

CWD=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('cwd', ''))
" 2>/dev/null) || CWD=""

# No command extracted — pass through silently
if [ -z "$CMD" ]; then
    echo '{}'
    exit 0
fi

# Scope guard: only police Bash in projects that have opted in to build-loop.
# A build-loop project is identified by .build-loop/state.json (active run) or
# .build-loop/config.json (configured policy). Without either, this gate must
# not fire — it would false-positive on every curl/grep/git command across the
# user's entire filesystem (e.g. "curl https://app.vercel.app/..." in an
# unrelated repo would substring-match "vercel" and trigger an approval prompt).
#
# Additional belt-and-braces: never enforce when CWD is empty, root, or HOME
# itself. HOME hosts ~/.build-loop/ (global memory + audit state), and a literal
# existence check would otherwise activate the gate for every shell command run
# from the user's home directory.
if [ -z "$CWD" ] || [ "$CWD" = "/" ] || [ "$CWD" = "$HOME" ]; then
    echo '{}'
    exit 0
fi
if [ ! -f "$CWD/.build-loop/state.json" ] && [ ! -f "$CWD/.build-loop/config.json" ]; then
    echo '{}'
    exit 0
fi

# Honor an explicit kill switch for emergencies.
if [ "${BUILD_LOOP_HOOKS:-}" = "off" ]; then
    echo '{}'
    exit 0
fi

# Resolve CLAUDE_PLUGIN_ROOT for finding autonomy_gate.py
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
    # Fall back: gate script is in scripts/ sibling of scripts/hooks/
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PLUGIN_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
fi

GATE="${PLUGIN_ROOT}/scripts/autonomy_gate.py"
if [ ! -f "$GATE" ]; then
    # Gate missing — allow by default (don't block work)
    echo '{}'
    exit 0
fi

WORKDIR="${CWD:-.}"

# Invoke the gate; capture stdout regardless of exit code.
# Gate exits 0=auto/warn, 1=confirm, 2=block — these are informational, not errors.
# Use a temp file to capture output safely without triggering set -e on non-zero exit.
GATE_TMP=$(mktemp)
python3 "$GATE" \
    --workdir "$WORKDIR" \
    --action "PreToolUse:Bash" \
    --command "$CMD" \
    --json >"$GATE_TMP" 2>/dev/null || true
RESULT=$(cat "$GATE_TMP")
rm -f "$GATE_TMP"

# If result is empty, pass through silently
if [ -z "$RESULT" ]; then
    echo '{}'
    exit 0
fi

# Parse action and reason from gate result
ACTION=$(printf '%s' "$RESULT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('action', 'auto'))
except Exception:
    print('auto')
" 2>/dev/null) || ACTION="auto"

# Map gate verdict to PreToolUse permissionDecision
# auto  -> allow  (no pattern matched, safe)
# warn  -> allow  (warn does not block; just flagged for tracking)
# confirm -> ask  (user/permission flow)
# block -> deny   (hard stop)
case "$ACTION" in
    auto|warn) DECISION="allow" ;;
    confirm)   DECISION="ask" ;;
    block)     DECISION="deny" ;;
    *)         DECISION="ask" ;;
esac

# Pass values to Python via env vars (NOT shell string interpolation) — single
# quotes in REASON would otherwise terminate Python literals and could allow
# code injection. JSON encoding is done inside Python on the env-var values.
export _BL_DECISION="$DECISION"
export _BL_GATE_RESULT="$RESULT"

python3 <<'PY'
import json, os, sys
decision = os.environ.get('_BL_DECISION', 'ask')
gate_result = os.environ.get('_BL_GATE_RESULT', '{}')
try:
    reason = json.loads(gate_result).get('reason', '')
except Exception:
    reason = ''
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': decision,
        'permissionDecisionReason': reason,
    }
}))
PY

exit 0
