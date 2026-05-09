#!/usr/bin/env bash
# Fixture-driven tests for pre_bash_autonomy.sh and stop_finalize.sh
# All 6 cases must pass; any failure exits non-zero.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
PRE_BASH="${SCRIPT_DIR}/pre_bash_autonomy.sh"
STOP_FIN="${SCRIPT_DIR}/stop_finalize.sh"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1 — $2"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Case 1: PreToolUse benign command -> permissionDecision=allow
# ---------------------------------------------------------------------------
RESULT=$(printf '%s' \
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls -la"},"cwd":"/tmp"}' \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$PRE_BASH")

DECISION=$(printf '%s' "$RESULT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('hookSpecificOutput',{}).get('permissionDecision',''))
except Exception:
    print('')
" 2>/dev/null)

if [ "$DECISION" = "allow" ]; then
    pass "Case 1: benign command -> allow"
else
    fail "Case 1: benign command -> allow" "got permissionDecision='${DECISION}' from: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Case 2: PreToolUse deployment command -> permissionDecision=ask (confirm)
# Uses "production deploy v1" which hits deployment_policy -> confirm verdict.
# Note: "git push --force origin main" also maps to confirm, but the global
# pre-tool-use guardian blocks that string in the test environment; using a
# functionally equivalent confirm-class command avoids that constraint.
# ---------------------------------------------------------------------------
RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"production deploy v1\"},\"cwd\":\"${REPO_ROOT}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$PRE_BASH")

DECISION=$(printf '%s' "$RESULT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('hookSpecificOutput',{}).get('permissionDecision',''))
except Exception:
    print('')
" 2>/dev/null)

if [ "$DECISION" = "ask" ]; then
    pass "Case 2: deployment command -> ask (confirm verdict)"
else
    fail "Case 2: deployment command -> ask (confirm verdict)" "got permissionDecision='${DECISION}' from: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Case 3: PreToolUse with subagent agent_id present -> still works (no special handling)
# ---------------------------------------------------------------------------
RESULT=$(printf '%s' \
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"echo hello"},"cwd":"/tmp","agent_id":"sub-123"}' \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$PRE_BASH")

DECISION=$(printf '%s' "$RESULT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('hookSpecificOutput',{}).get('permissionDecision',''))
except Exception:
    print('')
" 2>/dev/null)

# Pre-bash hook doesn't filter by agent_id — it just evaluates the command
if [ "$DECISION" = "allow" ]; then
    pass "Case 3: PreToolUse with agent_id -> allow (no special handling)"
else
    fail "Case 3: PreToolUse with agent_id -> allow" "got permissionDecision='${DECISION}' from: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Case 4: Stop hook with subagent agent_id -> silent exit 0
# ---------------------------------------------------------------------------
TMPDIR_4=$(mktemp -d)
trap 'rm -rf "$TMPDIR_4"' EXIT

RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"Stop\",\"session_id\":\"sess-abc\",\"cwd\":\"${TMPDIR_4}\",\"agent_id\":\"sub-456\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$STOP_FIN")

# Should be empty (silent exit 0)
if [ -z "$RESULT" ]; then
    pass "Case 4: Stop with agent_id -> silent exit 0"
else
    fail "Case 4: Stop with agent_id -> silent" "got output: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Case 5: Stop hook on non-build-loop cwd -> silent exit 0
# ---------------------------------------------------------------------------
TMPDIR_5=$(mktemp -d)
trap 'rm -rf "$TMPDIR_5"' EXIT

RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"Stop\",\"session_id\":\"sess-def\",\"cwd\":\"${TMPDIR_5}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$STOP_FIN")

# No state.json present -> exit 0 silently
if [ -z "$RESULT" ]; then
    pass "Case 5: Stop on non-build-loop cwd -> silent exit 0"
else
    fail "Case 5: Stop on non-build-loop cwd -> silent" "got output: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Case 6: Stop hook idempotency — existing session in runs[] -> skip
# ---------------------------------------------------------------------------
TMPDIR_6=$(mktemp -d)
trap 'rm -rf "$TMPDIR_6"' EXIT

mkdir -p "${TMPDIR_6}/.build-loop"
python3 -c "
import json
state = {
    'phase': 'report',
    'runs': [
        {'run_id': 'run-2026-05-09-sess-xyz-12345678', 'date': '2026-05-09T00:00:00+00:00', 'session_id': 'sess-xyz-idempotent'}
    ]
}
with open('${TMPDIR_6}/.build-loop/state.json', 'w') as f:
    json.dump(state, f, indent=2)
"

RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"Stop\",\"session_id\":\"sess-xyz-idempotent\",\"cwd\":\"${TMPDIR_6}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$STOP_FIN")

# Already recorded -> silent exit 0
if [ -z "$RESULT" ]; then
    pass "Case 6: Stop idempotency (duplicate session_id) -> silent exit 0"
else
    fail "Case 6: Stop idempotency -> silent" "got output: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

exit 0
