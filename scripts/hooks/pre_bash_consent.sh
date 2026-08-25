#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# PreToolUse hook: CLI dispatch consent gate (WARN-ONLY rollout).
#
# Contract: references/cli-dispatch-consent-contract.md — read it before
# changing anything here. Build Loop and Rally Point each shell out to another
# vendor's LLM CLI; this hook is Build Loop's enforcement point for that
# contract's "Enforcement points" table. Rally Point implements the same
# contract independently in Rust.
#
# WHAT IT DOES: when the typed Bash command invokes a vendor CLI (`claude`,
# `codex`, `cursor-agent`, `ollama`) as the LEADING command of a segment — not
# merely mentioned inside it — this hook asks scripts/cli_dispatch_consent.py
# (frozen; not owned by this file) whether that (product, vendor) pair may
# dispatch without asking. Absence of a decision, a denial, a not-yet-decided
# `ask`/`once`, and a broken hash chain are ALL "not allowed" per the contract's
# exit-code table (1/2/3); the depth guard (AGENT_DISPATCH_DEPTH > cap)
# is checked inside the module and also comes back as one of those exit codes.
# This hook never reimplements any of that logic — it only asks and reports.
#
# ROLLOUT ("Rollout" in the contract): ship warn-only. This hook NEVER emits
# permissionDecision "deny" — every not-allowed result becomes "ask", with the
# reason string naming what the REAL decision would have been, so the fire
# rate is measurable before the gate is armed. A noisy gate is worse than no
# gate.
#
# Vendor detection: match the INVOCATION, not a mention. `grep codex f.txt` and
# `# codex` must not fire — only a segment (split on `;`, `|`, `&`, matching
# the dispatcher's own newline-normalized `$CMD` convention) whose FIRST
# whitespace token is exactly one of the vendor binaries (or a path ending in
# one, e.g. `/usr/local/bin/codex`) counts as an invocation.
#
# ALWAYS exits 0 (Claude Code contract: non-zero = hook failure, NOT deny).
# Fail-open on every internal error: a missing python3, a missing consent
# module, an unparsable event, or a failed warn-count write all degrade to a
# silent `{}` (or, past the point a real decision is known, to the ask
# envelope alone) — never a crash, never a block.

set -euo pipefail

INPUT=$(cat)

# Emergency kill switch — mirrors every sibling gate. The dispatcher already
# checks this before spawning ANY sub-gate, but this hook can also be invoked
# directly (tests, a future standalone caller), so it re-checks rather than
# relying on a caller it cannot see.
if [ "${BUILD_LOOP_HOOKS:-}" = "off" ]; then
    printf '{}'
    exit 0
fi

# ── Step 1: parse the event + detect a vendor CLI invocation ────────────────
# Reads tool_input.command from the RAW event (the dispatcher passes the
# original, un-normalized stdin to every sub-gate — see pre_bash_dispatch.sh's
# `_run_gate` comment), so embedded newlines are normalized here too, the same
# way the dispatcher normalizes them for its own $CMD.
read -r -d '' _DETECT <<'PY' || true
import json, re, sys

# cursor-agent maps to the contract's vendor name "cursor" (key granularity:
# references/cli-dispatch-consent-contract.md "Key granularity").
VMAP = {"claude": "claude", "codex": "codex", "cursor-agent": "cursor", "ollama": "ollama"}


def find_vendor(cmd: str):
    normalized = cmd.replace("\r", "\n").replace("\n", ";")
    for seg in re.split(r"[;|&]", normalized):
        s = seg.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^(\S+)", s)
        if not m:
            continue
        tok = m.group(1).strip("'\"")
        base = tok.rsplit("/", 1)[-1]
        if base in VMAP:
            return VMAP[base], base
    return "", ""


try:
    d = json.load(sys.stdin)
    cwd = d.get("cwd", "") or ""
    cmd = d.get("tool_input", {}).get("command", "") or ""
except Exception:
    cwd, cmd = "", ""

vendor, matched = find_vendor(cmd) if cmd else ("", "")
print(cwd)
print(vendor)
print(matched)
PY
_PARSED=$(printf '%s' "$INPUT" | python3 -c "$_DETECT" 2>/dev/null) || _PARSED=$'\n\n'
CWD=$(printf '%s' "$_PARSED" | sed -n '1p')
VENDOR=$(printf '%s' "$_PARSED" | sed -n '2p')
MATCHED_BIN=$(printf '%s' "$_PARSED" | sed -n '3p')

# No vendor CLI invocation — the common case. Silent pass, no further cost.
if [ -z "$VENDOR" ]; then
    printf '{}'
    exit 0
fi

# ── Step 2: resolve the consent module + the repo the dispatch acts in ──────
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PLUGIN_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
fi
CONSENT_SCRIPT="$PLUGIN_ROOT/scripts/cli_dispatch_consent.py"
if [ ! -f "$CONSENT_SCRIPT" ]; then
    # Frozen module missing from this install — fail open, not blocked.
    printf '{}'
    exit 0
fi

# Best-effort repo root for the warn-count evidence file. A plain directory
# (not a work tree) falls back to CWD itself — never fails the hook over this.
REPO_ROOT=""
if [ -n "$CWD" ]; then
    REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || true)
fi
if [ -z "$REPO_ROOT" ]; then
    REPO_ROOT="$CWD"
fi

# ── Step 3: ask the consent module (read-only --check, never --set) ─────────
# Exit codes 1 (must ask) / 2 (denied or depth-exceeded) / 3 (chain broken) are
# ALL legitimate "not allowed" outcomes, not failures — `|| true` keeps
# `set -e` from treating them as a crash.
_CHECK_OUT=$(python3 "$CONSENT_SCRIPT" --product build-loop --vendor "$VENDOR" --check --json 2>/dev/null) || true
if [ -z "$_CHECK_OUT" ]; then
    # python3 unresolvable, or the module crashed with no stdout — fail open.
    printf '{}'
    exit 0
fi

# ── Step 4: WARN-ONLY decision. Never "deny" — only "{}" (silent, allowed) or
# "ask" (everything else), with the reason naming the real would-be decision.
export _BLC_CHECK="$_CHECK_OUT"
export _BLC_VENDOR="$VENDOR"
export _BLC_MATCHED_BIN="$MATCHED_BIN"
export _BLC_REPO_ROOT="$REPO_ROOT"

_FINAL=$(python3 - <<'PY' 2>/dev/null
import json, os, sys
from datetime import datetime, timezone


def emit(d):
    print(json.dumps(d))


try:
    check_raw = os.environ.get("_BLC_CHECK", "")
    vendor = os.environ.get("_BLC_VENDOR", "")
    matched = os.environ.get("_BLC_MATCHED_BIN", "") or vendor
    repo_root = os.environ.get("_BLC_REPO_ROOT", "")

    try:
        result = json.loads(check_raw)
    except Exception:
        # Unparsable --check output — fail open silently rather than guess.
        emit({})
        sys.exit(0)

    if bool(result.get("allowed")):
        # Real decision was allow-without-asking. Silent pass either way.
        emit({})
        sys.exit(0)

    exit_code = result.get("exit")
    reason = result.get("reason", "")
    key = result.get("key") or f"build-loop:{vendor}"

    # Contract exit codes (cli_dispatch_consent.py "Exit codes"): 1 = must ask,
    # 2 = denied (incl. depth-guard refusal), 3 = chain does not verify.
    LABELS = {1: "must ask", 2: "denied", 3: "chain broken (treated as not-allowed)"}
    label = LABELS.get(exit_code, "not allowed")

    warn_reason = (
        f"[WARN-ONLY — cli-dispatch-consent gate is measuring, not enforcing] "
        f"build-loop is about to run {vendor} ({matched}) through its command "
        f"line. Real decision would be: {label} (exit {exit_code}) for "
        f"{key} — {reason}"
    )

    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": warn_reason,
        }
    })

    # Evidence for the later decision to arm this gate (contract "Rollout").
    # Best-effort only — never let a write failure affect the hook's exit.
    try:
        if repo_root:
            log_dir = os.path.join(repo_root, ".build-loop")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "consent-warn-count.jsonl")
            entry = {
                "timestamp": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "vendor": vendor,
                "would_be_exit": exit_code,
                "key": key,
                "reason": reason,
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
except Exception:
    # Anything unexpected above this line still must not crash the hook.
    emit({})
PY
) || true
[ -z "$_FINAL" ] && _FINAL='{}'
printf '%s' "$_FINAL"
exit 0
