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

# Include shell/input/interpreter startup in the host deadline, not only main().
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/_bounded_hook.sh"
bl_bound_hook "${BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS:-1500}" 1500 text "$BASH" "$0" "$@"

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$SCRIPT_DIR/../scripts/rally_point"

[ -d "$PKG" ] || exit 0

# Same precedence as session-start-rally-point.sh: RALLY_TOOL_ID wins.
HOST_TOOL="${RALLY_TOOL_ID:-${BUILD_LOOP_RALLY_TOOL:-${RALLY_HOST_FAMILY:-}}}"
if [ -z "$HOST_TOOL" ]; then
    if [ -n "${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}" ]; then
        HOST_TOOL=codex
    else
        HOST_TOOL=claude_code
    fi
fi

# The PreToolUse JSON event (Edit/Write file_path, Bash command, session_id)
# is parsed inside hooks.py via --event-stdin (64 KB cap, malformed JSON = no
# hints), so this wrapper spawns one interpreter instead of two. All fields
# are optional resolution hints, not gates.

# Single call: pre_edit_hint (legacy, workdir channel) + pre_edit_join
# (throttled, operative-repo channel). Both are advisory and fail-open.
# Empty strings on the Python side are treated as "no hint" (falsy).
#
# Stderr routing (f1): quiet on the happy path; let stderr through only
# when BUILD_LOOP_RALLY_DEBUG=1 so diagnostics are available on demand
# without polluting the terminal by default.
if [ "${BUILD_LOOP_RALLY_DEBUG:-0}" = "1" ]; then
    python3 "$PKG/hooks.py" pre-edit --workdir "$WORKDIR" --tool "$HOST_TOOL" \
        --event-stdin || exit 0
else
    python3 "$PKG/hooks.py" pre-edit --workdir "$WORKDIR" --tool "$HOST_TOOL" \
        --event-stdin 2>/dev/null || exit 0
fi

exit 0
