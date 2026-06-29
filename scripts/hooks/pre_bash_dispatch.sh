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
#
# Side effect: the sub-gate's exit code is written to the file named by
# $GATE_RC_FILE (when set), so a caller that runs `_run_gate` inside a `$(...)`
# command substitution can still recover it — assignments inside a `$(...)`
# subshell are lost to the parent, so a plain variable would always read 0.
# Callers that enforce a hard-block exit code (the commit auditor's rc==2
# secrets/conflict block) point GATE_RC_FILE at a temp file and read it back
# after the call. ALL other rc values are advisory and stay fail-open.
_run_gate() {
    local gate="$1"
    local out=""
    local rc=0
    if [ -x "$gate" ]; then
        out=$(printf '%s' "$INPUT" | "$gate") || rc=$?
    elif [ -f "$gate" ]; then
        out=$(printf '%s' "$INPUT" | python3 "$gate") || rc=$?
    fi
    [ -n "${GATE_RC_FILE:-}" ] && printf '%s' "$rc" > "$GATE_RC_FILE" 2>/dev/null || true
    [ -z "$out" ] && out='{}'
    printf '%s' "$out"
}

# Collect envelopes only from the gates whose command class is present.
ENVELOPES=()

# Autonomy gate: always the policy classifier.
ENVELOPES+=("$(_run_gate "$PLUGIN_ROOT/scripts/hooks/pre_bash_autonomy.sh")")

# Dependency cooldown: only on package installs/adds. This pre-filter MUST be
# a SUPERSET of the inner classifier in pre_bash_dependency_cooldown.sh
# (regex `\bnpm\s+(i|install|add|update|ci)\b`); otherwise the dispatcher drops
# a command the inner gate would have policed. Two cases the inner regex
# matches that a naive list misses:
#   - `npm update` (the inner `update` alternative)
#   - a command ENDING in `npm i` (no trailing arg) — `*"npm i "*` requires a
#     trailing space, so `*"npm i"` (no space) catches the bare/terminal form.
case "$CMD" in
    *"npm install"*|*"npm i "*|*"npm i"|*"npm ci"*|*"npm add"*|*"npm update"*|\
    *"pnpm add"*|*"pnpm install"*|*"yarn add"*|*"yarn install"*|\
    *"bun add"*|*"bun install"*)
        ENVELOPES+=("$(_run_gate "$PLUGIN_ROOT/scripts/hooks/pre_bash_dependency_cooldown.sh")")
        ;;
esac

# Commit auditor: only when the command commits. This is the big win — the
# 515-LOC auditor no longer spawns on every non-commit Bash call.
#
# HARD-BLOCK propagation: audit_before_commit.py returns rc==2 ONLY for
# deterministic, zero-judgment violations (a staged secrets file with
# credential-shaped content, or unresolved merge-conflict markers). This is the
# ONE intentional enforcement path in the chain. When it fires the dispatcher
# MUST exit 2 so Claude Code blocks the commit — consolidating the chain must
# not demote this gate to advisory. The auditor's stderr (which names the
# blocking reason) has already been passed through. Every OTHER rc (0, 1, a
# crash, a missing python3) stays fail-open: we do not block on auditor errors.
COMMIT_AUDIT_HARD_BLOCK=0
case "$CMD" in
    *commit*)
        GATE_RC_FILE=$(mktemp 2>/dev/null || echo "")
        ENVELOPES+=("$(_run_gate "$PLUGIN_ROOT/scripts/audit_before_commit.py")")
        if [ -n "$GATE_RC_FILE" ] && [ -f "$GATE_RC_FILE" ]; then
            if [ "$(cat "$GATE_RC_FILE" 2>/dev/null)" = "2" ]; then
                COMMIT_AUDIT_HARD_BLOCK=1
            fi
            rm -f "$GATE_RC_FILE" 2>/dev/null || true
        fi
        unset GATE_RC_FILE
        ;;
esac

# Pre-push security gate: deterministic OWASP scan before a push. Mirrors the
# commit-auditor hard-block. Named, observed failure that earns it: a GitHub
# OAuth access_token logged to console.log shipped unnoticed (2026-06) — detection
# was gated on a judgment flag + a Fable-pinned agent, with no always-on backstop.
# Hard-block (exit 2) only on HIGH+ findings (scanner rc==1); its stderr names
# them. Fail-open on any other rc (missing python3, scanner crash) — a broken
# scanner must never wedge `git push`. Escape: `// nosec: <reason>` on a confirmed
# false positive, or BUILD_LOOP_HOOKS=off to bypass.
SECURITY_HARD_BLOCK=0
case "$CMD" in
    *"git push"*)
        _SCAN="$PLUGIN_ROOT/scripts/security_scan.py"
        if [ -f "$_SCAN" ] && command -v python3 >/dev/null 2>&1; then
            _SCAN_RC=0
            _SCAN_OUT=$(python3 "$_SCAN" --path "$CWD" --fail-on high 2>&1) || _SCAN_RC=$?
            if [ "$_SCAN_RC" = "1" ]; then
                SECURITY_HARD_BLOCK=1
                printf '%s\n' "$_SCAN_OUT" >&2
                printf '\n[build-loop] Pre-push security scan found HIGH+ findings — push blocked.\nFix them, annotate a confirmed false positive with `// nosec: <reason>`, or set BUILD_LOOP_HOOKS=off to bypass.\n' >&2
            fi
        fi
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

# Hard-block the commit (auditor) or push (security scan) on a deterministic
# violation. stderr was already emitted; exit 2 tells Claude Code to deny.
if [ "$COMMIT_AUDIT_HARD_BLOCK" = "1" ] || [ "$SECURITY_HARD_BLOCK" = "1" ]; then
    exit 2
fi

exit 0
