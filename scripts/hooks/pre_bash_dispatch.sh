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
            _SCAN_ARGS=(--path "$CWD" --fail-on high)
            # Scope the scan to the push delta: only what's actually being pushed
            # (files changed vs the upstream tracking branch), not the whole tree.
            # No upstream (detached/new branch) → keep the whole-repo scan (safe
            # fallback; scanner also falls back on any bad ref).
            _UPSTREAM=$(git -C "$CWD" rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || true)
            # Current branch — the push is only plain when the pushed ref IS the
            # current branch (h2: comparing to the tracking STRING alone let
            # `git push origin main` on a feature branch scope to the wrong,
            # empty delta while local main's secret shipped). Empty on detached
            # HEAD → classifier can't prove plain → full scan.
            _BRANCH=$(git -C "$CWD" symbolic-ref --short HEAD 2>/dev/null || true)
            if [ -n "$_UPSTREAM" ]; then
                # Only scope to the upstream delta when the push is PLAIN —
                # current branch → its tracking remote/ref, no refspec, no
                # destination-changing flag. Any other shape pushes content the
                # upstream..HEAD range does NOT cover, so scoping to it would
                # scan the wrong (often empty) range and let a secret ship. The
                # classifier is conservative BY CONSTRUCTION: a flag ALLOWLIST
                # (unknown flag → not plain), EVERY `git push` segment judged
                # (not just the last), and the pushed ref matched to the current
                # branch. Anything not positively classified as plain → OMIT
                # --diff → full-repo scan (fail-safe: never scan less than
                # intended).
                _PLAIN=$(CMD="$CMD" UPSTREAM="$_UPSTREAM" BRANCH="$_BRANCH" python3 - <<'PY' 2>/dev/null || true
import os, re, shlex, sys
cmd = os.environ.get("CMD", "")
upstream = os.environ.get("UPSTREAM", "")  # e.g. "origin/main"
branch = os.environ.get("BRANCH", "")      # current branch, e.g. "feature"
rem, _, up_branch = upstream.partition("/")

# h3 — ALLOWLIST polarity. Only flags positively known NOT to change the push
# destination or which refs are pushed stay plain. A denylist defaulted every
# unknown/future flag (e.g. --repo=backup) to unsafe-but-treated-safe; an
# allowlist closes them all by construction.
SAFE_BOOL = {
    "-q", "--quiet", "-v", "--verbose", "--progress", "--no-progress",
    "--no-verify", "--verify", "-n", "--dry-run",
    "-f", "--force", "--force-with-lease", "--no-force-with-lease",
    "-u", "--set-upstream",
    "-4", "--ipv4", "-6", "--ipv6", "--atomic", "--no-atomic",
    "--thin", "--no-thin",
}
# Value-consuming safe flags: a server-side push option, no dest/ref change.
SAFE_VALUE = {"-o", "--push-option"}

def is_plain(seg):
    try:
        toks = shlex.split(seg)
    except ValueError:
        return False
    if len(toks) < 2 or toks[0] != "git" or toks[1] != "push":
        return False
    toks = toks[2:]  # strip leading `git push`
    positionals = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t == "--":                      # end of options; rest are positionals
            positionals.extend(toks[i + 1:])
            break
        if t.startswith("-"):
            key = t.split("=", 1)[0]
            if key in SAFE_VALUE:
                # `-o v` / `--push-option v` → consume the following value token;
                # `-o=v` / `--push-option=v` → value is attached, consume nothing.
                if "=" not in t and t == key:
                    i += 1
                i += 1
                continue
            if key in SAFE_BOOL:           # --force-with-lease[=<lease>] via key
                i += 1
                continue
            return False                   # unknown flag → not plain
        if ":" in t:                       # refspec src:dst
            return False
        positionals.append(t)
        i += 1
    # Positionals must resolve to the current branch → its tracking remote.
    if not rem or not branch:              # can't prove plain without both
        return False
    if not positionals:                    # bare `git push`
        # push.default=matching could ship OTHER branches; require the tracked
        # branch to BE the current branch so that drift can't pass unseen.
        return up_branch == branch
    if len(positionals) == 1:              # `git push <remote>`
        return positionals[0] == rem and up_branch == branch
    if len(positionals) == 2:              # `git push <remote> <ref>`
        # h2: the ref must be the CURRENT branch, not merely the tracking name.
        return positionals[0] == rem and positionals[1] == branch
    return False                           # 3+ positionals (multi-ref) → not plain

# h1 — classify EVERY `git push` occurrence, each segment up to its next shell
# control operator (&& || ; | &). Plain ONLY if ALL segments are plain; any
# segment not provably plain → full scan.
found = False
plain = True
for m in re.finditer(r"git\s+push", cmd):
    found = True
    seg = re.split(r"&&|\|\||[;|&]", cmd[m.start():], maxsplit=1)[0]
    if not is_plain(seg):
        plain = False
        break
print("yes" if (found and plain) else "no")
PY
)
                if [ "$_PLAIN" = "yes" ]; then
                    _SCAN_ARGS+=(--diff "$_UPSTREAM")
                fi
                # else: non-plain push → omit --diff → full-repo scan.
            fi
            # Optional excludeGlobs from .build-loop/config.json (best-effort; a
            # missing file/key is a silent no-op — never a hard dependency).
            if [ -f "$CWD/.build-loop/config.json" ]; then
                _EX_GLOBS=$(python3 -c 'import sys,json
try:
    d=json.load(open(sys.argv[1]))
    g=d.get("securityScan",{}).get("excludeGlobs",[])
    if isinstance(g,list):
        for x in g:
            if isinstance(x,str) and x: print(x)
except Exception:
    pass' "$CWD/.build-loop/config.json" 2>/dev/null || true)
                while IFS= read -r _glob; do
                    if [ -n "$_glob" ]; then
                        _SCAN_ARGS+=(--exclude "$_glob")
                    fi
                done <<EOF
$_EX_GLOBS
EOF
            fi
            _SCAN_RC=0
            _SCAN_OUT=$(python3 "$_SCAN" "${_SCAN_ARGS[@]}" 2>&1) || _SCAN_RC=$?
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
