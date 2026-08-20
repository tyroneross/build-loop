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
#
# f3: the COMMAND can be multi-line (`git add -A\ngit commit\ngit push`), so it
# cannot be a fixed line-N field. Print the single-line CWD FIRST, then the
# command as everything after it — CMD = lines 2..$ captures the whole command
# regardless of embedded newlines. (The old order — command then cwd on line 2
# — put line 2 of a multi-line command into CWD and dropped every push/commit
# past line 1, so NO gate ran.) Embedded newlines are then normalized to `;` so
# the `case` guards and the classifier's `[;|&]` segment splitter see every
# segment; the sub-gates still receive the raw event on stdin (unaffected).
# Normalization happens INSIDE python (newlines → ';') so the command emerges as
# a single line — the shell path stays python3 + sed only (no `tr`), preserving
# the minimal-PATH fail-open contract.
read -r -d '' _EXTRACT <<'PY' || true
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get("cwd", ""))
    cmd = d.get("tool_input", {}).get("command", "")
    print(cmd.replace("\r", "\n").replace("\n", ";"))
except Exception:
    print("")
    print("")
PY
_PARSED=$(printf '%s' "$INPUT" | python3 -c "$_EXTRACT" 2>/dev/null) || _PARSED=$'\n'
CWD=$(printf '%s' "$_PARSED" | sed -n '1p')
CMD=$(printf '%s' "$_PARSED" | sed -n '2,$p')

# No command — pass through silently.
if [ -z "$CMD" ]; then
    printf '{}'
    exit 0
fi

# ── Privileged-command gate ──────────────────────────────────────────────────
# Runs BEFORE the build-loop scope guard, and is the ONE gate here that does.
# Every other gate polices repository work, so confining it to opted-in repos is
# correct. An administrator-password dialog is not repository work: the same
# `sfltool dumpbtm` opens the same anonymous dialog from any directory, and a
# guard that let it through from an un-opted-in repo would be a silent hole in
# exactly the control this gate exists to provide.
#
# Confining it here is safe because the matcher is EXACT — the sub-gate resolves
# argv against scripts/privileged_commands.json, so `spctl -a` (unprivileged read)
# and `spctl --add` (root) get opposite answers. The `case` below is only a cheap
# pre-filter that decides whether to spawn at all; it deliberately over-matches
# and lets the sub-gate say no. The character class anchors each name to a
# command position (start, separator, or path), so prose and paths containing
# "security" or "sudo" do not pay for a spawn.
#
# Named failure it exists for (2026-08-20): one Codex turn ran `sfltool dumpbtm`
# twice, 14 seconds apart, producing two administrator-password dialogs that named
# only "sfltool" — no app, no repository, no reason. Three sessions reached for
# the same host fact inside 27 minutes with nothing coalescing them.
#
# A deny here is a REDIRECT, not a refusal: the reason carries the exact brokered
# command to re-issue. It short-circuits the remaining gates because deny already
# wins the merge — once the command is being rewritten, no other verdict matters.
case " $CMD" in
    *[\ \;\|\&\(/]sfltool*|*[\ \;\|\&\(/]sudo*|*[\ \;\|\&\(/]csrutil*|\
    *[\ \;\|\&\(/]spctl*|*[\ \;\|\&\(/]systemsetup*|*[\ \;\|\&\(/]authopen*|\
    *[\ \;\|\&\(/]nvram*|*[\ \;\|\&\(/]bputil*|*[\ \;\|\&\(/]kmutil*|\
    *[\ \;\|\&\(/]softwareupdate*|*[\ \;\|\&\(/]pmset*|*[\ \;\|\&\(/]dscl*|\
    *[\ \;\|\&\(/]launchctl*|*[\ \;\|\&\(/]installer*|*[\ \;\|\&\(/]security*|\
    *[\ \;\|\&\(/]dsenableroot*|*[\ \;\|\&\(/]tmutil*|*[\ \;\|\&\(/]fdesetup*|\
    *[\ \;\|\&\(/]diskutil*)
        _PRIV_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
        if [ -z "$_PRIV_ROOT" ]; then
            _PRIV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
            _PRIV_ROOT="$(dirname "$(dirname "$_PRIV_DIR")")"
        fi
        _PRIV_GATE="$_PRIV_ROOT/scripts/hooks/pre_bash_privileged.py"
        if [ -f "$_PRIV_GATE" ]; then
            _PRIV_OUT=$(printf '%s' "$INPUT" | python3 "$_PRIV_GATE" 2>/dev/null) || _PRIV_OUT='{}'
            case "$_PRIV_OUT" in
                *'"permissionDecision": "deny"'*|*'"permissionDecision":"deny"'*)
                    printf '%s' "$_PRIV_OUT"
                    exit 0
                    ;;
            esac
        fi
        ;;
esac

# ── Per-repo state resolution ────────────────────────────────────────────────
# Every `.build-loop/` read below resolves against the REPOSITORY THE COMMAND
# ACTS ON, never the session's working directory. The two differ routinely —
# `cd /other/repo && git push`, `git -C /other/repo push`, or a session parked in
# a subdirectory — and the difference used to be silent, because a config that
# does not exist at the guessed path reads as "no config".
#
# Named failure that earns this (2026-08-03): a vault push was gated on the
# SESSION repo's config instead of the vault's, so the vault's own
# securityScan.excludeGlobs were dropped and the push blocked on 2 HIGH / 40
# MEDIUM findings that all sat inside already-excluded paths. Re-running the
# scanner with those excludes returned exit 0. The same push then succeeded once
# the shell happened to sit in the vault — which makes the gate a property of
# where the shell is parked rather than of the repository, and teaches users to
# reach for BUILD_LOOP_HOOKS=off.

# Directory → the root of the repository that owns it. `.build-loop/` lives at
# the repo root, so a command run from a subdirectory must still find it. Falls
# back to the directory itself when it is not a work tree (the scanner tolerates
# a plain directory; never fail the hook over this).
_bl_repo_root() {
    local dir="$1" top=""
    if [ -z "$dir" ] || [ ! -d "$dir" ]; then
        printf '%s' "$dir"
        return 0
    fi
    top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)
    if [ -n "$top" ] && [ -d "$top" ]; then
        printf '%s' "$top"
    else
        printf '%s' "$dir"
    fi
}

# The directory the COMMAND operates on. Resolution order mirrors what git
# itself honours:
#   1. an explicit `git -C <path>`
#   2. a `cd <path>` leading the command
#   3. $CWD
# Relative candidates resolve against $CWD (the shell's directory), not the hook
# process's — the hook runs wherever Claude Code spawned it.
_bl_effective_dir() {
    local git_c cd_p cand
    git_c=$(printf '%s' "$CMD" | sed -n 's/.*git[[:space:]]\{1,\}-C[[:space:]]\{1,\}\([^[:space:];&|]\{1,\}\).*/\1/p' | head -1 || true)
    cd_p=$(printf '%s' "$CMD" | sed -n 's/^[[:space:]]*cd[[:space:]]\{1,\}\([^;&|]\{1,\}\).*/\1/p' | head -1 || true)
    cd_p=$(printf '%s' "$cd_p" | sed -e 's/[[:space:]]*$//' -e "s/[\"']//g" || true)
    for cand in "$git_c" "$cd_p"; do
        [ -n "$cand" ] || continue
        case "$cand" in
            "~"*) cand="$HOME${cand#\~}" ;;
            /*) : ;;
            *) cand="$CWD/$cand" ;;
        esac
        [ -d "$cand" ] || continue
        printf '%s' "$cand"
        return 0
    done
    printf '%s' "$CWD"
}

# securityScan.excludeGlobs from a repo's own config, one glob per line.
#
# Silence policy: a repo with NO config legitimately has no excludes, so a
# missing file stays a quiet no-op. A config that EXISTS but cannot be read is a
# different thing — its owner wrote dispositions the scan is about to ignore —
# so it says so on stderr. Silent degradation is precisely what let the
# wrong-directory read hide for as long as it did.
_bl_exclude_globs() {
    local cfg="$1" out="" rc=0
    [ -f "$cfg" ] || return 0
    out=$(python3 - "$cfg" <<'PY'
import json, sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON is not an object")
    section = data.get("securityScan", {})
    if not isinstance(section, dict):
        raise ValueError("securityScan is not an object")
    globs = section.get("excludeGlobs", [])
    if not isinstance(globs, list):
        raise ValueError("securityScan.excludeGlobs is not a list")
except Exception as exc:
    print(f"[build-loop] {path}: {exc}", file=sys.stderr)
    sys.exit(3)
for g in globs:
    if isinstance(g, str) and g:
        print(g)
PY
) || rc=$?
    if [ "$rc" != "0" ]; then
        printf '[build-loop] security config exists at %s but could not be read — scanning WITHOUT its excludeGlobs.\n' "$cfg" >&2
        return 0
    fi
    printf '%s' "$out"
}

# Scope guard (mirrors the sub-hooks): only police Bash in build-loop projects.
# Empty/root/HOME cwd never enforces — HOME hosts ~/.build-loop/ (global memory
# + audit state), so a literal existence check there would arm the gate for
# every shell command run from the user's home directory.
if [ -z "$CWD" ] || [ "$CWD" = "/" ] || [ "$CWD" = "$HOME" ]; then
    printf '{}'
    exit 0
fi
# The opt-in marker lives at the REPO ROOT, so checking $CWD alone made
# enforcement depend on how deep in the tree the shell happened to sit: a session
# in `<repo>/.obsidian/plugins/<x>` escaped every gate in a repo that HAD opted
# in — accidental non-enforcement, not a security property. $CWD is checked
# first, so an in-root session (the common case) still costs no subprocess.
BL_SCOPE_ROOT="$CWD"
if [ ! -f "$CWD/.build-loop/state.json" ] && [ ! -f "$CWD/.build-loop/config.json" ]; then
    BL_SCOPE_ROOT=$(_bl_repo_root "$CWD")
fi
if [ "$BL_SCOPE_ROOT" = "/" ] || [ "$BL_SCOPE_ROOT" = "$HOME" ]; then
    printf '{}'
    exit 0
fi
if [ ! -f "$BL_SCOPE_ROOT/.build-loop/state.json" ] && [ ! -f "$BL_SCOPE_ROOT/.build-loop/config.json" ]; then
    printf '{}'
    exit 0
fi

# Resolve plugin root for locating sub-scripts.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PLUGIN_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
fi

# ── Unbounded-wait gate ──────────────────────────────────────────────────────
# Runs FIRST and cheap: a wait with no exit condition is wrong regardless of what
# else the command does, and blocking it here costs one short subprocess.
#
# Named failure it exists for (2026-07-27, atomize-ai): a dispatched orchestrator
# wrote `while true; do sleep 30; done` as a placeholder wait on its reviewer
# subagents. Nothing could end it; it burned ~100 min of wall clock, was killed on
# timeout, and emitted a SECOND spurious completion for an already-finished run.
#
# Fails open on any error — see the gate's own contract. It speaks through stderr
# and exit 2, so there is no JSON to merge.
_WAIT_GATE="$PLUGIN_ROOT/scripts/hooks/unbounded_wait_gate.py"
if [ -f "$_WAIT_GATE" ]; then
    if ! printf '%s' "$INPUT" | python3 "$_WAIT_GATE" >/dev/null; then
        printf '{}'
        exit 2
    fi
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

# Classify the command's GENUINE git subcommands ONCE (segment-wise + heredoc-aware).
# Replaces the coarse `case "$CMD" in *commit*` / `*git*push*` substring globs that
# false-fired on repo paths containing "git", prose containing "push"/"pushed", and heredoc
# TEXT containing example git commands (6+ false fires, 2026-07-11 — each dumping a
# ~40-finding full-repo scan into context, the heredoc also tripping the commit-audit
# packet builder). The classifier reads the RAW command (newlines intact) from $INPUT, so
# it sees heredoc structure the normalized $CMD has already flattened.
#
# Bounded-failure contract. The classifier ALWAYS exits 0 (its own conservatism
# contract already returns "commit push" on parse ambiguity — a positively-classified
# verdict that still scans). So the `|| { … }` block fires ONLY when the subprocess
# could not RUN AT ALL: python3 unresolvable in a minimal-PATH hook env, or a spawn
# failure under load. A subprocess that never produced a verdict must NOT be upgraded
# into one. The OLD fallback set "commit push" for ANY *git*-matching command, so a
# transient classifier outage turned every `git commit` / `git status` / `grep git`
# into a FULL-REPO security scan that HARD-BLOCKED on doc-embedded example keys
# (observed 2026-07-14: a whole session's bash frozen in a large .build-loop docs repo
# with false-positive findings). Bounded degrade: SKIP both gates with a warning. Only a
# positively-classified `git push` (the classifier RAN and said push) ever triggers the
# scan; a real `git push` secret is still caught on the normal classifier-runs path (the
# security scan itself is unchanged). A transient outage trades that rare window for never
# wedging the session — the correct bound, since a hard-block on a false positive is a
# worse, more common failure than a missed scan during a spawn outage.
_GITCLASS=$(printf '%s' "$INPUT" | python3 "$PLUGIN_ROOT/scripts/hooks/git_command_classifier.py" 2>/dev/null) || {
    _GITCLASS=""
    printf '[build-loop] git command classifier could not run (transient) — commit/push gates skipped for this command.\n' >&2
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
case " $_GITCLASS " in
    *" commit "*)
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
# The guard is now driven by git_command_classifier.py (see $_GITCLASS above):
# a `push` word appears ONLY when a real `git push` segment was parsed — heredoc
# TEXT, repo paths, and prose no longer false-fire. This admits every genuine
# spelling (`git -C <path> push`, `git<TAB>push`, compound/piped pushes) because
# the classifier parses argv, not substrings. The INNER classifier below then
# decides delta-scope vs full-scan; a push that is not provably plain OMITs
# --diff → full scan, the correct conservative default (never scan less than
# intended).
SECURITY_HARD_BLOCK=0
_SEC_SCAN_RAN=0
case " $_GITCLASS " in
    *" push "*)
        _SCAN="$PLUGIN_ROOT/scripts/security_scan.py"
        if [ -f "$_SCAN" ] && command -v python3 >/dev/null 2>&1; then
            _SEC_SCAN_RAN=1
            # --spot-check widens a delta scan back to whole-tree coverage using
            # the high-confidence check subset. The delta still gets every check;
            # the rest of the tree is swept for the classes that are worth
            # knowing about on any ship (secrets, injection, broken object authz,
            # fail-open auth, client-exposed keys, token hygiene, CORS). Those
            # findings are ADVISORY — only a CRITICAL among them blocks — so the
            # gate covers the whole repo without a stranger's old MEDIUM wedging
            # an unrelated push.
            # Scan the repo BEING PUSHED, not the shell's working directory.
            # $CWD is where the shell happens to sit, which is not necessarily
            # what `git push` targets — observed 2026-07-30: a push of atomize-ai
            # was blocked by two findings in build-loop, which is both a false
            # block and a false clean (the pushed repo went unscanned).
            # _bl_effective_dir / _bl_repo_root carry the resolution rules; the
            # root normalisation matters because a subdirectory would scan only
            # part of the tree.
            _SCAN_TARGET=$(_bl_repo_root "$(_bl_effective_dir)")
            _SCAN_ARGS=(--path "$_SCAN_TARGET" --fail-on high --spot-check)
            # Scope the scan to the push delta: only what's actually being pushed
            # (files changed vs the upstream tracking branch), not the whole tree.
            # No upstream (detached/new branch) → keep the whole-repo scan (safe
            # fallback; scanner also falls back on any bad ref).
            _UPSTREAM=$(git -C "$_SCAN_TARGET" rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || true)
            # Current branch — the push is only plain when the pushed ref IS the
            # current branch (h2: comparing to the tracking STRING alone let
            # `git push origin main` on a feature branch scope to the wrong,
            # empty delta while local main's secret shipped). Empty on detached
            # HEAD → classifier can't prove plain → full scan.
            _BRANCH=$(git -C "$_SCAN_TARGET" symbolic-ref --short HEAD 2>/dev/null || true)
            # f2: the command string does NOT determine the push destination —
            # push config does. Two shapes classify "plain" from ref-name
            # equality alone yet ship content outside upstream..HEAD:
            #   - push.default=matching → bare `git push` ships ALL matching
            #     branches, not just the current one.
            #   - triangular config (remote.pushDefault / branch.<n>.pushRemote)
            #     → bare push goes to the PUSH remote, not @{u}'s remote.
            # @{push} resolves the triangular destination; the explicit
            # push.default check excludes `matching`, whose multi-branch
            # semantics @{push} (current-branch destination only) cannot
            # represent. The push is config-plain ONLY when push.default is
            # empty/simple/upstream/current AND @{push} == @{u}. Anything else →
            # _CFG_PLAIN=no → OMIT --diff → full scan (fail-safe).
            _PDEF=$(git -C "$_SCAN_TARGET" config --get push.default 2>/dev/null || true)
            _PUSHDEST=$(git -C "$_SCAN_TARGET" rev-parse --abbrev-ref @{push} 2>/dev/null || true)
            _CFG_PLAIN=no
            case "$_PDEF" in
                ""|simple|upstream|current)
                    if [ -n "$_UPSTREAM" ] && [ "$_PUSHDEST" = "$_UPSTREAM" ]; then
                        _CFG_PLAIN=yes
                    fi
                    ;;
            esac
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
        # f1: AND the tracked branch must BE the current branch — mirror the
        # bare/1-positional arms. Without `up_branch == branch`, a branch that
        # tracks a differently-named upstream (main tracks origin/develop) let
        # `git push origin main` classify plain and scope to develop..HEAD (the
        # wrong range), shipping main's secret unseen.
        return positionals[0] == rem and positionals[1] == branch and up_branch == branch
    return False                           # 3+ positionals (multi-ref) → not plain

# h1 — classify EVERY `git push` occurrence, each segment up to its next shell
# control operator (&& || ; | &). Plain ONLY if ALL segments are plain; any
# segment not provably plain → full scan.
# f4 — tolerate global git options between `git` and `push` (`git -C <path>
# push`, `git -c k=v push`, `git --no-pager push`) and any whitespace (TAB /
# double-space). Over-matching stays safe: `is_plain` re-parses the segment and
# a leading global option makes toks[1] != 'push' → not plain → full scan.
found = False
plain = True
for m in re.finditer(r"git(\s+(-[cC]|--[a-z-]+)(\s+\S+|=\S*)?)*\s+push", cmd):
    found = True
    seg = re.split(r"&&|\|\||[;|&]", cmd[m.start():], maxsplit=1)[0]
    if not is_plain(seg):
        plain = False
        break
print("yes" if (found and plain) else "no")
PY
)
                # Scope to the delta ONLY when the command classifies plain AND
                # push config agrees the destination is @{u} (f2). Either alone
                # is insufficient: the command can't see config, and config
                # can't see a refspec/flag in the command.
                if [ "$_PLAIN" = "yes" ] && [ "$_CFG_PLAIN" = "yes" ]; then
                    _SCAN_ARGS+=(--diff "$_UPSTREAM")
                fi
                # else: non-plain push → omit --diff → full-repo scan.
            fi
            # excludeGlobs from the config of the repo BEING PUSHED. The globs
            # are matched relative to --path, so config and scan root must be the
            # same directory — reading one repo's dispositions while scanning
            # another's tree drops every exclude silently.
            _EX_GLOBS=$(_bl_exclude_globs "$_SCAN_TARGET/.build-loop/config.json")
            while IFS= read -r _glob; do
                if [ -n "$_glob" ]; then
                    _SCAN_ARGS+=(--exclude "$_glob")
                fi
            done <<EOF
$_EX_GLOBS
EOF
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

# Pre-DEPLOY security gate. `git push` is only one way code reaches users:
# `vercel deploy`, `wrangler deploy`, `flyctl deploy`, `railway up`, `eas
# submit`, an App Store upload, and `npm publish` all ship without ever touching
# the push path, so a push-only gate leaves every one of them uncovered.
#
# Reuses scripts/deployment_policy.py as the single command classifier — the
# same one the orchestrator consults for confirm/block policy — rather than
# inventing a second deploy taxonomy that would drift from it.
#
# Scope: a deploy publishes the WHOLE tree, not a delta, so this path always
# full-scans (no --diff) with the spot subset applied to nothing — every file
# gets every check. Skipped when the push gate above already scanned, so a
# `git push` is never scanned twice.
#
# Fail-open on any classifier or scanner error, matching the push gate: a broken
# helper must never wedge a deploy.
if [ "$_SEC_SCAN_RAN" = "0" ]; then
    _DSCAN="$PLUGIN_ROOT/scripts/security_scan.py"
    _DPOL="$PLUGIN_ROOT/scripts/deployment_policy.py"
    if [ -f "$_DSCAN" ] && [ -f "$_DPOL" ] && command -v python3 >/dev/null 2>&1; then
        # is_deploy_like() splits compound commands itself, so
        # `npm run build && vercel deploy --prod` gates on the deploy segment
        # rather than on whatever leads the line.
        _DTARGET=$(CMD="$CMD" python3 - "$_DPOL" <<'PY' 2>/dev/null || true
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("deployment_policy", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
cmd = os.environ.get("CMD", "")
if mod.is_deploy_like(cmd):
    # Name the policy target when it has one, for the operator-facing message.
    try:
        target, _reason = mod.classify_command(cmd)
    except Exception:
        target = "unknown"
    print(target if target != "unknown" else "deploy")
PY
)
        case "$_DTARGET" in
            production|testflight|preview|deploy)
                # Same resolution as the push gate: `cd apps/web && vercel
                # deploy` ships a repo the shell need not be sitting in, so both
                # the scan root and the config come from the acted-on repo.
                _DSCAN_TARGET=$(_bl_repo_root "$(_bl_effective_dir)")
                _DSCAN_ARGS=(--path "$_DSCAN_TARGET" --fail-on high)
                _DEX_GLOBS=$(_bl_exclude_globs "$_DSCAN_TARGET/.build-loop/config.json")
                while IFS= read -r _dglob; do
                    if [ -n "$_dglob" ]; then
                        _DSCAN_ARGS+=(--exclude "$_dglob")
                    fi
                done <<EOF
$_DEX_GLOBS
EOF
                _DSCAN_RC=0
                _DSCAN_OUT=$(python3 "$_DSCAN" "${_DSCAN_ARGS[@]}" 2>&1) || _DSCAN_RC=$?
                if [ "$_DSCAN_RC" = "1" ]; then
                    SECURITY_HARD_BLOCK=1
                    printf '%s\n' "$_DSCAN_OUT" >&2
                    printf '\n[build-loop] Pre-deploy security scan found HIGH+ findings on a %s deploy — blocked.\nFix them, annotate a confirmed false positive with `// nosec: <reason>`, or set BUILD_LOOP_HOOKS=off to bypass.\n' "$_DTARGET" >&2
                fi
                ;;
        esac
    fi
fi

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
