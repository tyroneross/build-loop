#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# Fixture-driven tests for pre_bash_autonomy.sh and stop_finalize.sh
# (plus the dependency-cooldown backstop hook). Any failure exits non-zero.

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
# Case 1: PreToolUse benign command in a build-loop-marked cwd -> allow
# The autonomy hook scope-guards to projects with a .build-loop/ marker
# (state.json or config.json). A benign command inside such a project must
# evaluate to permissionDecision=allow (auto verdict). cwd must therefore be a
# marked project dir, not a bare /tmp (the scope-guard correctly returns {} for
# unmarked dirs — that contract is asserted separately below).
# ---------------------------------------------------------------------------
BL_DIR_1=$(mktemp -d)
mkdir -p "${BL_DIR_1}/.build-loop"
echo '{}' > "${BL_DIR_1}/.build-loop/config.json"

RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"ls -la\"},\"cwd\":\"${BL_DIR_1}\"}" \
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
    pass "Case 1: benign command (build-loop cwd) -> allow"
else
    fail "Case 1: benign command (build-loop cwd) -> allow" "got permissionDecision='${DECISION}' from: ${RESULT}"
fi
rm -rf "$BL_DIR_1"

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
# Case 3: PreToolUse with subagent agent_id present -> still works (no special
# handling). The hook does not filter by agent_id; a benign command in a
# build-loop-marked cwd must still evaluate to allow regardless of agent_id.
# ---------------------------------------------------------------------------
BL_DIR_3=$(mktemp -d)
mkdir -p "${BL_DIR_3}/.build-loop"
echo '{}' > "${BL_DIR_3}/.build-loop/config.json"

RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo hello\"},\"cwd\":\"${BL_DIR_3}\",\"agent_id\":\"sub-123\"}" \
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
    pass "Case 3: agent_id present (build-loop cwd) -> allow (no special handling)"
else
    fail "Case 3: agent_id present (build-loop cwd) -> allow" "got permissionDecision='${DECISION}' from: ${RESULT}"
fi
rm -rf "$BL_DIR_3"

# ---------------------------------------------------------------------------
# Case 3b: scope-guard — benign command with cwd:/tmp (NO .build-loop/ marker)
# -> silent {}. Locks in the autonomy hook's scope-guard hardening so the
# pre-hardening /tmp expectation cannot silently regress. Mirrors the
# cooldown-hook scope-guard assertion (Case 11 below).
# ---------------------------------------------------------------------------
RESULT=$(printf '%s' \
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls -la"},"cwd":"/tmp"}' \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$PRE_BASH")

if [ "$RESULT" = "{}" ]; then
    pass "Case 3b: non-build-loop cwd (/tmp) -> scope-guarded silent {}"
else
    fail "Case 3b: non-build-loop cwd (/tmp) -> silent {}" "got: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Case 4: Stop hook with subagent agent_id -> valid no-op JSON
# ---------------------------------------------------------------------------
TMPDIR_4=$(mktemp -d)
trap 'rm -rf "$TMPDIR_4"' EXIT

RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"Stop\",\"session_id\":\"sess-abc\",\"cwd\":\"${TMPDIR_4}\",\"agent_id\":\"sub-456\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$STOP_FIN")

# Should emit valid no-op JSON.
if [ "$RESULT" = "{}" ]; then
    pass "Case 4: Stop with agent_id -> valid {}"
else
    fail "Case 4: Stop with agent_id -> valid {}" "got output: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Case 5: Stop hook on non-build-loop cwd -> valid no-op JSON
# ---------------------------------------------------------------------------
TMPDIR_5=$(mktemp -d)
trap 'rm -rf "$TMPDIR_5"' EXIT

RESULT=$(printf '%s' \
    "{\"hook_event_name\":\"Stop\",\"session_id\":\"sess-def\",\"cwd\":\"${TMPDIR_5}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$STOP_FIN")

# No state.json present -> no-op JSON
if [ "$RESULT" = "{}" ]; then
    pass "Case 5: Stop on non-build-loop cwd -> valid {}"
else
    fail "Case 5: Stop on non-build-loop cwd -> valid {}" "got output: ${RESULT}"
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

# Already recorded -> no-op JSON
if [ "$RESULT" = "{}" ]; then
    pass "Case 6: Stop idempotency (duplicate session_id) -> valid {}"
else
    fail "Case 6: Stop idempotency -> valid {}" "got output: ${RESULT}"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
# Dependency-cooldown backstop hook (pre_bash_dependency_cooldown.sh)
# ---------------------------------------------------------------------------
DEP_HOOK="${SCRIPT_DIR}/pre_bash_dependency_cooldown.sh"
DC_DIR=$(mktemp -d)
mkdir -p "${DC_DIR}/.build-loop"
echo '{"name":"t"}' > "${DC_DIR}/package.json"
echo '{}' > "${DC_DIR}/.build-loop/config.json"

dc_decision() {
    printf '%s' "$1" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('hookSpecificOutput',{}).get('permissionDecision',''))
except Exception:
    print('')
" 2>/dev/null
}

# Case 7: npm install of a third-party pkg, no native config -> allow + --before
R=$(printf '%s' "{\"tool_input\":{\"command\":\"npm install lodash\"},\"cwd\":\"${DC_DIR}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$DEP_HOOK")
if [ "$(dc_decision "$R")" = "allow" ] && printf '%s' "$R" | grep -q -- "--before="; then
    pass "Case 7: npm install third-party -> allow + --before"
else
    fail "Case 7: npm install third-party -> allow + --before" "got: ${R}"
fi

# Case 8: allowlisted scope -> {} (no delay)
R=$(printf '%s' "{\"tool_input\":{\"command\":\"npm install @tyroneross/build-loop\"},\"cwd\":\"${DC_DIR}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$DEP_HOOK")
if [ "$R" = "{}" ]; then
    pass "Case 8: allowlisted scope -> no delay"
else
    fail "Case 8: allowlisted scope -> no delay" "got: ${R}"
fi

# Case 9: non-install Bash -> {} silent no-op
R=$(printf '%s' "{\"tool_input\":{\"command\":\"ls -la && git status\"},\"cwd\":\"${DC_DIR}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$DEP_HOOK")
if [ "$R" = "{}" ]; then
    pass "Case 9: non-install Bash -> silent no-op"
else
    fail "Case 9: non-install Bash -> silent no-op" "got: ${R}"
fi

# Case 10: npm ci (lockfile-driven, cannot date-pin) -> deny
R=$(printf '%s' "{\"tool_input\":{\"command\":\"npm ci\"},\"cwd\":\"${DC_DIR}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$DEP_HOOK")
if [ "$(dc_decision "$R")" = "deny" ]; then
    pass "Case 10: npm ci -> deny with actionable reason"
else
    fail "Case 10: npm ci -> deny" "got: ${R}"
fi

# Case 10b: deny writes cooldown diagnostics with injector --check JSON + PATH
DIAG_FILE=$(find "${DC_DIR}/.build-loop/issues" -name 'cooldown-*.json' -type f 2>/dev/null | head -n 1 || true)
if [ -n "$DIAG_FILE" ] && python3 - "$DIAG_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["schema"] == "build-loop.dependency_cooldown.deny.v1"
assert data["command"] == "npm ci"
assert isinstance(data.get("check"), dict)
assert "enforced" in data["check"]
assert isinstance(data.get("path"), str) and data["path"]
PY
then
    pass "Case 10b: npm ci deny -> writes cooldown diagnostics"
else
    fail "Case 10b: npm ci deny -> diagnostics file" "file=${DIAG_FILE:-missing}"
fi

# Case 11: not a build-loop project -> {} (scope guard)
NONBL=$(mktemp -d)
R=$(printf '%s' "{\"tool_input\":{\"command\":\"npm install lodash\"},\"cwd\":\"${NONBL}\"}" \
    | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$DEP_HOOK")
if [ "$R" = "{}" ]; then
    pass "Case 11: non-build-loop project -> scope-guarded no-op"
else
    fail "Case 11: non-build-loop project -> no-op" "got: ${R}"
fi
rm -rf "$DC_DIR" "$NONBL"

# --- npm with ACTIVE native min-release-age (allowlist_mechanism=hook) ------
# These require a real npm >= 11.10 (machine has 11.14.1). The injector's
# --check runs `npm config get min-release-age` to confirm enforcement.
DC2=$(mktemp -d)
mkdir -p "${DC2}/.build-loop"
echo '{"name":"t"}' > "${DC2}/package.json"
echo '{}' > "${DC2}/.build-loop/config.json"
printf 'min-release-age=7\n' > "${DC2}/.npmrc"

# Confirm the injector reports enforced (skip these cases on old npm).
DC2_ENF=$(python3 "${REPO_ROOT}/scripts/inject_dependency_cooldown.py" \
    --workdir "$DC2" --check --json 2>/dev/null \
    | python3 -c "import sys,json;print('1' if json.load(sys.stdin).get('enforced') else '0')" 2>/dev/null || echo 0)

if [ "$DC2_ENF" = "1" ]; then
    # Case 12: npm native active + all allowlisted -> --min-release-age=0 rewrite
    R=$(printf '%s' "{\"tool_input\":{\"command\":\"npm install @tyroneross/foo\"},\"cwd\":\"${DC2}\"}" \
        | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$DEP_HOOK")
    if [ "$(dc_decision "$R")" = "allow" ] \
        && printf '%s' "$R" | grep -q -- "--min-release-age=0" \
        && ! printf '%s' "$R" | grep -q -- "--before"; then
        pass "Case 12: npm native + all-allowlisted -> --min-release-age=0 rewrite, no --before"
    else
        fail "Case 12: npm native + all-allowlisted -> --min-release-age=0" "got: ${R}"
    fi

    # Case 13: npm native active + third-party -> silent {} (native gates it; no --before)
    R=$(printf '%s' "{\"tool_input\":{\"command\":\"npm install lodash\"},\"cwd\":\"${DC2}\"}" \
        | CLAUDE_PLUGIN_ROOT="$REPO_ROOT" bash "$DEP_HOOK")
    if [ "$R" = "{}" ]; then
        pass "Case 13: npm native + third-party -> silent {} (no --before)"
    else
        fail "Case 13: npm native + third-party -> silent {}" "got: ${R}"
    fi
else
    pass "Case 12-13: SKIPPED (npm < 11.10 — native min-release-age unavailable)"
fi
rm -rf "$DC2"

# Case 14: written-but-unrecognized key -> injector --check reports enforced:false
# (false-positive fix). Simulate via the old buggy camelCase npm key.
DC3=$(mktemp -d)
mkdir -p "${DC3}/.build-loop"
echo '{"name":"t"}' > "${DC3}/package.json"
echo '{}' > "${DC3}/.build-loop/config.json"
printf 'minimumReleaseAge=7\n' > "${DC3}/.npmrc"
ENF=$(python3 "${REPO_ROOT}/scripts/inject_dependency_cooldown.py" \
    --workdir "$DC3" --check --json 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('enforced'))" 2>/dev/null || echo "err")
if [ "$ENF" = "False" ]; then
    pass "Case 14: written-but-unrecognized npm key -> --check enforced:false (false-positive fix)"
else
    fail "Case 14: written-but-unrecognized key -> enforced:false" "got enforced=${ENF}"
fi
rm -rf "$DC3"

# ---------------------------------------------------------------------------
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

exit 0
