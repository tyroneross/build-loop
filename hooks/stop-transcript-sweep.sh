#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# stop-transcript-sweep.sh — launch ONE background transcript sweep from the
# Claude Code `Stop` hook.
#
#   $1 = sweep: all | decisions | corrections | findings | cost-ledger
#
#        `all` is the ONLY mode that should be REGISTERED as a hook. The four
#        sweeps share a firing moment, an input, and an intent (extract signals
#        from this session's transcript), so four registrations were four
#        copies of one hook -- each re-resolving and re-normalizing the same
#        transcript. One hook resolves once and fans out. The individual modes
#        remain callable for testing and manual re-runs.
#
# WHY THIS EXISTS (the defect it fixes)
# ------------------------------------
# The four Stop-hook sweeps were previously guarded inline with
# `if [ -n "$CLAUDE_TRANSCRIPT_PATH" ]; then ... fi`. **There is no
# `CLAUDE_TRANSCRIPT_PATH` environment variable.** Claude Code delivers the
# transcript path ONLY inside the stdin JSON payload, as `transcript_path`
# (see https://code.claude.com/docs/en/hooks.md — documented hook env vars are
# CLAUDE_PROJECT_DIR / CLAUDE_PLUGIN_ROOT / CLAUDE_PLUGIN_DATA / CLAUDE_EFFORT /
# CLAUDE_CODE_REMOTE / CLAUDE_CODE_BRIDGE_SESSION_ID / CLAUDE_PLUGIN_OPTION_*).
# The guard was therefore false in every real session, the python never
# launched, and nothing logged — a completely silent failure. Forensics on
# `~/.local/state/build-loop/scan.log` (2026-05-05 .. 2026-08-11) found 0
# references to a real `~/.claude/projects/` transcript. See
# `scripts/hooks/session_end_retro_sweep.py::resolve_transcript` for the same
# note, and hooks/hooks.json SessionEnd for the pattern this mirrors.
#
# STDIN CONTRACT (verified live, 2026-08-14, Claude Code 2.1.232)
# ---------------------------------------------------------------
# Sibling hooks inside ONE matcher group run in PARALLEL and EACH command
# process is spawned with its OWN stdin pipe carrying a full copy of the
# payload. A three-sibling `cat > file` probe produced three byte-identical
# 562-byte payloads (same md5), so draining stdin here cannot starve a later
# hook. Reading stdin independently per entry is correct; no read-once-and-share
# coordination is needed. (`scripts/hooks/stop_finalize.sh` already does
# `INPUT=$(cat)` in this same group and has always worked, consistent with
# this.)
#
# Contract:
#   - Advisory + fail-open: ALWAYS exit 0; never emits `decision: block`.
#   - Emits `{}` on stdout (valid Stop-hook JSON) and nothing on stderr.
#   - Backgrounds the sweep (`nohup ... &`) so the hook returns in <500ms.
#   - Minimal-PATH safe: hooks run under /usr/bin:/bin. python3 is resolved via
#     `command -v` + absolute fallbacks (hooks/_resolve_python.sh); missing
#     python → silent no-op exit 0.
#   - Payload values reach python via ARGV / env, never by shell-string
#     interpolation into python source (closes docs/SECURITY_FOLLOWUP_2026-05-05.md).
#
# See memory `reference_hooks_minimal_path_failopen`.

set -u

SWEEP="${1:-}"

# Always emit valid no-op hook JSON and exit 0, whatever happened.
_noop() {
    printf '{}'
    exit 0
}

[ -n "$SWEEP" ] || _noop

# Read the Stop payload once. Empty/absent stdin is a normal no-op.
# Empty stdin is NORMAL on a host that supplies no Stop payload (Codex). The
# old `[ -n "$PAYLOAD" ] || _noop` here made every non-Claude host a silent
# no-op before the transcript was ever looked for.
PAYLOAD="$(cat 2>/dev/null || true)"

# Resolve python3 without depending on a populated PATH (shared helper).
_HOOK_DIR="$(dirname "$0")"
_py=""
[ -f "${_HOOK_DIR}/_resolve_python.sh" ] && . "${_HOOK_DIR}/_resolve_python.sh"
[ -n "$_py" ] || _noop

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}"

# Extract transcript_path + cwd from the payload. The payload is passed through
# the environment (never interpolated into the python source) so a value
# containing quotes or shell metacharacters cannot inject.
_BL_STOP_PAYLOAD="$PAYLOAD"
export _BL_STOP_PAYLOAD
# Two newline-separated lines: transcript_path, then cwd. Newlines are stripped
# from the values so the line framing can't be spoofed by a crafted payload.
_parsed="$(
    "$_py" - <<'PY' 2>/dev/null
import json, os
try:
    d = json.loads(os.environ.get("_BL_STOP_PAYLOAD", "") or "{}")
    if not isinstance(d, dict):
        d = {}
except Exception:
    d = {}


def safe(v):
    return " ".join(str(v or "").split("\n"))


print(safe(d.get("transcript_path")))
print(safe(d.get("cwd")))
PY
)" || _parsed=""

TRANSCRIPT="$(printf '%s\n' "$_parsed" | sed -n '1p')"
CWD="$(printf '%s\n' "$_parsed" | sed -n '2p')"
[ -n "$CWD" ] || CWD="${CLAUDE_PROJECT_DIR:-$PWD}"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/build-loop"

# No transcript in the payload: resolve the host's own session from CWD and
# normalize it to the Claude shape every sweep below parses. One call does both
# -- handing back a raw Codex path would move the no-op one step downstream.
# Claude always supplies transcript_path, so this is for hosts that do not.
if [ -z "$TRANSCRIPT" ]; then
    _adapter="${PLUGIN_ROOT}/scripts/transcript_adapter.py"
    if [ -f "$_adapter" ]; then
        TRANSCRIPT="$("$_py" "$_adapter" --find-codex-session "$CWD" \
            --cache-dir "$STATE_DIR/transcript-cache" 2>/dev/null)" || TRANSCRIPT=""
    fi
fi

# Every sweep needs a transcript; check once rather than in each arm.
[ -n "$TRANSCRIPT" ] || _noop

_run_one() {
    case "$1" in
    decisions)
        nohup "$_py" "${PLUGIN_ROOT}/scripts/scan_transcript_for_decisions.py" \
            --workdir "$CWD" \
            --transcript "$TRANSCRIPT" \
            --log-file "${STATE_DIR}/scan.log" \
            </dev/null >/dev/null 2>&1 &
        ;;
    corrections)
        nohup env PYTHONPATH="${PLUGIN_ROOT}/scripts" "$_py" -m scan_corrections \
            --workdir "$CWD" \
            --transcript "$TRANSCRIPT" \
            --source stop-hook \
            </dev/null >/dev/null 2>&1 &
        ;;
    findings)
        nohup env PYTHONPATH="${PLUGIN_ROOT}/scripts" "$_py" -m scan_findings \
            --workdir "$CWD" \
            --transcript "$TRANSCRIPT" \
            --log-file "${STATE_DIR}/findings-scan.log" \
            </dev/null >/dev/null 2>&1 &
        ;;
    cost-ledger)
        # cost_ledger_hook.py parses the Stop payload itself (it needs
        # session_id + cwd + transcript_path), so hand it the RAW payload on
        # stdin rather than argv. printf writes and exits; the sweep stays
        # detached.
        printf '%s' "$PAYLOAD" \
            | nohup "$_py" "${PLUGIN_ROOT}/scripts/cost_ledger_hook.py" \
                >/dev/null 2>&1 &
        ;;
    esac
}

if [ "$SWEEP" = "all" ]; then
    for one in decisions corrections findings cost-ledger; do
        _run_one "$one"
    done
else
    _run_one "$SWEEP"
fi

_noop
