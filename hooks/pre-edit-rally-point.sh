#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# PreToolUse(Edit|Write|Bash) hook — Rally Point throttled per-tool-use re-join
# + cheap revision-stat hint.
#
# Contract:
#   - command-type only (NEVER prompt-type)
#   - exit 0 ALWAYS — NEVER blocks the tool use
#   - cheap: a single `revision` stat+read for the legacy hint, a single
#     presence stat+read for the throttled re-join, NO tail read, NO lock
#   - prints one short hint ONLY when the channel revision is ahead of
#     this reader's last-seen revision; silent otherwise
#   - graceful absence: no channel -> silent exit 0
#
# Codex-parity hardening (2026-06-07):
# Beyond the legacy revision hint, this hook now ALSO resolves the
# OPERATIVE repo from the tool event (Edit/Write file_path, Bash leading
# ``cd <path>``) and writes a throttled heartbeat presence in THAT repo's
# room. SessionStart misregisters (or no-ops) when the agent launches
# outside the repo it actually edits; this re-join recovers presence on
# the first tool-use without a flock on the hot path.

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$SCRIPT_DIR/../scripts/rally_point"

[ -d "$PKG" ] || exit 0

# Read stdin (Claude Code PreToolUse JSON event). Best-effort; an empty
# or malformed payload simply means "no operative-repo hint" — the legacy
# revision-stat hint still fires using the workdir's channel.
# Cap at 64 KB: a pathological Bash command must not exhaust ARG_MAX when
# the payload is forwarded to Python via the environment.
STDIN_JSON=""
if [ ! -t 0 ]; then
    STDIN_JSON=$(cat 2>/dev/null || true)
    STDIN_JSON=$(printf '%s' "$STDIN_JSON" | head -c 65536)
fi

# Extract file_path (Edit/Write) and command (Bash) from the event JSON.
# Both are optional; pre-edit treats them as resolution hints, not gates.
# Print them on two separate lines so the shell does not have to deal
# with NUL handling across portable awk/read.
FILE_PATH=""
TOOL_CMD=""
if [ -n "$STDIN_JSON" ]; then
    EXTRACTED=$(STDIN_JSON="$STDIN_JSON" python3 - <<'PYEOF' 2>/dev/null
import json, os, sys
raw = os.environ.get("STDIN_JSON") or ""
try:
    d = json.loads(raw)
except (ValueError, TypeError):
    sys.exit(0)
ti = (d.get("tool_input") or {}) if isinstance(d.get("tool_input"), dict) else {}
fp = ti.get("file_path") or ti.get("path") or ti.get("filename") or ""
cmd = ti.get("command") or ""
# Emit fp as base64 (single line, no newlines), then cmd as base64. The
# bash side decodes; this avoids embedded-newline parsing entirely.
import base64
b64 = lambda s: base64.b64encode(s.encode("utf-8", "replace")).decode("ascii") if s else ""
sys.stdout.write(b64(fp) + "\n" + b64(cmd) + "\n")
PYEOF
)
    if [ -n "$EXTRACTED" ]; then
        FILE_PATH_B64=$(printf '%s\n' "$EXTRACTED" | sed -n '1p')
        TOOL_CMD_B64=$(printf '%s\n' "$EXTRACTED" | sed -n '2p')
        if [ -n "$FILE_PATH_B64" ]; then
            FILE_PATH=$(printf '%s' "$FILE_PATH_B64" | base64 --decode 2>/dev/null || true)
        fi
        if [ -n "$TOOL_CMD_B64" ]; then
            TOOL_CMD=$(printf '%s' "$TOOL_CMD_B64" | base64 --decode 2>/dev/null || true)
        fi
    fi
fi

# Single call: pre_edit_hint (legacy, workdir channel) + pre_edit_join
# (throttled, operative-repo channel). Both are advisory and fail-open.
# Empty strings on the Python side are treated as "no hint" (falsy).
#
# Stderr routing (f1): quiet on the happy path; let stderr through only
# when BUILD_LOOP_RALLY_DEBUG=1 so diagnostics are available on demand
# without polluting the terminal by default.
if [ "${BUILD_LOOP_RALLY_DEBUG:-0}" = "1" ]; then
    python3 "$PKG/hooks.py" pre-edit --workdir "$WORKDIR" --tool claude_code \
        --file-path "$FILE_PATH" --command "$TOOL_CMD" || exit 0
else
    python3 "$PKG/hooks.py" pre-edit --workdir "$WORKDIR" --tool claude_code \
        --file-path "$FILE_PATH" --command "$TOOL_CMD" 2>/dev/null || exit 0
fi

exit 0
