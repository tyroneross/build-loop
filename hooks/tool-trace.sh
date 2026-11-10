#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# Async tool-trace hook: tool-trace.sh <start|end|error>, host event on stdin.
#
# Runs bin/native/bl hook trace when it is fresh, otherwise exactly the
# command hooks/hooks.json used to inline:
#   python3 <plugin>/scripts/tool_trace.py --workdir "$CLAUDE_PROJECT_DIR" hook --phase <phase>
# Freshness (same guard as pre-edit-rally-point.sh): the binary runs only when
# bin/native/bl.sources equals line 1 of crates/SOURCES.digest, compared with
# builtins; BUILD_LOOP_NATIVE_HOOKS=off disables it. bl exits 75 (did nothing,
# read nothing) and the shell exits 126/127 (could not run it): Python runs.
case "${BASH_SOURCE[0]}" in */*) BL_ROOT="${BASH_SOURCE[0]%/*}/..";; *) BL_ROOT=..;; esac
if [ "${BUILD_LOOP_NATIVE_HOOKS:-}" != off ] \
    && [ -x "$BL_ROOT/bin/native/bl" ] \
    && { read -r BL_BUILT_DIGEST < "$BL_ROOT/bin/native/bl.sources"; } 2>/dev/null \
    && { read -r BL_TREE_DIGEST < "$BL_ROOT/crates/SOURCES.digest"; } 2>/dev/null \
    && [ -n "$BL_TREE_DIGEST" ] && [ "$BL_BUILT_DIGEST" = "$BL_TREE_DIGEST" ]; then
    "$BL_ROOT/bin/native/bl" hook trace --workdir "$CLAUDE_PROJECT_DIR" --phase "$1"
    case $? in 75|126|127) ;; *) exit 0 ;; esac
fi
exec python3 "$BL_ROOT/scripts/tool_trace.py" --workdir "$CLAUDE_PROJECT_DIR" hook --phase "$1"
