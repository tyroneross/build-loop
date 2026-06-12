#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# closeout.sh — structural run-close shim for INLINE build-loop runs (f6).
#
# Backs the Claude Code `Stop` hook and the Codex `Stop` equivalent (see
# hooks/hooks.json + .codex/hooks.json). Thin minimal-PATH-safe wrapper around
# scripts/stop_closeout.py, which holds the (tested) logic. Mirrors the existing
# `commit_state_check.py --hook` invocation pattern.
#
#   $1 = mode: "stop" (default) | "session-start"
#
# Contract:
#   - Advisory + fail-open: ALWAYS exit 0; never `decision: block`.
#   - Self-gates on `.build-loop/` presence (walk up); silent elsewhere — safe
#     to install globally.
#   - Minimal-PATH safe: hooks run under /usr/bin:/bin. Resolve python3 via
#     `command -v` + absolute fallbacks; missing → silent exit 0.
#   - stdin (the hook JSON payload, carrying session_id) is forwarded to python
#     untouched so stop_closeout.py can read the session id.
#
# See memory `reference_hooks_minimal_path_failopen`.

set -u

MODE="${1:-stop}"

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${PWD}}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${PROJECT_DIR}}"

# Walk up from the project dir to find a `.build-loop/`. Absent → silent exit 0
# (this hook is safe to install globally; it does nothing outside a build-loop repo).
_dir="$PROJECT_DIR"
_found=""
for _ in 1 2 3 4 5 6 7 8; do
    if [ -d "${_dir}/.build-loop" ]; then
        _found="$_dir"
        break
    fi
    _parent="$(dirname "$_dir")"
    [ "$_parent" = "$_dir" ] && break
    _dir="$_parent"
done
[ -n "$_found" ] || exit 0

# Resolve a python3 binary without depending on a populated PATH (shared helper).
_HOOK_DIR="$(dirname "$0")"
_py=""
[ -f "${_HOOK_DIR}/_resolve_python.sh" ] && . "${_HOOK_DIR}/_resolve_python.sh"
[ -n "$_py" ] || exit 0

SCRIPT="${PLUGIN_ROOT}/scripts/stop_closeout.py"
[ -f "$SCRIPT" ] || exit 0

# Emit whatever the helper prints (valid hook JSON), then exit 0 no matter what.
"$_py" "$SCRIPT" --workdir "$_found" --mode "$MODE" --hook 2>/dev/null || printf '{}'
exit 0
