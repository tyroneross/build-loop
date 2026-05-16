#!/usr/bin/env bash
# PreToolUse hook: dependency-cooldown backstop (supply-chain layer 2).
#
# Catches ad-hoc package installs in build-loop projects.
#
# Stand-down regime is keyed on the injector's `allowlist_mechanism`:
#   - "native" (pnpm/yarn): native config carries the exclude list, so once
#     enforced the hook stands down entirely (silent pass).
#   - "hook" (npm): npm has NO native exclude (npm/cli#8994). Even when
#     native `min-release-age` is active for transitive coverage, the hook
#     STAYS ENGAGED to honor the allowlist:
#       * all explicit pkgs allowlisted -> rewrite append `--min-release-age=0`
#         (command-scoped bypass for self-authored packages)
#       * not all allowlisted -> silent `{}` (native min-release-age handles
#         it; NEVER add `--before` — npm errors when both are present)
#
# For ungated projects (not enforced): npm/yarn add/install rewrites with
# `--before=<date 7d ago>`. For commands that can't be safely rewritten
# (pnpm add, `npm ci`) it denies with an actionable message.
#
# Mirrors scripts/hooks/pre_bash_autonomy.sh exactly: stdin event JSON,
# scope-guard to build-loop projects, silent `{}` exit 0 on the common path,
# BUILD_LOOP_HOOKS=off kill switch, CLAUDE_PLUGIN_ROOT resolution with a
# sibling-dir fallback. ALWAYS exits 0 (Claude Code contract: non-zero =
# hook failure, NOT deny; deny is expressed via permissionDecision).
#
# Allowlist single-source: this hook does NOT re-parse config. It reads the
# resolved allowlist + enforced state from
# scripts/inject_dependency_cooldown.py --check, so hook and injector can
# never diverge.

set -euo pipefail

INPUT=$(cat)

CMD=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null) || CMD=""

CWD=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('cwd', ''))
except Exception:
    print('')
" 2>/dev/null) || CWD=""

# No command — pass through silently.
if [ -z "$CMD" ]; then
    echo '{}'
    exit 0
fi

# Scope guard: only police build-loop projects. Never fire from /, $HOME, or
# an empty cwd (identical rationale to pre_bash_autonomy.sh).
if [ -z "$CWD" ] || [ "$CWD" = "/" ] || [ "$CWD" = "$HOME" ]; then
    echo '{}'
    exit 0
fi
if [ ! -f "$CWD/.build-loop/state.json" ] && [ ! -f "$CWD/.build-loop/config.json" ]; then
    echo '{}'
    exit 0
fi

# Emergency kill switch.
if [ "${BUILD_LOOP_HOOKS:-}" = "off" ]; then
    echo '{}'
    exit 0
fi

# Classify: is this a package add/install/update command? Conservative —
# anything that does not clearly add packages is a silent no-op so unrelated
# Bash is never disturbed (F-criterion).
#   npm i / npm install / npm install <pkg> / npm add / npm update / npm ci
#   pnpm add / pnpm install
#   yarn add
IS_INSTALL=$(printf '%s' "$CMD" | python3 -c "
import sys, re
c = sys.stdin.read()
pats = [
    r'\bnpm\s+(i|install|add|update|ci)\b',
    r'\bpnpm\s+(add|install)\b',
    r'\byarn\s+add\b',
]
print('1' if any(re.search(p, c) for p in pats) else '0')
" 2>/dev/null) || IS_INSTALL="0"

if [ "$IS_INSTALL" != "1" ]; then
    echo '{}'
    exit 0
fi

# Resolve plugin root (find inject_dependency_cooldown.py).
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PLUGIN_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
fi
INJECTOR="${PLUGIN_ROOT}/scripts/inject_dependency_cooldown.py"
if [ ! -f "$INJECTOR" ]; then
    # Injector missing — fail open (don't block work).
    echo '{}'
    exit 0
fi

# Ask the injector (single source of truth) whether the project is already
# enforcing and what the resolved allowlist is.
CHECK_TMP=$(mktemp)
python3 "$INJECTOR" --workdir "$CWD" --check --json >"$CHECK_TMP" 2>/dev/null || true
CHECK=$(cat "$CHECK_TMP")
rm -f "$CHECK_TMP"
if [ -z "$CHECK" ]; then
    echo '{}'
    exit 0
fi

ENFORCED=$(printf '%s' "$CHECK" | python3 -c "
import sys, json
try:
    print('1' if json.load(sys.stdin).get('enforced') else '0')
except Exception:
    print('0')
" 2>/dev/null) || ENFORCED="0"

MECHANISM=$(printf '%s' "$CHECK" | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin).get('allowlist_mechanism') or '')
except Exception:
    print('')
" 2>/dev/null) || MECHANISM=""

# Enforced + native exclude (pnpm/yarn) — config carries the allowlist, hook
# stands down entirely (silent pass).
if [ "$ENFORCED" = "1" ] && [ "$MECHANISM" = "native" ]; then
    echo '{}'
    exit 0
fi

# Enforced + hook-provided allowlist (npm): native min-release-age covers
# transitive deps but npm has no exclude — fall through to the decision
# block, which honors the allowlist via a command-scoped --min-release-age=0
# rewrite (and NEVER adds --before, which npm rejects alongside native config).

# Decide allow+rewrite vs deny. Pass CMD + CHECK to python via env (never
# shell interpolation — injection-safe, same discipline as the autonomy hook).
export _BL_CMD="$CMD"
export _BL_CHECK="$CHECK"
export _BL_ENFORCED="$ENFORCED"
export _BL_MECHANISM="$MECHANISM"

python3 <<'PY'
import json, os, re, sys
from datetime import datetime, timedelta, timezone

cmd = os.environ.get("_BL_CMD", "")
try:
    check = json.loads(os.environ.get("_BL_CHECK", "{}"))
except Exception:
    check = {}
allowlist = check.get("allowlist") or ["@tyroneross/*"]
days = check.get("threshold_days", 7)
enforced = os.environ.get("_BL_ENFORCED", "0") == "1"
mechanism = os.environ.get("_BL_MECHANISM", "")
# True only on npm-with-active-native-config: native min-release-age covers
# transitive deps; this hook only adds the allowlist bypass on top.
npm_native_active = enforced and mechanism == "hook"


def emit(decision, reason="", updated=None):
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    if updated is not None:
        out["hookSpecificOutput"]["updatedInput"] = {"command": updated}
    print(json.dumps(out))


def glob_match(name, pattern):
    rx = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
    return re.match(rx, name) is not None


# Extract explicit package args (tokens that are not flags and not the
# npm/pnpm/yarn/subcommand head). Heuristic but conservative.
toks = cmd.split()
SUBCMDS = {"npm", "pnpm", "yarn", "i", "install", "add", "update", "ci", "run", "exec"}
pkgs = []
for t in toks:
    if t.startswith("-"):
        continue
    if t in SUBCMDS:
        continue
    # strip version spec: lodash@4.17.21 / @scope/x@^1
    base = t
    at = base.rfind("@")
    if at > 0:
        base = base[:at]
    pkgs.append(base)

all_allowlisted = bool(pkgs) and all(
    any(glob_match(p, a) for a in allowlist) for p in pkgs
)

is_npm = re.search(r"\bnpm\s+(i|install|add|update)\b", cmd) is not None
is_npm_ci = re.search(r"\bnpm\s+ci\b", cmd) is not None
is_yarn_add = re.search(r"\byarn\s+add\b", cmd) is not None
is_pnpm = re.search(r"\bpnpm\s+(add|install)\b", cmd) is not None

# -------------------------------------------------------------------------
# Regime A: npm with ACTIVE native min-release-age (allowlist_mechanism=hook).
# Native config covers transitive deps. This hook only adds the allowlist
# bypass on top — and must NEVER add --before (npm errors when both
# min-release-age config and --before are present).
# -------------------------------------------------------------------------
if npm_native_active:
    if all_allowlisted:
        # Self-authored only — command-scoped bypass of the cooldown.
        if "--min-release-age" in cmd:
            print("{}")  # already specified, leave it
            sys.exit(0)
        emit(
            "allow",
            f"Supply-chain cooldown: all packages allowlisted "
            f"({', '.join(pkgs)}); appended --min-release-age=0 to bypass the "
            f"native {days}d gate for this command only. "
            f"constitution:C-SUPPLY/dependency_cooldown",
            updated=cmd + " --min-release-age=0",
        )
        sys.exit(0)
    # Mixed/third-party: native min-release-age already gates these. Do NOT
    # add --before (npm rejects it alongside native config). Silent pass.
    print("{}")
    sys.exit(0)

# -------------------------------------------------------------------------
# Regime B: project NOT gated by native config — hook is the active gate.
# -------------------------------------------------------------------------

# All explicit packages allowlisted -> no delay for self-authored.
if all_allowlisted:
    print("{}")
    sys.exit(0)

before = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

# npm ci is lockfile-driven; --before cannot influence it. Deny with a fix.
if is_npm_ci:
    emit(
        "deny",
        f"Supply-chain cooldown ({days}d) not enforced for this project and "
        f"`npm ci` is lockfile-driven (cannot be date-pinned). Run "
        f"`python3 scripts/inject_dependency_cooldown.py --workdir .` to add "
        f"native min-release-age config (constitution:C-SUPPLY/dependency_cooldown), "
        f"then retry.",
    )
    sys.exit(0)

# Already date-pinned? leave it.
if "--before" in cmd:
    print("{}")
    sys.exit(0)

if is_npm or is_yarn_add:
    emit(
        "allow",
        f"Supply-chain cooldown: appended --before={before} (latest version "
        f"published >= {days}d ago). Add native min-release-age config to "
        f"remove this rewrite. constitution:C-SUPPLY/dependency_cooldown",
        updated=cmd + f" --before={before}",
    )
    sys.exit(0)

# pnpm has no --before equivalent; deny with the actionable fix.
if is_pnpm:
    emit(
        "deny",
        f"Supply-chain cooldown ({days}d) not enforced and pnpm has no "
        f"--before equivalent. Run `python3 scripts/inject_dependency_cooldown.py "
        f"--workdir .` to write pnpm-workspace.yaml minimumReleaseAge "
        f"(constitution:C-SUPPLY/dependency_cooldown), then retry.",
    )
    sys.exit(0)

# Anything that slipped through — fail open.
print("{}")
PY

exit 0
