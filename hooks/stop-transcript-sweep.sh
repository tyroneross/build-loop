#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# stop-transcript-sweep.sh — launch ONE background transcript sweep from the
# Claude Code `Stop` hook.
#
#   $1 = sweep: all | decisions | corrections | findings | traces | cost-ledger
#
#        `all` is the ONLY mode that should be REGISTERED as a hook. The five
#        consumers share the session event and one bounded worker. One hook
#        resolves the transcript once and fans out. The individual modes
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
# Foreground work only dispatches; the worker inherits the payload pipe. Hashing, parsing,
# cache waits and consumers belong to the existing worker invocation.
if [ "${2:-}" != "--worker" ]; then
    # Keep the inherited pipe open across the supervisor; payload bytes must
    # never become one environment/argv string (exec has per-string limits).
    nohup "$BASH" "$0" "$SWEEP" --worker <&0 >/dev/null 2>&1 &
    printf '{}'
    exit 0
fi
# Reuse the existing process-group supervisor to bound detached consumers too.
_WORKER_HOOK_DIR="$(dirname "$0")"
. "$_WORKER_HOOK_DIR/_bounded_hook.sh"
bl_bound_hook "${BUILD_LOOP_TRANSCRIPT_WORKER_MS:-120000}" 120000 advisory "$BASH" "$0" "$SWEEP" --worker

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
# A sentinel preserves trailing JSON whitespace through command substitution.
PAYLOAD="$(cat 2>/dev/null && printf '.')" || PAYLOAD="."
PAYLOAD="${PAYLOAD%.}"

# Resolve python3 without depending on a populated PATH (shared helper).
_HOOK_DIR="$(dirname "$0")"
_py=""
[ -f "${_HOOK_DIR}/_resolve_python.sh" ] && . "${_HOOK_DIR}/_resolve_python.sh"
[ -n "$_py" ] || _noop

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/build-loop"

# Extract transcript_path + cwd through stdin, never shell source or exec
# environment. The original JSON remains available for billing below.
# Four lines: original path, cwd, explicit-path presence, declared session ID.
# Newlines are stripped
# from the values so the line framing can't be spoofed by a crafted payload.
_parsed="$(
    printf '%s' "$PAYLOAD" | "$_py" -c '
import json, sys
try:
    d = json.loads(sys.stdin.read() or "{}")
    if not isinstance(d, dict):
        d = {}
except Exception:
    d = {}


def safe(v):
    return " ".join(str(v or "").split("\n"))


transcript = safe(d.get("transcript_path"))
provided = bool(transcript)
print(transcript)
print(safe(d.get("cwd")))
print("1" if provided else "0")
print(safe(d.get("session_id")))
' 2>/dev/null
)" || _parsed=""

TRANSCRIPT="$(printf '%s\n' "$_parsed" | sed -n '1p')"
CWD="$(printf '%s\n' "$_parsed" | sed -n '2p')"
TRANSCRIPT_PROVIDED="$(printf '%s\n' "$_parsed" | sed -n '3p')"
SESSION_ID="$(printf '%s\n' "$_parsed" | sed -n '4p')"
ORIGINAL_TRANSCRIPT="$TRANSCRIPT"
[ -n "$CWD" ] || CWD="${CLAUDE_PROJECT_DIR:-$PWD}"

# Billing remains a direct shell child so the shared process-group deadline
# covers its full lifetime. Send the original event before expensive parsing.
if [ "$TRANSCRIPT_PROVIDED" = "1" ]; then
    case "$SWEEP" in
        all|cost-ledger)
            printf '%s' "$PAYLOAD" | nohup "$_py" "${PLUGIN_ROOT}/scripts/cost_ledger_hook.py" >/dev/null 2>&1 &
            ;;
    esac
fi
if [ "$SWEEP" = cost-ledger ]; then
    wait
    _noop
fi

if [ -n "$TRANSCRIPT" ]; then
    TRANSCRIPT="$(
        _BL_SWEEP_SOURCE="$TRANSCRIPT" _BL_SWEEP_SCRIPTS="${PLUGIN_ROOT}/scripts" \
        _BL_SWEEP_CACHE="$STATE_DIR/transcript-cache" _BL_SWEEP_OWNER_PID="$$" "$_py" - <<'PY_NORMALIZE' 2>/dev/null
import os, sys
from pathlib import Path
transcript = os.environ["_BL_SWEEP_SOURCE"]
if transcript:
    # A native Codex payload already contains its rollout path. Normalize it
    # here too; the cwd-discovery fallback below only covers a missing path.
    sys.path.insert(0, os.environ["_BL_SWEEP_SCRIPTS"])
    try:
        import transcript_adapter as adapter
    except ImportError:
        pass  # Preserve the existing Claude-only path when no adapter ships.
    else:
        try:
            if adapter.detect_format(Path(transcript)) == adapter.CODEX:
                transcript = str(adapter.normalize_cached(
                    Path(transcript), Path(os.environ["_BL_SWEEP_CACHE"]),
                    lease_owner_pid=int(os.environ["_BL_SWEEP_OWNER_PID"])
                ) or "")
        except (OSError, ValueError, ImportError, TypeError):
            transcript = ""  # preserve event identity even if adapter IO fails
print(transcript)
PY_NORMALIZE
    )" || TRANSCRIPT=""
fi

# No transcript in the payload: resolve the host's own session from CWD and
# normalize it to the Claude shape every sweep below parses. One call does both
# -- handing back a raw Codex path would move the no-op one step downstream.
# Claude always supplies transcript_path, so this is for hosts that do not.
if [ -z "$TRANSCRIPT" ] && [ "$TRANSCRIPT_PROVIDED" != "1" ]; then
    _adapter="${PLUGIN_ROOT}/scripts/transcript_adapter.py"
    if [ -f "$_adapter" ]; then
        TRANSCRIPT="$("$_py" "$_adapter" --find-codex-session "$CWD" \
            --cache-dir "$STATE_DIR/transcript-cache" --lease-owner-pid "$$" 2>/dev/null)" || TRANSCRIPT=""
    fi
fi

# An explicit path belongs to this event even if text normalization failed.
# Never rediscover another session, and keep original cost attribution usable.
[ -n "$TRANSCRIPT" ] || [ "$TRANSCRIPT_PROVIDED" = "1" ] || _noop

_run_one() {
    [ "$1" = "traces" ] || [ -n "$TRANSCRIPT" ] || return 0
    case "$1" in
    traces)
        if [ -n "$TRANSCRIPT" ]; then
            local trace_args=(--workdir "$CWD" ingest --transcript "$TRANSCRIPT" --session-id "$SESSION_ID")
            if [ -n "$ORIGINAL_TRANSCRIPT" ]; then
                trace_args+=(--source-locator "$ORIGINAL_TRANSCRIPT")
            fi
            nohup "$_py" "${PLUGIN_ROOT}/scripts/tool_trace.py" "${trace_args[@]}" </dev/null >/dev/null 2>&1 &
        else
            # Reuse the consumer's bounded failure receipt without reparsing an
            # unavailable original source or inventing another session identity.
            nohup env PYTHONPATH="${PLUGIN_ROOT}/scripts" _BL_TRACE_SOURCE="$ORIGINAL_TRANSCRIPT" \
                _BL_TRACE_CWD="$CWD" _BL_TRACE_SESSION="$SESSION_ID" "$_py" - <<'PY_RECEIPT' >/dev/null 2>&1 &
import os
from pathlib import Path
try:
    from tool_trace import _ingestion_receipt
    _ingestion_receipt(Path(os.environ["_BL_TRACE_CWD"]), Path(os.environ["_BL_TRACE_SOURCE"]),
        os.environ["_BL_TRACE_SESSION"], error=OSError("normalization unavailable"))
except (ImportError, OSError, TypeError):
    pass
PY_RECEIPT
        fi
        ;;
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
    esac
}

if [ "$SWEEP" = "all" ]; then
    for one in decisions corrections findings traces; do
        _run_one "$one"
    done
else
    _run_one "$SWEEP"
fi
wait
case "$TRANSCRIPT" in
    "$STATE_DIR"/transcript-cache/.normalized-*.lease-$$-*.jsonl) rm -f -- "$TRANSCRIPT" ;;
esac
_noop
